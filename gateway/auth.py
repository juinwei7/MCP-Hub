"""
下游授權 —— OAuth 骨架。

分兩部分:
  1. SqliteTokenStorage / provider_for  → 已完成、可用:把 token 存 SQLite,
     並把 SDK 內建的 OAuthClientProvider 接到我們的下游連線上。
  2. ConsoleOAuthFlow                    → 骨架:管理台的互動授權流程(開瀏覽器 → callback)。
     這部分要用「真實、支援 OAuth 的遠端 MCP」才跑得完,目前假下游無法驗證,
     所以標記為 skeleton;結構就緒,拿到真服務即可收尾。

註:靜態 Bearer token 那條路不走這裡,是 hub.headers_for() 直接加 header(已驗證)。
"""

import asyncio

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientMetadata, OAuthToken, OAuthClientInformationFull

from gateway import store
from gateway.config import BASE_URL


class SqliteTokenStorage(TokenStorage):
    """把 OAuth token / client 資訊存進 SQLite 的 oauth_tokens 表(每台下游一列)。"""

    def __init__(self, slug):
        self.slug = slug

    async def get_tokens(self):
        raw = store.get_oauth_tokens(self.slug)
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens):
        store.set_oauth_tokens(self.slug, tokens.model_dump_json())

    async def get_client_info(self):
        raw = store.get_oauth_client_info(self.slug)
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info):
        store.set_oauth_client_info(self.slug, client_info.model_dump_json())


def _client_metadata():
    """本 Hub 作為 OAuth client 的自我描述(動態註冊時用)。"""
    return OAuthClientMetadata(
        client_name="MCP Hub",
        redirect_uris=[f"{BASE_URL}/oauth/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
    )


def provider_for(server, redirect_handler=None, callback_handler=None):
    """
    產生一個 OAuthClientProvider 給下游連線用。
    - 非互動情境(hub_server 轉發):不給 handler,靠已存的 token;沒 token 就會失敗(需先在管理台授權)。
    - 互動情境(管理台授權):由 ConsoleOAuthFlow 提供 redirect/callback handler。
    """
    return OAuthClientProvider(
        server_url=server["base_url"],
        client_metadata=_client_metadata(),
        storage=SqliteTokenStorage(server["slug"]),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


# ── 管理台互動授權流程(skeleton,需真服務才跑得完) ──────────────
class ConsoleOAuthFlow:
    """
    協調「一次」互動授權:
      redirect_handler:provider 算出授權網址時呼叫 → 我們把它交給 /oauth/start 去導向瀏覽器
      callback_handler:provider 需要授權碼時呼叫 → 我們等 /oauth/callback 收到 code

    簡化假設:同時只有一個進行中的授權流程(個人本機工具足夠)。
    """

    _active = None  # 目前進行中的流程(單一)

    def __init__(self, slug):
        loop = asyncio.get_running_loop()  # 只在 async 情境(管理台的授權路由)建立
        self.slug = slug
        self.auth_url = loop.create_future()   # provider → 我們:要導向的授權網址
        self.code = loop.create_future()       # /oauth/callback → 我們:拿到的 (code, state)

    async def redirect_handler(self, authorization_url):
        if not self.auth_url.done():
            self.auth_url.set_result(authorization_url)

    async def callback_handler(self):
        code, state = await self.code
        return code, state

    @classmethod
    def start(cls, slug):
        flow = cls(slug)
        cls._active = flow
        return flow

    @classmethod
    def resolve_callback(cls, code, state):
        flow = cls._active
        if flow and not flow.code.done():
            flow.code.set_result((code, state))
            return True
        return False
