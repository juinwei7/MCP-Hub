"""
下游授權 —— OAuth 2.1 + PKCE。

分兩部分:
  1. SqliteTokenStorage / provider_for  → 已完成、可用:把 token 存 SQLite,
     並把 SDK 內建的 OAuthClientProvider 接到我們的下游連線上。
  2. ConsoleOAuthFlow                    → 管理台的互動授權流程(開瀏覽器 → callback)。
     每個流程以 OAuth state 分開，可同時處理多台下游。

註:靜態 Bearer token 那條路不走這裡,是 hub.headers_for() 直接加 header(已驗證)。
"""

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken, OAuthClientInformationFull
from mcp.shared.auth_utils import calculate_token_expiry

from gateway import store
from gateway.config import BASE_URL


class SqliteTokenStorage(TokenStorage):
    """把 OAuth token / client 資訊存進 SQLite 的 oauth_tokens 表(每台下游一列)。"""

    def __init__(self, slug):
        self.slug = slug

    async def get_tokens(self):
        if not self._compatible_client_info():
            return None
        raw = store.get_oauth_tokens(self.slug)
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens):
        store.set_oauth_tokens(self.slug, tokens.model_dump_json())
        expires_at = calculate_token_expiry(tokens.expires_in)
        if expires_at is not None:
            store.set_oauth_expiry(self.slug, expires_at)

    async def get_client_info(self):
        return self._compatible_client_info()

    async def set_client_info(self, client_info):
        store.set_oauth_client_info(self.slug, client_info.model_dump_json())

    def _compatible_client_info(self):
        raw = store.get_oauth_client_info(self.slug)
        if not raw:
            return None
        try:
            client_info = OAuthClientInformationFull.model_validate_json(raw)
        except Exception:
            store.clear_oauth(self.slug)
            return None
        if client_info.token_endpoint_auth_method not in (None, "none"):
            store.clear_oauth(self.slug)
            return None
        return client_info


def _client_metadata():
    """本 Hub 作為 OAuth client 的自我描述(動態註冊時用)。"""
    return OAuthClientMetadata(
        client_name="MCP Hub",
        redirect_uris=[f"{BASE_URL}/oauth/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


async def _discover_oauth_metadata(server_url):
    """
    Best-effort 做一次 PRM + ASM discovery(跟 SDK 的 async_auth_flow 401 分支的
    Step 1/2 同一套邏輯,搬出 generator 自己跑),失敗就回 None,不擋住主流程。
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            prm = None
            for url in build_protected_resource_metadata_discovery_urls(None, server_url):
                response = await client.send(create_oauth_metadata_request(url))
                prm = await handle_protected_resource_response(response)
                if prm:
                    break

            auth_server_url = None
            if prm and prm.authorization_servers:
                auth_server_url = str(prm.authorization_servers[0])

            for url in build_oauth_authorization_server_metadata_discovery_urls(
                    auth_server_url, server_url):
                response = await client.send(create_oauth_metadata_request(url))
                ok, asm = await handle_auth_metadata_response(response)
                if asm:
                    return asm
                if not ok:
                    break
    except Exception:
        return None
    return None


async def provider_for(server, redirect_handler=None, callback_handler=None):
    """
    產生一個 OAuthClientProvider 給下游連線用。
    - 非互動情境(hub_server 轉發):不給 handler,靠已存的 token;沒 token 就會失敗(需先在管理台授權)。
    - 互動情境(管理台授權):由 ConsoleOAuthFlow 提供 redirect/callback handler。

    SDK 重建 provider 時只會從 storage 載入 token 本身,不會載入到期時間或
    Authorization Server metadata,導致 refresh 永遠不會被觸發、或觸發了打錯 token
    endpoint。這裡在建好後手動把之前存的這兩項補回 context,讓 refresh 真的能運作。
    """
    slug = server["slug"]
    provider = OAuthClientProvider(
        server_url=server["base_url"],
        client_metadata=_client_metadata(),
        storage=SqliteTokenStorage(slug),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    await provider._initialize()

    expires_at = store.get_oauth_expiry(slug)
    if expires_at is not None:
        provider.context.token_expiry_time = expires_at

    cached_metadata = store.get_oauth_metadata(slug)
    if cached_metadata:
        try:
            provider.context.oauth_metadata = OAuthMetadata.model_validate_json(cached_metadata)
        except Exception:
            cached_metadata = None
    if not cached_metadata and provider.context.current_tokens:
        metadata = await _discover_oauth_metadata(server["base_url"])
        if metadata:
            provider.context.oauth_metadata = metadata
            store.set_oauth_metadata(slug, metadata.model_dump_json())

    return provider


# ── 管理台互動授權流程(skeleton,需真服務才跑得完) ──────────────
class ConsoleOAuthFlow:
    """
    協調「一次」互動授權:
      redirect_handler:provider 算出授權網址時呼叫 → 我們把它交給 /oauth/start 去導向瀏覽器
      callback_handler:provider 需要授權碼時呼叫 → 我們等 /oauth/callback 收到 code

    每個流程以 OAuth state 分開管理，可以同時授權不同下游。
    """

    _by_state = {}

    def __init__(self, slug):
        loop = asyncio.get_running_loop()  # 只在 async 情境(管理台的授權路由)建立
        self.slug = slug
        self.state = None
        self.auth_url = loop.create_future()   # provider → 我們:要導向的授權網址
        self.code = loop.create_future()       # /oauth/callback → 我們:拿到的 (code, state)

    async def redirect_handler(self, authorization_url):
        states = parse_qs(urlparse(str(authorization_url)).query).get("state", [])
        if len(states) != 1 or not states[0]:
            raise ValueError("OAuth authorization URL is missing state")
        self.state = states[0]
        self._by_state[self.state] = self
        if not self.auth_url.done():
            self.auth_url.set_result(authorization_url)

    async def callback_handler(self):
        code, state = await self.code
        return code, state

    @classmethod
    def start(cls, slug):
        return cls(slug)

    @classmethod
    async def resolve_callback(cls, code, state, error=""):
        flow = cls._by_state.get(state)
        if flow and not flow.code.done():
            if error:
                flow.code.set_exception(RuntimeError(error))
            else:
                flow.code.set_result((code, state))
            return True
        return False

    def close(self):
        if self.state and self._by_state.get(self.state) is self:
            self._by_state.pop(self.state, None)
        if not self.auth_url.done():
            self.auth_url.cancel()
        if not self.code.done():
            self.code.cancel()
