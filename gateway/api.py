"""
JSON API —— 給原生 client(macOS / Windows app)用的介面。

職責只有三件事:驗證請求、把 HTTP 參數轉成既有函式的呼叫、把結果序列化成 JSON。
**不含任何業務邏輯** —— 一律呼叫 store / hub / auth 既有的函式,與網頁管理台走同一條路。
設計決策見本檔各段註解。

與網頁管理台掛在同一個 FastAPI app、同一個 port,原因有二:
  1. OAuth 的 redirect_uri 綁在 8765,而 callback 由 web.py 提供;拆成兩個程序會把
     授權流程切開(ConsoleOAuthFlow 的 state 表是行程內的字典,跨程序找不到)。
  2. 原生 app 只需監督一個子程序。

沒設 MCP_HUB_API_TOKEN 就等於不開放 API(回 503),但網頁管理台不受影響。
"""

import asyncio
import hmac
import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from gateway import store, hub, auth
from gateway.config import API_TOKEN, DB_PATH


def _fail(status, code, message):
    """統一的錯誤形狀:code 給程式判斷(穩定),message 給人看(可變動)。"""
    raise HTTPException(status_code=status, detail={"error": code, "message": message})


def install_error_handler(app):
    """
    把 API 的錯誤攤平成 {"error", "message"},不要 FastAPI 預設的 {"detail": {...}} 巢狀。

    只處理 detail 是我們自己那個形狀的情況,其餘一律交回預設行為 —— 不能影響
    網頁管理台既有的錯誤處理。
    """
    from fastapi.exception_handlers import http_exception_handler
    from fastapi.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def _flatten(request, exc):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return await http_exception_handler(request, exc)


async def require_token(request: Request):
    """
    驗證每個 API 請求。

    既有管理台沒有登入,只靠綁定 127.0.0.1 —— 那在「只有瀏覽器會連」的前提下勉強成立,
    但 API 的呼叫者是程式,同機任何程序都打得到。這裡不延續那個弱點。
    """
    if not API_TOKEN:
        _fail(503, "api_disabled", "未設定 MCP_HUB_API_TOKEN,API 未開放。")
    supplied = request.headers.get("authorization", "")
    prefix = "bearer "
    if not supplied.lower().startswith(prefix):
        _fail(401, "unauthorized", "缺少 Authorization: Bearer <token>。")
    # 常數時間比對:計時側通道是零成本就能避免的問題。
    if not hmac.compare_digest(supplied[len(prefix):], API_TOKEN):
        _fail(401, "unauthorized", "token 不正確。")


# 兩個 router:health 免驗證(app 要靠它判斷子程序起來沒),其餘一律驗證。
# 由 web.py 分別掛上,不互相巢狀 —— 巢狀會讓前綴變成 /api/v1/api/v1。
router = APIRouter(prefix="/api/v1")
guarded = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


# ── 啟動前檢查 ────────────────────────────────────────────
OAUTH_PORT = 8765   # auth.py 把 {BASE_URL}/oauth/callback 註冊成 OAuth redirect_uri


def preflight(host, port):
    """
    啟動前檢查 port,回傳 (ok, message)。

    port 不能隨便換:授權伺服器會把 client_id 綁在註冊時的 redirect_uri 上,而那個
    URL 是從 port 推導出來的。換 port 等於所有 OAuth 下游的動態註冊全部失效,
    使用者每次開 app 都要重新授權一次 —— 所以寧可明確失敗,也不要默默改用別的 port。
    """
    import socket

    if port != OAUTH_PORT:
        return True, (f"警告:目前使用 port {port},非預設的 {OAUTH_PORT}。"
                      f"OAuth 下游的 redirect_uri 綁在 port 上,換 port 會讓既有授權失效,"
                      f"需要重新授權。")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # 必須跟 uvicorn 一樣設 SO_REUSEADDR。少了它,剛重啟過的 port 會因為舊 socket
        # 還在 TIME_WAIT 而綁不上 —— 檢查比實際伺服器更嚴格,就會擋掉本來能啟動的情況。
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as e:
            return False, (f"port {port} 無法綁定({e})。可能已有另一個 MCP Hub 在執行,"
                           f"或被其他程式佔用。請先關閉它 —— 這裡不會自動改用其他 port,"
                           f"因為那會讓 OAuth 授權失效。")
    return True, ""


# ── 序列化 ────────────────────────────────────────────────
# store 回傳的 server dict 帶著「已解密的」bearer_token 與 env,
# 直接序列化就會把密鑰送出去。一律白名單輸出,只回報有無設定。
def _server_out(row, tool_count=None):
    out = {
        "slug": row["slug"],
        "name": row["name"],
        "base_url": row["base_url"],
        "transport": row.get("transport") or "http",
        "auth_type": row["auth_type"],
        "enabled": bool(row["enabled"]),
        "status": row.get("status") or "unknown",
        "status_detail": row.get("status_detail") or "",
        "checked_at": row.get("checked_at") or "",
        "created_at": row["created_at"],
        "command": row.get("command") or "",
        "args": _loads(row.get("args"), []),
        "has_token": bool(row.get("bearer_token")),
        "has_env": bool(_loads(row.get("env"), {})),
    }
    if tool_count is not None:
        out["tool_count"] = tool_count
    return out


def _tool_out(row):
    return {
        "name": row["name"],
        "description": row["description"] or "",
        "desc_override": row.get("desc_override") or "",
        "input_schema": _loads(row.get("input_schema"), None),
        "enabled": bool(row["enabled"]),
        "needs_confirm": bool(row["needs_confirm"]),
    }


def _log_out(row):
    return {
        "id": row["id"],
        "time": row["time"],
        "server_slug": row["server_slug"] or "",
        "tool": row["tool"] or "",
        "arguments": _loads(row.get("arguments"), None),
        "status": row["status"],
        "duration_ms": row["duration_ms"],
        "error": row["error"] or "",
    }


def _action_out(row):
    # list_actions 的 result 是原始 JSON 字串,get_action 的已解析 —— 這裡統一。
    result = row.get("result")
    return {
        "id": row["id"],
        "tool": row["tool"],
        "arguments": row["arguments"] if isinstance(row["arguments"], (dict, list)) else _loads(row["arguments"], None),
        "status": row["status"],
        "result": result if isinstance(result, (dict, list)) else _loads(result, None),
        "created_at": row["created_at"],
        "decided_at": row.get("decided_at") or "",
    }


def _loads(raw, fallback):
    if not raw:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _get_server_or_404(slug):
    srv = store.get_server(slug)
    if srv is None:
        _fail(404, "server_not_found", f"找不到下游 {slug}。")
    return srv


def _json_field(raw, kind, field):
    """stdio 的 args / env 必須是合法 JSON,且型別要對。錯了就明講,不要默默吞掉。"""
    if raw is None:
        return None
    if isinstance(raw, kind):
        return json.dumps(raw, ensure_ascii=False)
    _fail(400, "invalid_json", f"{field} 必須是 {'陣列' if kind is list else '物件'}。")


# ── 健康檢查(免驗證)────────────────────────────────────
# app 要靠這個判斷子程序何時可以接受請求,而那時它還不確定 token 是否被接受。
# 回應刻意不含任何敏感資訊。
@router.get("/health")
def health():
    return {
        "ready": True,
        "db_path": str(DB_PATH),
        "api_enabled": bool(API_TOKEN),
    }


# ── 總開關 ────────────────────────────────────────────────
# 暫停是 Hub 層級的,不動每台下游的啟用狀態 —— 恢復時才知道本來哪幾台是開的。
@guarded.get("/paused")
def get_paused():
    return {"paused": store.is_paused()}


@guarded.put("/paused")
def put_paused(body: dict = Body(...)):
    paused = bool(body.get("paused"))
    store.set_paused(paused)
    publish("paused_changed", {"paused": paused})
    return {"paused": paused}


# ── 下游 ──────────────────────────────────────────────────
@guarded.get("/servers")
def list_servers():
    return [_server_out(s, store.count_tools(s["slug"])) for s in store.list_servers()]


# 字面路徑要宣告在 /servers/{slug} 之前,避免被當成 slug 吃掉。
@guarded.post("/servers/check-all")
async def check_all():
    servers = [s for s in store.list_servers() if s["enabled"]]

    async def one(srv):
        status, detail, tools = await hub.check_server(srv)
        store.set_server_status(srv["slug"], status, detail)
        if tools is not None:
            store.cache_tools(srv["slug"], [(t.name, t.description, t.inputSchema) for t in tools])
        publish("server_status", {"slug": srv["slug"], "status": status, "detail": detail})
        return status == "ok"

    results = await asyncio.gather(*(one(s) for s in servers), return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    return {"checked": len(servers), "ok": ok, "failed": len(servers) - ok}


@guarded.post("/servers", status_code=201)
async def add_server(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        _fail(400, "invalid_request", "name 不可為空。")

    base = store.slugify(payload.get("slug") or name)
    existing = {s["slug"] for s in store.list_servers()}
    if payload.get("slug") and base in existing:
        _fail(409, "slug_conflict", f"slug {base} 已存在。")
    slug, i = base, 2
    while slug in existing:
        slug, i = f"{base}_{i}", i + 1

    transport = payload.get("transport") or "http"
    args_json = _json_field(payload.get("args"), list, "args") or "[]"
    env_json = _json_field(payload.get("env"), dict, "env") or "{}"

    store.add_server(
        slug, name, (payload.get("base_url") or "").strip(),
        payload.get("auth_type") or "none", payload.get("bearer_token") or None,
        transport, (payload.get("command") or "").strip(), args_json, env_json,
    )
    await _probe(slug)
    return _server_out(store.get_server(slug), store.count_tools(slug))


@guarded.get("/servers/{slug}")
def get_server(slug: str):
    srv = _get_server_or_404(slug)
    out = _server_out(srv, store.count_tools(slug))
    out["tools"] = [_tool_out(t) for t in store.list_cached_tools(slug)]
    return out


@guarded.patch("/servers/{slug}")
async def update_server(slug: str, payload: dict):
    srv = _get_server_or_404(slug)

    def pick(key, current):
        return current if key not in payload else payload[key]

    # bearer_token 省略代表「不動」,不是「清空」—— 編輯表單不該意外清掉密鑰。
    token = srv["bearer_token"] if "bearer_token" not in payload else (payload["bearer_token"] or None)
    args_json = _json_field(payload.get("args"), list, "args") or (srv.get("args") or "[]")
    env_json = _json_field(payload.get("env"), dict, "env") or (srv.get("env") or "{}")

    store.update_server(
        slug, pick("name", srv["name"]), (pick("base_url", srv["base_url"]) or "").strip(),
        pick("auth_type", srv["auth_type"]), token,
        pick("transport", srv.get("transport") or "http"),
        (pick("command", srv.get("command") or "") or "").strip(), args_json, env_json,
    )
    await _probe(slug)
    return _server_out(store.get_server(slug), store.count_tools(slug))


@guarded.delete("/servers/{slug}", status_code=204)
def delete_server(slug: str):
    _get_server_or_404(slug)
    store.delete_server(slug)
    publish("server_changed", {"slug": slug, "deleted": True})


@guarded.patch("/servers/{slug}/enabled")
def set_enabled(slug: str, payload: dict):
    _get_server_or_404(slug)
    if "enabled" not in payload:
        _fail(400, "invalid_request", "必須提供 enabled。")
    # 明確值而非 toggle:兩個 client 同時 toggle 會互相抵銷。
    store.set_server_enabled(slug, bool(payload["enabled"]))
    publish("server_changed", {"slug": slug})
    return _server_out(store.get_server(slug), store.count_tools(slug))


@guarded.post("/servers/{slug}/check")
async def check_server(slug: str):
    srv = _get_server_or_404(slug)
    status, detail, tools = await hub.check_server(srv)
    store.set_server_status(slug, status, detail)
    if tools is not None:
        store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
    publish("server_status", {"slug": slug, "status": status, "detail": detail})
    return {"status": status, "detail": detail, "tool_count": store.count_tools(slug)}


@guarded.post("/servers/{slug}/refresh")
async def refresh_server(slug: str):
    _get_server_or_404(slug)
    ok, detail = await _probe(slug)
    if not ok:
        _fail(502, "downstream_error", detail)
    return {"tool_count": store.count_tools(slug), "detail": detail}


async def _probe(slug):
    """連一次下游、更新工具快取與狀態。新增 / 編輯 / 重抓共用。"""
    srv = store.get_server(slug)
    try:
        tools = await hub.fetch_tools(srv)
        store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
        detail = f"{len(tools)} 個工具"
        store.set_server_status(slug, "ok", detail)
        publish("server_status", {"slug": slug, "status": "ok", "detail": detail})
        return True, detail
    except Exception as e:
        detail = str(e)[:200]
        store.set_server_status(slug, "error", detail)
        publish("server_status", {"slug": slug, "status": "error", "detail": detail})
        return False, detail


# ── 下游工具 ──────────────────────────────────────────────
@guarded.get("/servers/{slug}/tools")
def list_tools(slug: str):
    _get_server_or_404(slug)
    return [_tool_out(t) for t in store.list_cached_tools(slug)]


@guarded.patch("/servers/{slug}/tools")
def set_all_tools(slug: str, payload: dict):
    _get_server_or_404(slug)
    if "enabled" not in payload:
        _fail(400, "invalid_request", "必須提供 enabled。")
    store.set_all_tools_enabled(slug, bool(payload["enabled"]))
    return [_tool_out(t) for t in store.list_cached_tools(slug)]


@guarded.patch("/servers/{slug}/tools/{tool}")
def set_tool(slug: str, tool: str, payload: dict):
    _get_server_or_404(slug)
    if store.get_tool(slug, tool) is None:
        _fail(404, "tool_not_found", f"{slug} 沒有工具 {tool}。")
    if "enabled" in payload or "needs_confirm" in payload:
        store.set_tool_flag(
            slug, tool,
            enabled=None if "enabled" not in payload else bool(payload["enabled"]),
            needs_confirm=None if "needs_confirm" not in payload else bool(payload["needs_confirm"]),
        )
    if "desc_override" in payload:
        # 空字串代表還原成下游原始描述,這是既有語意。
        store.set_tool_desc(slug, tool, (payload["desc_override"] or "").strip())
    return _tool_out(store.get_tool(slug, tool))


# ── 呼叫記錄 ──────────────────────────────────────────────
@guarded.get("/logs")
def list_logs(page: int = 1, per_page: int = 50, errors_only: bool = False):
    per_page = max(1, min(per_page, 200))
    total = store.count_logs(errors_only=errors_only)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    rows = store.list_logs(per_page, (page - 1) * per_page, errors_only=errors_only)
    return {
        "rows": [_log_out(r) for r in rows],
        "page": page, "pages": pages, "per_page": per_page,
        "total": total, "errors": store.count_logs(errors_only=True),
    }


# ── 人工確認 ──────────────────────────────────────────────
@guarded.get("/actions")
def list_actions(status: str = ""):
    rows = store.list_actions()
    if status:
        rows = [r for r in rows if r["status"] == status]
    return [_action_out(r) for r in rows]


@guarded.get("/actions/{action_id}")
def get_action(action_id: str):
    a = store.get_action(action_id)
    if a is None:
        _fail(404, "action_not_found", f"找不到票券 {action_id}。")
    return _action_out(a)


@guarded.post("/actions/{action_id}/decision")
def decide_action(action_id: str, payload: dict):
    """
    只把票券從 WAITING 移到 APPROVED / REJECTED,不執行工具。

    實際執行發生在另一個程序(hub_server._resume),由 agent 呼叫 check_action 觸發。
    在這裡執行會是新行為,不是鏡像 —— 邊界要守住。
    """
    a = store.get_action(action_id)
    if a is None:
        _fail(404, "action_not_found", f"找不到票券 {action_id}。")
    if "approve" not in payload:
        _fail(400, "invalid_request", "必須提供 approve。")
    if a["status"] != store.WAITING:
        # store.decide 帶 AND status='WAITING' 但不回報 rowcount,所以先自己判斷。
        _fail(409, "action_not_waiting", f"票券狀態是 {a['status']},無法再次決定。")
    store.decide(action_id, approve=bool(payload["approve"]))
    publish("action_decided", {"id": action_id})
    return _action_out(store.get_action(action_id))


# ── 即時推送 ──────────────────────────────────────────────
# 行程內廣播,不輪詢 DB。用 SSE 而非 WebSocket:事件是單向的,兩端都簡單得多,
# 而且自動重連是協定內建的。
#
# 已知限制:hub_server 是獨立程序,它寫入的呼叫記錄不會觸發這裡的廣播。
# client 需要時自行重新查詢補足。
_subscribers = set()


def publish(event, data=None):
    """對所有訂閱者送一個事件。滿了就丟棄 —— 慢的 client 不該拖住健檢迴圈。"""
    for q in list(_subscribers):
        try:
            q.put_nowait((event, data or {}))
        except asyncio.QueueFull:
            pass


@guarded.get("/events")
async def events():
    from fastapi.responses import StreamingResponse

    q = asyncio.Queue(maxsize=100)
    _subscribers.add(q)

    async def stream():
        try:
            yield _sse("ready", {"subscribers": len(_subscribers)})
            while True:
                event, data = await q.get()
                yield _sse(event, data)
        finally:
            _subscribers.discard(q)   # 斷線一定要清掉,否則連線會累積

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── OAuth ─────────────────────────────────────────────────
@guarded.post("/servers/{slug}/oauth/start")
async def oauth_start(slug: str):
    """
    回傳授權網址,由 client 以系統授權視窗開啟(既有 HTML 路由是直接轉址)。

    callback 仍由 web.py 的 /oauth/callback 接收,靠 ConsoleOAuthFlow 行程內的
    state 表找回這個流程 —— 這也是 API 必須與管理台同程序的原因。
    """
    srv = _get_server_or_404(slug)
    if (srv.get("transport") or "http") != "http":
        _fail(400, "unsupported_transport", "只有 http 下游支援 OAuth 授權。")

    flow = auth.ConsoleOAuthFlow.start(slug)
    provider = await auth.provider_for(srv, flow.redirect_handler, flow.callback_handler)
    err_box = {}

    async def run():
        try:
            async with hub._http_session(srv["base_url"], auth=provider) as s:
                await s.list_tools()
        except Exception as e:
            err_box["err"] = e
        finally:
            auth.persist_discovered_metadata(slug, provider)
            flow.close()

    task = asyncio.create_task(run())
    url_fut = flow.auth_url
    await asyncio.wait({url_fut, task}, timeout=15, return_when=asyncio.FIRST_COMPLETED)
    if url_fut.done() and not url_fut.cancelled() and url_fut.exception() is None:
        # 背景任務繼續等 callback,不能取消。
        return {"authorization_url": str(url_fut.result())}

    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    flow.close()
    from gateway.web import _oauth_error_hint   # 延遲匯入避免循環
    _fail(502, "oauth_start_failed", _oauth_error_hint(err_box.get("err")))
