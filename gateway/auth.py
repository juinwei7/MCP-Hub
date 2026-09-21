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
import time
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
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from mcp.shared.auth_utils import (
    calculate_token_expiry,
    check_resource_allowed,
    resource_url_from_server_url,
)

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
        # token 與到期時間同一筆寫入:分兩次寫會留下「有到期時間、沒 token」的殘缺列,
        # 而 expires_in 缺席時(RFC 6749 允許)得把舊值清成 NULL,不能沿用上一顆 token 的。
        store.set_oauth_tokens(
            self.slug, tokens.model_dump_json(), calculate_token_expiry(tokens.expires_in))

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


DISCOVERY_TIMEOUT = 15        # 整段 discovery 的上限(連線池在全域鎖裡等它,不能沒有天花板)
DISCOVERY_RETRY_AFTER = 300   # 探查失敗後隔多久才願意再試一次
_discovery_failed_at = {}     # slug → 上次探查失敗的 monotonic 時間


def _resource_matches(prm, server_url):
    """RFC 8707:PRM 宣告的 resource 必須涵蓋這台下游,否則不採信它指的 AS。"""
    if not prm.resource:
        return True
    return check_resource_allowed(
        requested_resource=resource_url_from_server_url(server_url),
        configured_resource=str(prm.resource),
    )


async def _discover_oauth_metadata(server_url):
    """
    Best-effort 做一次 PRM + ASM discovery(跟 SDK 的 async_auth_flow 401 分支的
    Step 1/2 同一套邏輯,搬出 generator 自己跑),回傳 (prm, asm),失敗就回 (None, None)。

    follow_redirects 要跟 SDK 的 transport(create_mcp_http_client)一致 —— 少了它,
    well-known 端點只要回 301/302 這裡就判定失敗,而 SDK 自己跑卻會成功。
    """
    prm = asm = None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for url in build_protected_resource_metadata_discovery_urls(None, server_url):
                response = await client.send(create_oauth_metadata_request(url))
                prm = await handle_protected_resource_response(response)
                if prm:
                    break

            if prm and not _resource_matches(prm, server_url):
                return None, None   # 不是這台下游的 PRM,它指的 token endpoint 也別存

            auth_server_url = None
            if prm and prm.authorization_servers:
                auth_server_url = str(prm.authorization_servers[0])

            for url in build_oauth_authorization_server_metadata_discovery_urls(
                    auth_server_url, server_url):
                response = await client.send(create_oauth_metadata_request(url))
                ok, asm = await handle_auth_metadata_response(response)
                if asm or not ok:
                    break
    except Exception:
        return None, None
    return prm, asm


def persist_discovered_metadata(slug, provider):
    """把 context 上的探查結果存起來,供之後重建的 provider 直接用(refresh 才打得對)。"""
    ctx = provider.context
    if not ctx.oauth_metadata and not ctx.protected_resource_metadata:
        return
    store.set_oauth_metadata(
        slug,
        ctx.oauth_metadata.model_dump_json() if ctx.oauth_metadata else None,
        ctx.protected_resource_metadata.model_dump_json()
        if ctx.protected_resource_metadata else None,
    )


def _restore_cached_metadata(provider, slug):
    """把存下的 PRM / ASM 放回 context。壞掉的快取當作沒有,交給後面重新探查覆寫。"""
    for raw, model, field in (
        (store.get_oauth_metadata(slug), OAuthMetadata, "oauth_metadata"),
        (store.get_oauth_resource_metadata(slug), ProtectedResourceMetadata,
         "protected_resource_metadata"),
    ):
        if not raw:
            continue
        try:
            setattr(provider.context, field, model.model_validate_json(raw))
        except Exception:
            pass
    prm = provider.context.protected_resource_metadata
    if prm and prm.authorization_servers:
        provider.context.auth_server_url = str(prm.authorization_servers[0])


async def provider_for(server, redirect_handler=None, callback_handler=None):
    """
    產生一個 OAuthClientProvider 給下游連線用。
    - 非互動情境(hub_server 轉發):不給 handler,靠已存的 token;沒 token 就會失敗(需先在管理台授權)。
    - 互動情境(管理台授權):由 ConsoleOAuthFlow 提供 redirect/callback handler。

    SDK 重建 provider 時只會從 storage 載入 token 本身,不會載入到期時間或探查文件,
    導致 refresh 永遠不會被觸發、或觸發了打錯 token endpoint。這裡在建好後手動把之前
    存的補回 context,讓 refresh 真的能運作。
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

    # 互動授權不套用快取:SDK 的 401 分支本來就會重新探查,這裡先塞一份過期/錯誤的
    # endpoint 反而會讓使用者連「重新授權」這條救命路都走不通。
    if redirect_handler or callback_handler:
        return provider

    _restore_cached_metadata(provider, slug)
    # refresh 真正需要的是 ASM 裡的 token_endpoint;只有它缺席才值得付探查的網路成本。
    if not provider.context.oauth_metadata and provider.context.current_tokens:
        await _discover_and_cache(provider, slug, server["base_url"])

    return provider


async def _discover_and_cache(provider, slug, server_url):
    """沒有快取時才探查一次。全程有上限,失敗會退避,不讓一台下游拖垮整個連線池。"""
    failed_at = _discovery_failed_at.get(slug)
    if failed_at is not None and time.monotonic() - failed_at < DISCOVERY_RETRY_AFTER:
        return
    try:
        prm, asm = await asyncio.wait_for(
            _discover_oauth_metadata(server_url), DISCOVERY_TIMEOUT)
    except Exception:   # 含 wait_for 逾時;探查失敗不該擋住連線
        prm = asm = None
    if not prm and not asm:
        _discovery_failed_at[slug] = time.monotonic()
        return
    _discovery_failed_at.pop(slug, None)
    provider.context.oauth_metadata = asm
    provider.context.protected_resource_metadata = prm
    if prm and prm.authorization_servers:
        provider.context.auth_server_url = str(prm.authorization_servers[0])
    persist_discovered_metadata(slug, provider)


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
