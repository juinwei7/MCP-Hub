"""
後端 —— 給原生 app 用的 JSON API,以及背景健康檢查。

這個模組曾經是一整套網頁管理台(66 條 HTML 路由、25 個 Jinja2 模板)。
原生 app 補完所有功能之後,那些被移除了。現在這裡只剩三件事:

  1. FastAPI 的 app 物件 —— JSON API 掛在它上面
  2. 背景健康檢查迴圈
  3. /oauth/callback —— 唯一還需要回 HTML 的端點,因為授權伺服器導回的是
     瀏覽器而不是程式

啟動:
    ./.venv/bin/python -m gateway.web   → http://localhost:8765
"""

import sys
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from gateway import store, hub, auth
from gateway.config import HOST, PORT, HEALTH_INTERVAL


async def _health_loop():
    """背景:定期檢查每台啟用的下游,更新狀態並刷新工具快取。"""
    while True:
        try:
            for srv in store.enabled_servers():
                status, detail, tools = await hub.check_server(srv)
                store.set_server_status(srv["slug"], status, detail)
                if tools is not None:
                    store.cache_tools(srv["slug"], [(t.name, t.description, t.inputSchema) for t in tools])
                _api.publish("server_status",
                             {"slug": srv["slug"], "status": status, "detail": detail})
        except Exception:
            pass
        await asyncio.sleep(HEALTH_INTERVAL)


@asynccontextmanager
async def lifespan(app):
    store.init_db()
    store.backfill_github_category()  # 把之前誤匯入的 GitHub 工具歸到「GitHub」分類
    task = asyncio.create_task(_health_loop())
    yield
    task.cancel()


app = FastAPI(title="MCP Hub 管理台", lifespan=lifespan)

# 給原生 client 用的 JSON API。掛在同一個 app 同一個 port,
# 因為 OAuth 的 redirect_uri 綁在這裡,且 ConsoleOAuthFlow 的 state 表是行程內的。
from gateway import api as _api   # noqa: E402  放在 app 之後,因為 api 需要 web 的錯誤解讀
app.include_router(_api.router)
app.include_router(_api.guarded)
from gateway import api_tools as _api_tools, api_import as _api_import   # noqa: E402
app.include_router(_api_tools.router)
app.include_router(_api_import.router)
_api.install_error_handler(app)


def _exception_details(err):
    # 實作已移到 hub.py —— 錯誤是在那裡產生的,健檢與管理台要用同一套解讀。
    return hub.exception_details(err)


def _oauth_error_hint(err):
    """把 TaskGroup / ExceptionGroup 解包，顯示真正的 OAuth 原因。"""
    details = _exception_details(err)
    lowered = details.lower()
    if "429" in lowered or "too_many_requests" in lowered:
        return f"OAuth client 註冊頻率過高，請稍後重試。技術細節：{details}"
    if "invalid_client_metadata" in lowered:
        return f"OAuth client metadata 被授權服務拒絕。技術細節：{details}"
    if "registration failed" in lowered:
        return f"Dynamic Client Registration 失敗。技術細節：{details}"
    return f"OAuth 授權初始化失敗。技術細節：{details or '未知錯誤'}"


# ── OAuth callback ────────────────────────────────────────
# 這是唯一還需要回 HTML 的端點:授權伺服器導回的是**瀏覽器**,不是程式。
# 內容只需要告訴使用者「可以關掉這個分頁了」,不值得為它留一整套模板引擎。
_CALLBACK_PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>MCP Hub — OAuth</title>
<style>
  body {{ font: 15px/1.7 -apple-system, "PingFang TC", system-ui, sans-serif;
         background: #1c1c1e; color: #f2f2f7; display: grid; place-items: center;
         min-height: 100vh; margin: 0; padding: 24px; }}
  .card {{ max-width: 460px; text-align: center; }}
  h1 {{ font-size: 19px; margin: 0 0 8px; }}
  p {{ color: #a1a1a6; margin: 0; }}
</style></head>
<body><div class="card"><h1>{heading}</h1><p>{message}</p></div></body></html>"""


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(code: str = "", state: str = "",
                         error: str = "", error_description: str = ""):
    callback_error = error_description or error
    ok = await auth.ConsoleOAuthFlow.resolve_callback(code, state, callback_error)
    if error:
        message = f"授權服務未完成授權:{error_description or error}"
    elif ok:
        message = "已收到授權回呼,Hub 正在交換 token。可以關掉這個分頁,回到 app 重新整理工具。"
    else:
        message = "找不到對應的授權流程,可能已逾時或 state 不符。請回到 app 重新開始授權。"
    return HTMLResponse(_CALLBACK_PAGE.format(heading="OAuth 回呼", message=message))


if __name__ == "__main__":
    import uvicorn
    from gateway import console
    console.use_utf8()   # Windows 主控台預設編碼編不出中文
    ok, note = _api.preflight(HOST, PORT)
    if note:
        console.eprint(note)
    if not ok:
        sys.exit(1)
    uvicorn.run(app, host=HOST, port=PORT)
