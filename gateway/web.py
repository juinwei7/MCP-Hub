"""
管理台 —— 用瀏覽器管理你的 MCP Hub。

頁面:
  /                      Servers:下游清單、開關、工具數、狀態
  /servers/add           新增下游(name / URL / 授權)
  /servers/{slug}        詳情:工具清單 + 每個工具的「啟用 / 需確認」開關 + 重新整理
  /logs                  呼叫紀錄
  /a/{id}                核准頁(v1,給「可選確認」用)

啟動:
    ./.venv/bin/python -m gateway.web   → http://localhost:8765
"""

import os
import sys
import json
import asyncio
import subprocess
import html as _h
import urllib.parse as _u
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from gateway import store, hub, auth, custom, openapi, directory, composite, skills
from gateway.config import HOST, PORT, HEALTH_INTERVAL
from gateway.dispatch import blocks_to_texts, execute_named_tool

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
_api.install_error_handler(app)


def _tool_name_conflict(name, *, allow_existing=None):
    if name in {"check_action", "skill_workbench"}:
        return f"工具名稱不能使用保留字 {name}"
    if "__" in name:
        return "工具名稱不能包含 __，避免與下游 slug__tool 命名空間衝突"
    existing = store.get_custom_tool(name) or store.get_composite_tool(name)
    if existing and name != allow_existing:
        return f"名稱 {name} 已存在"
    for s in store.list_servers():
        if store.get_tool(s["slug"], name):
            return f"名稱 {name} 與既有下游工具衝突"
    return ""


# ── Servers 清單 ──────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request, msg: str = ""):
    health_map = {"ok": ("ok", "健康"), "error": ("error", "異常"), "unknown": ("off", "未檢查")}
    servers = []
    for s in store.list_servers():
        transport = s.get("transport", "http")
        hcls, hlabel = health_map.get(s.get("status", "unknown"), ("off", "未檢查"))
        enabled = bool(s["enabled"])
        servers.append({
            "slug": s["slug"], "name": s["name"], "transport": transport,
            "target": (s.get("command") or "") if transport == "stdio" else s["base_url"],
            "hlabel": hlabel, "hdetail": s.get("status_detail", "") or "",
            # 停用中就不算健康,燈號轉灰;否則反映實際健康狀態
            "dot": "off" if not enabled else hcls,
            "tool_count": store.count_tools(s["slug"]),
            "enabled": enabled,
            "toggle_label": "停用" if enabled else "啟用",
            "status": s.get("status", "unknown"),
        })
    stats = {
        "servers": len(servers),
        "tools": sum(s["tool_count"] for s in servers),
        "healthy": sum(1 for s in servers if s["enabled"] and s["status"] == "ok"),
        "errored": sum(1 for s in servers if s["enabled"] and s["status"] == "error"),
    }
    return templates.TemplateResponse(request, "servers.html",
                                      {"active": "servers", "servers": servers, "msg": msg, "stats": stats})


# ── 新增下游 ──────────────────────────────────────────────
@app.get("/servers/add", response_class=HTMLResponse)
def add_form(request: Request, err: str = ""):
    return templates.TemplateResponse(request, "server_add.html",
                                      {"active": "servers", "err": err, "py_path": sys.executable})


@app.post("/servers/add")
async def add_submit(
    name: str = Form(...),
    slug: str = Form(""),
    transport: str = Form("http"),
    base_url: str = Form(""),
    auth_type: str = Form("none"),
    bearer_token: str = Form(""),
    command: str = Form(""),
    args: str = Form(""),
    env: str = Form(""),
):
    slug = store.slugify(slug or name)
    existing = {s["slug"] for s in store.list_servers()}
    base_slug, i = slug, 2
    while slug in existing:
        slug = f"{base_slug}_{i}"; i += 1

    # stdio 的 args/env 驗證(壞掉就退回空)
    args_json, env_json = "[]", "{}"
    if transport == "stdio":
        try:
            a = json.loads(args) if args.strip() else []
            args_json = json.dumps(a if isinstance(a, list) else [], ensure_ascii=False)
        except Exception:
            pass
        try:
            e = json.loads(env) if env.strip() else {}
            env_json = json.dumps(e if isinstance(e, dict) else {}, ensure_ascii=False)
        except Exception:
            pass

    store.add_server(slug, name, base_url.strip(), auth_type, bearer_token or None,
                     transport, command.strip(), args_json, env_json)

    # 新增後立刻試連 + 記健康狀態
    srv = store.get_server(slug)
    try:
        tools = await hub.fetch_tools(srv)
        store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
        store.set_server_status(slug, "ok", f"{len(tools)} 個工具")
    except Exception as e:
        store.set_server_status(slug, "error", str(e)[:200])
    return RedirectResponse(f"/servers/{slug}", status_code=303)


# ── 下游詳情 ──────────────────────────────────────────────
@app.get("/servers/{slug}", response_class=HTMLResponse)
def server_detail(request: Request, slug: str, msg: str = ""):
    s = store.get_server(slug)
    if s is None:
        return templates.TemplateResponse(request, "message.html", {
            "active": "servers", "heading": f"找不到 {slug}", "back_href": "/", "back_label": "← 回 Servers"})
    auth_desc = {"bearer": "Bearer token", "oauth": "OAuth"}.get(s["auth_type"], "無")
    return templates.TemplateResponse(request, "server_detail.html", {
        "active": "servers",
        "s": {
            "slug": slug, "name": s["name"], "base_url": s["base_url"],
            "auth_desc": auth_desc, "is_oauth": s["auth_type"] == "oauth",
        },
        "tools": store.list_cached_tools(slug),
        "msg": msg,
    })


# ── 便利性:貼設定新增 / 匯入現有 Claude 設定 / 匯出 ──────────
def _entry_to_server(name, entry):
    """把一個 mcpServers 條目轉成 add_server 的參數 dict;無法辨識回 None。"""
    if not isinstance(entry, dict):
        return None
    # 跳過指向本 Hub 自己的設定(避免迴圈)
    blob = json.dumps(entry, ensure_ascii=False)
    if "gateway.hub_server" in blob:
        return None
    if entry.get("command"):
        return dict(name=name, base_url="", auth_type="none", bearer_token=None,
                    transport="stdio", command=entry["command"],
                    args_json=json.dumps(entry.get("args", []) or [], ensure_ascii=False),
                    env_json=json.dumps(entry.get("env", {}) or {}, ensure_ascii=False))
    url = entry.get("url") or entry.get("baseUrl") or entry.get("serverUrl")
    if url:
        auth_type, token = "none", None
        authz = (entry.get("headers") or {}).get("Authorization") or (entry.get("headers") or {}).get("authorization")
        if authz and str(authz).lower().startswith("bearer "):
            auth_type, token = "bearer", str(authz)[7:]
        return dict(name=name, base_url=url, auth_type=auth_type, bearer_token=token,
                    transport="http", command="", args_json="[]", env_json="{}")
    return None


def _extract_mcp_servers(obj):
    """從貼上的 JSON 取出 name→entry 對照。接受 {mcpServers:{...}} / {name:{...}} / 單一條目。"""
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("mcpServers"), dict):
        return obj["mcpServers"]
    if "command" in obj or "url" in obj:
        return {"server": obj}
    return obj


def _add_servers(entries):
    """entries = name→entry;回傳實際新增的數量。已存在的 slug 跳過(避免重複匯入)。"""
    existing = {s["slug"] for s in store.list_servers()}
    added = 0
    for name, entry in entries.items():
        params = _entry_to_server(name, entry)
        if not params:
            continue
        slug = store.slugify(name)
        if slug in existing:
            continue
        existing.add(slug)
        store.add_server(slug, params["name"], params["base_url"], params["auth_type"],
                         params["bearer_token"], params["transport"], params["command"],
                         params["args_json"], params["env_json"])
        added += 1
    return added


@app.post("/servers/import_config")
def import_config(config: str = Form("")):
    try:
        obj = json.loads(config)
    except Exception:
        return RedirectResponse("/servers/add?err=貼上的內容不是合法 JSON", status_code=303)
    n = _add_servers(_extract_mcp_servers(obj))
    if not n:
        return RedirectResponse("/servers/add?err=沒有可新增的 server(可能已存在或格式不符)", status_code=303)
    return RedirectResponse("/?msg=" + _u.quote(f"已新增 {n} 台下游"), status_code=303)


def _claude_config_paths():
    """
    各平台的 Claude 設定檔位置。

    原本只查 macOS 的路徑,所以在 Windows / Linux 上這個功能會安靜地回報
    「找到 0 台」—— 使用者不會知道是路徑找錯,只會以為自己沒有設定。
    `~/.claude.json`(Claude Code)三個平台都一樣。
    """
    # 三個分支一律看 sys.platform,不混用 os.name —— 測試要模擬平台時,
    # 動到 os.name 會讓 pathlib 拒絕在非 Windows 上建 WindowsPath,整個測不了。
    home = Path.home()
    paths = []
    if sys.platform == "darwin":
        paths.append(home / "Library/Application Support/Claude/claude_desktop_config.json")
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(home / ".config/Claude/claude_desktop_config.json")
    paths.append(home / ".claude.json")
    return paths


@app.post("/servers/import_claude")
def import_claude():
    """讀取現有的 Claude Desktop / Claude Code 設定,把裡面的 mcpServers 匯入。"""
    entries = {}
    for path in _claude_config_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data.get("mcpServers"), dict):
            entries.update(data["mcpServers"])
        for proj in (data.get("projects") or {}).values():  # .claude.json 專案層
            if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
                entries.update(proj["mcpServers"])
    found = sum(1 for n, e in entries.items() if _entry_to_server(n, e))
    added = _add_servers(entries)
    msg = f"從 Claude 設定找到 {found} 台可匯入,新增 {added} 台"
    if found > added:
        msg += f",略過 {found - added} 台(已存在)"
    msg += "。註:claude.ai 連接器(Gmail 等)不在本機設定,無法由此匯入。"
    return RedirectResponse("/?msg=" + _u.quote(msg), status_code=303)


@app.get("/config/export")
def export_servers():
    """把目前的下游匯出成標準 mcpServers JSON(可攜、可再匯入)。"""
    from fastapi.responses import Response
    out = {}
    for s in store.list_servers():
        if s.get("transport") == "stdio":
            entry = {"command": s["command"], "args": json.loads(s.get("args") or "[]")}
            env = json.loads(s.get("env") or "{}")
            if env:
                entry["env"] = env
        else:
            entry = {"url": s["base_url"]}
            if s["auth_type"] == "bearer" and s.get("bearer_token"):
                entry["headers"] = {"Authorization": f"Bearer {s['bearer_token']}"}
        out[s["slug"]] = entry
    payload = {"mcpServers": out}
    # 若含 stdio 下游,設定裡會有本機絕對路徑(Python 直譯器 / 家目錄),換機器可能要改。
    # 放在頂層 _note(re-import 只讀 mcpServers,會忽略它),讓提醒隨檔案一起走。
    if any(v.get("command") for v in out.values()):
        payload = {"_note": "此設定含本機絕對路徑(Python 直譯器、家目錄等),搬到別台機器可能需要調整。",
                   "mcpServers": out}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(content=body, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=mcp-hub-servers.json"})


# ── MCP 參考目錄(列表 + 一鍵建立未設定的下游,不自動安裝) ──────────
@app.get("/registry", response_class=HTMLResponse)
def registry(request: Request, msg: str = ""):
    return templates.TemplateResponse(request, "registry.html",
                                      {"active": "servers", "entries": store.list_directory_entries(), "msg": msg})


@app.post("/registry/add")
def registry_add(id: str = Form(""), name: str = Form(...), transport: str = Form("http"),
                 base_url: str = Form(""), command: str = Form(""), args: str = Form("[]"),
                 auth: str = Form("none"), needs: str = Form(""), doc_url: str = Form(""),
                 descr: str = Form("")):
    try:
        a = json.loads(args) if args.strip() else []
        args_json = json.dumps(a if isinstance(a, list) else [], ensure_ascii=False)
    except Exception:
        args_json = "[]"
    kind = "remote" if transport == "http" else "local"
    store.add_directory_entry(store.slugify(id or name), name, kind, transport,
                              base_url.strip(), command.strip(), args_json,
                              auth, needs.strip(), doc_url.strip(), descr.strip())
    return RedirectResponse("/registry?msg=" + _u.quote(f"已加入目錄項「{name}」"), status_code=303)


@app.post("/registry/{entry_id}/delete")
def registry_delete(entry_id: str):
    store.delete_directory_entry(entry_id)
    return RedirectResponse("/registry", status_code=303)


@app.post("/registry/{entry_id}/install")
def registry_install(entry_id: str):
    e = store.get_directory_entry(entry_id)
    if not e or e["kind"] == "ref" or not (e["base_url"] or e["command"]):
        return RedirectResponse("/registry", status_code=303)
    slug, existing, i = store.slugify(e["id"]), {s["slug"] for s in store.list_servers()}, 2
    base = slug
    while slug in existing:
        slug = f"{base}_{i}"; i += 1
    auth_type = {"oauth": "oauth", "token": "bearer"}.get(e["auth"], "none")
    store.add_server(slug, e["name"], e["base_url"] or "", auth_type, None,
                     e["transport"], e["command"] or "", e["args"] or "[]", "{}")
    nxt = {"oauth": "已加入,請到下方按「OAuth 授權」完成登入。",
           "bearer": "已加入,請按「編輯」填入 API token。"}.get(auth_type, "已加入,可按「重新整理工具」測試連線。")
    return RedirectResponse(f"/servers/{slug}?msg=" + _u.quote(nxt), status_code=303)


@app.post("/servers/{slug}/toggle")
def toggle_server(slug: str):
    s = store.get_server(slug)
    if s:
        store.set_server_enabled(slug, not s["enabled"])
    return RedirectResponse("/", status_code=303)


@app.post("/servers/{slug}/delete")
def delete_server(slug: str):
    store.delete_server(slug)
    return RedirectResponse("/", status_code=303)


@app.post("/servers/{slug}/refresh")
async def refresh_server(slug: str):
    s = store.get_server(slug)
    if s is None:
        return RedirectResponse("/", status_code=303)
    try:
        tools = await hub.fetch_tools(s)
        store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
        store.set_server_status(slug, "ok", f"{len(tools)} 個工具")
        msg = f"已重新整理,抓到 {len(tools)} 個工具。"
    except Exception as e:
        store.set_server_status(slug, "error", str(e)[:200])
        msg = f"連線失敗:{e}"
    return RedirectResponse(f"/servers/{slug}?msg={msg}", status_code=303)


@app.get("/servers/{slug}/edit", response_class=HTMLResponse)
def server_edit_form(request: Request, slug: str):
    s = store.get_server(slug)
    if s is None:
        return templates.TemplateResponse(request, "message.html", {
            "active": "servers", "heading": f"找不到 {slug}", "back_href": "/", "back_label": "← 回 Servers"})
    return templates.TemplateResponse(request, "server_edit.html", {"active": "servers", "s": s})


@app.post("/servers/{slug}/edit")
async def server_edit(
    request: Request, slug: str,
    name: str = Form(...), transport: str = Form("http"),
    base_url: str = Form(""), auth_type: str = Form("none"), bearer_token: str = Form(""),
    command: str = Form(""), args: str = Form(""), env: str = Form(""),
):
    if store.get_server(slug) is None:
        return RedirectResponse("/", status_code=303)
    args_json, env_json = "[]", "{}"
    if transport == "stdio":
        try:
            a = json.loads(args) if args.strip() else []
            args_json = json.dumps(a if isinstance(a, list) else [], ensure_ascii=False)
        except Exception:
            pass
        try:
            e = json.loads(env) if env.strip() else {}
            env_json = json.dumps(e if isinstance(e, dict) else {}, ensure_ascii=False)
        except Exception:
            pass
    store.update_server(slug, name, base_url.strip(), auth_type, bearer_token or None,
                        transport, command.strip(), args_json, env_json)
    # 存檔後重測健康 + 刷新工具
    srv = store.get_server(slug)
    try:
        tools = await hub.fetch_tools(srv)
        store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
        store.set_server_status(slug, "ok", f"{len(tools)} 個工具")
        msg = f"已更新,連線正常({len(tools)} 個工具)"
    except Exception as e:
        store.set_server_status(slug, "error", str(e)[:200])
        msg = f"已更新,但連線失敗:{e}"
    return RedirectResponse(f"/servers/{slug}?msg={_u.quote(msg)}", status_code=303)


@app.post("/servers/check_all")
async def check_all_route():
    """一鍵並發檢查所有啟用中的下游。注意:此字面路由的 method 為 POST,
    與 GET /servers/{slug} 不衝突。"""
    servers = [s for s in store.list_servers() if s["enabled"]]

    async def _one(s):
        try:
            status, detail, tools = await hub.check_server(s)
            store.set_server_status(s["slug"], status, detail)
            if tools is not None:
                store.cache_tools(s["slug"], [(t.name, t.description, t.inputSchema) for t in tools])
            return status == "ok"
        except Exception as e:
            store.set_server_status(s["slug"], "error", str(e)[:200])
            return False

    results = await asyncio.gather(*[_one(s) for s in servers]) if servers else []
    ok = sum(1 for r in results if r)
    msg = f"已檢查 {len(servers)} 台下游:{ok} 台正常 · {len(servers) - ok} 台異常"
    return RedirectResponse("/?msg=" + _u.quote(msg), status_code=303)


@app.post("/servers/{slug}/check")
async def check_server_route(slug: str):
    s = store.get_server(slug)
    if s:
        status, detail, tools = await hub.check_server(s)
        store.set_server_status(slug, status, detail)
        if tools is not None:
            store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
    return RedirectResponse("/", status_code=303)


# 批次開關(字面 4 段路由,與 5 段的 /tools/{tool}/... 不衝突)
@app.post("/servers/{slug}/tools/enable_all")
def enable_all_tools(slug: str):
    store.set_all_tools_enabled(slug, True)
    return RedirectResponse(f"/servers/{slug}", status_code=303)


@app.post("/servers/{slug}/tools/disable_all")
def disable_all_tools(slug: str):
    store.set_all_tools_enabled(slug, False)
    return RedirectResponse(f"/servers/{slug}", status_code=303)


@app.post("/servers/{slug}/tools/{tool}/toggle_enabled")
def toggle_tool_enabled(slug: str, tool: str):
    t = store.get_tool(slug, tool)
    if t:
        store.set_tool_flag(slug, tool, enabled=not t["enabled"])
    return RedirectResponse(f"/servers/{slug}", status_code=303)


@app.post("/servers/{slug}/tools/{tool}/toggle_confirm")
def toggle_tool_confirm(slug: str, tool: str):
    t = store.get_tool(slug, tool)
    if t:
        store.set_tool_flag(slug, tool, needs_confirm=not t["needs_confirm"])
    return RedirectResponse(f"/servers/{slug}", status_code=303)


@app.post("/servers/{slug}/tools/{tool}/desc")
def set_tool_desc(slug: str, tool: str, desc: str = Form("")):
    # 策展:覆寫給 AI 看的工具描述;留空＝還原成下游原始描述
    store.set_tool_desc(slug, tool, desc.strip())
    return RedirectResponse(f"/servers/{slug}", status_code=303)


# ── OAuth 互動授權(骨架,需真實 OAuth 服務才跑得完) ──────────
# OAuth 2.1 + PKCE 互動授權入口。
@app.get("/servers/{slug}/oauth/start")
async def oauth_start(request: Request, slug: str):
    srv = store.get_server(slug)
    if srv is None:
        return RedirectResponse("/", status_code=303)

    flow = auth.ConsoleOAuthFlow.start(slug)
    provider = await auth.provider_for(srv, flow.redirect_handler, flow.callback_handler)

    # 背景觸發授權握手:provider 會呼叫 redirect_handler 給我們授權網址,並等 callback
    err_box = {}

    async def run():
        try:
            async with hub._http_session(srv["base_url"], auth=provider) as s:
                await s.list_tools()
        except Exception as e:
            err_box["err"] = e
        finally:
            # 握手途中探查到的 metadata 先存 —— token 是在 list_tools 之前就拿到並寫進 DB 的,
            # 若只在成功路徑存 metadata,一旦 list_tools 失敗就會留下「有 token、沒 metadata」,
            # 之後 refresh 只能猜 token endpoint。
            auth.persist_discovered_metadata(slug, provider)
            flow.close()

    task = asyncio.create_task(run())
    # 等「拿到授權網址」或「背景任務先失敗」,誰先到算誰
    url_fut = flow.auth_url
    await asyncio.wait({url_fut, task}, timeout=15, return_when=asyncio.FIRST_COMPLETED)
    if url_fut.done() and not url_fut.cancelled() and url_fut.exception() is None:
        return RedirectResponse(url_fut.result(), status_code=303)

    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    flow.close()
    return templates.TemplateResponse(request, "message.html", {
        "active": "servers", "heading": "無法開始授權",
        "message": _oauth_error_hint(err_box.get("err")),
        "back_href": f"/servers/{slug}", "back_label": "← 返回"})


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


def _exception_details(err):
    # 實作已移到 hub.py —— 錯誤是在那裡產生的,健檢與管理台要用同一套解讀。
    return hub.exception_details(err)


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
        request: Request, code: str = "", state: str = "",
        error: str = "", error_description: str = ""):
    callback_error = error_description or error
    ok = await auth.ConsoleOAuthFlow.resolve_callback(code, state, callback_error)
    if error:
        msg = f"授權服務未完成授權：{error_description or error}"
    elif ok:
        msg = "已收到授權回呼，Hub 正在交換 token。可回管理台重新整理工具。"
    else:
        msg = "找不到對應的授權流程，可能已逾時或 state 不符。"
    return templates.TemplateResponse(request, "message.html", {
        "heading": "OAuth 回呼", "message": msg, "back_href": "/", "back_label": "← 回 Servers"})


# ── 自訂工具(把 HTTP API 包成 MCP 工具) ────────────────────
_PARAMS_EG = '[{"name":"city","type":"string","required":true,"description":"城市名"}]'
_HEADERS_EG = '{"Authorization": "Bearer YOUR_KEY"}'
_STEPS_EG = '[{"id":"digest","tool":"orgpulse__get_my_weekly_digest","args":{}},{"id":"commits","tool":"orgpulse__get_my_commits","args":{}}]'


def _schema_to_params(schema):
    schema = schema or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params = []
    for name, spec in props.items():
        params.append({
            "name": name,
            "type": spec.get("type", "string"),
            "required": name in required,
            "description": spec.get("description", ""),
        })
    return params


def _available_step_tools():
    tools = []
    for srv in store.enabled_servers():
        slug = srv["slug"]
        for t in store.list_cached_tools(slug):
            if not t["enabled"]:
                continue
            tools.append({
                "name": f"{slug}__{t['name']}",
                "description": t.get("desc_override") or (t.get("description") or ""),
                "params": _schema_to_params(json.loads(t.get("input_schema") or "{}")),
                "kind": "downstream",
            })
    for t in store.enabled_custom_tools():
        tools.append({
            "name": t["name"],
            "description": t.get("description") or "",
            "params": t.get("params") or [],
            "kind": "custom",
        })
    return sorted(tools, key=lambda t: t["name"].lower())


def _composite_form_ctx(t=None, error=""):
    is_edit = t is not None
    return {
        "is_edit": is_edit,
        "name": t["name"] if t else "",
        "group": (t["group_name"] if t else ""),
        "description": (t["description"] if t else ""),
        "params_val": json.dumps(t["params"], ensure_ascii=False, indent=2) if (t and t["params"]) else "",
        "params_init": json.dumps(t["params"], ensure_ascii=False) if (t and t["params"]) else "[]",
        "steps_val": json.dumps(t["steps"], ensure_ascii=False, indent=2) if (t and t["steps"]) else "",
        "steps_init": json.dumps(t["steps"], ensure_ascii=False) if (t and t["steps"]) else "[]",
        "available_tools_json": json.dumps(_available_step_tools(), ensure_ascii=False),
        "output": (t["output"] if t else "collect"),
        "action": f"/composites/{t['name']}/save" if is_edit else "/composites/add",
        "error": error,
    }


def _parse_composite_form(params, steps, output):
    import json as _j
    if (output or "collect") != "collect":
        raise ValueError("目前只支援 collect output")
    try:
        params_obj = _j.loads(params) if params.strip() else []
        assert isinstance(params_obj, list)
        for p in params_obj:
            assert "name" in p
    except Exception:
        raise ValueError("參數清單必須是合法的 JSON 陣列,每項要有 name,例如 " + _PARAMS_EG)
    try:
        steps_obj = _j.loads(steps) if steps.strip() else []
        assert isinstance(steps_obj, list) and steps_obj
        for s in steps_obj:
            assert "id" in s and "tool" in s
            assert isinstance(s.get("args", {}), dict)
    except Exception:
        raise ValueError("步驟必須是合法的 JSON 陣列,每項要有 id / tool / args,例如 " + _STEPS_EG)
    return _j.dumps(params_obj, ensure_ascii=False), _j.dumps(steps_obj, ensure_ascii=False)


@app.get("/composites", response_class=HTMLResponse)
def composites_index(request: Request):
    return templates.TemplateResponse(request, "composites.html", {
        "active": "composites",
        "tools": store.list_composite_tools(),
    })


@app.get("/composites/add", response_class=HTMLResponse)
def composite_add_form(request: Request):
    return templates.TemplateResponse(request, "composite_add.html", {"active": "composites", **_composite_form_ctx()})


@app.post("/composites/add")
def composite_add(
    request: Request,
    name: str = Form(...), description: str = Form(""), params: str = Form(""),
    steps: str = Form(""), output: str = Form("collect"), group: str = Form(""),
):
    import re
    name = name.strip()

    def _err(msg):
        return templates.TemplateResponse(request, "composite_add.html", {"active": "composites", **_composite_form_ctx(error=msg)})

    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        return _err("工具名稱只能用英數、底線、連字號")
    conflict = _tool_name_conflict(name)
    if conflict:
        return _err(conflict)
    try:
        p, s = _parse_composite_form(params, steps, output)
    except ValueError as e:
        return _err(str(e))
    store.upsert_composite_tool(name, description, p, s, output, group.strip())
    return RedirectResponse(f"/composites/{name}", status_code=303)


@app.get("/composites/{name}", response_class=HTMLResponse)
def composite_detail(request: Request, name: str, msg: str = ""):
    t = store.get_composite_tool(name)
    if t is None:
        return templates.TemplateResponse(request, "message.html", {
            "active": "composites", "heading": f"找不到 {name}", "back_href": "/composites", "back_label": "← 回複合工具"})
    ctx = {"active": "composites", "msg": msg, "enabled": t["enabled"], "needs_confirm": t["needs_confirm"]}
    ctx.update(_composite_form_ctx(t))
    return templates.TemplateResponse(request, "composite_detail.html", ctx)


@app.post("/composites/{name}/save")
def composite_save(
    request: Request,
    name: str, description: str = Form(""), params: str = Form(""),
    steps: str = Form(""), output: str = Form("collect"), group: str = Form(""),
):
    try:
        p, s = _parse_composite_form(params, steps, output)
    except ValueError as e:
        t = store.get_composite_tool(name)
        ctx = {"active": "composites", "msg": "", "enabled": t["enabled"], "needs_confirm": t["needs_confirm"]}
        ctx.update(_composite_form_ctx(t, error=str(e)))
        return templates.TemplateResponse(request, "composite_detail.html", ctx)
    store.upsert_composite_tool(name, description, p, s, output, group.strip())
    return RedirectResponse(f"/composites/{name}?msg=已儲存", status_code=303)


@app.post("/composites/{name}/delete")
def composite_delete(name: str):
    store.delete_composite_tool(name)
    return RedirectResponse("/composites", status_code=303)


@app.post("/composites/{name}/toggle_enabled")
def composite_toggle_enabled(name: str):
    t = store.get_composite_tool(name)
    if t:
        store.set_composite_tool_flag(name, enabled=not t["enabled"])
    return RedirectResponse(f"/composites/{name}", status_code=303)


@app.post("/composites/{name}/toggle_confirm")
def composite_toggle_confirm(name: str):
    t = store.get_composite_tool(name)
    if t:
        store.set_composite_tool_flag(name, needs_confirm=not t["needs_confirm"])
    return RedirectResponse(f"/composites/{name}", status_code=303)


@app.post("/composites/{name}/test", response_class=HTMLResponse)
async def composite_test(request: Request, name: str, args: str = Form("")):
    t = store.get_composite_tool(name)
    if t is None:
        return RedirectResponse("/composites", status_code=303)
    try:
        arguments = json.loads(args) if args.strip() else {}
        result = "\n\n".join(blocks_to_texts(await execute_named_tool(name, arguments)))
    except Exception as e:
        result = f"❌ 錯誤:{e}"
    return templates.TemplateResponse(request, "tool_test_result.html", {
        "active": "composites", "name": name, "args": args or "{}", "result": result,
        "back_href": f"/composites/{name}",
    })


@app.get("/composites/{name}/skill", response_class=HTMLResponse)
def composite_skill(request: Request, name: str):
    t = store.get_composite_tool(name)
    if t is None:
        return templates.TemplateResponse(request, "message.html", {
            "active": "composites", "heading": f"找不到 {name}", "back_href": "/composites", "back_label": "← 回複合工具"})
    saved = store.get_skill_draft(name)
    return templates.TemplateResponse(request, "composite_skill.html", {
        "active": "composites", "name": name, "purpose": (saved or {}).get("purpose") or t.get("description") or "",
        "draft": (saved or {}).get("skill_md") or skills.starter_skill(t), "error": "",
        "saved": bool(saved), "skill_name": skills.skill_name(name),
    })


@app.post("/composites/{name}/skill/download")
def composite_skill_download(request: Request, name: str, draft: str = Form(...), purpose: str = Form("")):
    t = store.get_composite_tool(name)
    if t is None:
        return RedirectResponse("/composites", status_code=303)
    try:
        content = skills.skill_zip(name, draft)
    except ValueError as exc:
        return templates.TemplateResponse(request, "composite_skill.html", {
            "active": "composites", "name": name, "purpose": purpose,
            "draft": draft, "error": f"無法匯出：{exc}", "saved": bool(store.get_skill_draft(name)),
            "skill_name": skills.skill_name(name),
        })
    filename = skills.skill_name(name) + ".zip"
    return Response(content, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })


def _form_ctx(t=None, error=""):
    """新增 / 編輯自訂工具表單的 context(給 _tool_form.html)。"""
    is_edit = t is not None
    return {
        "is_edit": is_edit,
        "name": t["name"] if t else "",
        "group": (t["group_name"] if t else ""),
        "description": (t["description"] if t else ""),
        "method": (t["method"] if t else "GET"),
        "url_template": (t["url_template"] if t else ""),
        "headers_val": json.dumps(t["headers"], ensure_ascii=False) if (t and t["headers"]) else "",
        "params_val": json.dumps(t["params"], ensure_ascii=False, indent=2) if (t and t["params"]) else "",
        "action": f"/tools/{t['name']}/save" if is_edit else "/tools/add",
        "error": error,
    }


@app.get("/tools", response_class=HTMLResponse)
def tools_index(request: Request):
    named, uncat = store.category_overview()
    cats = []
    for c in named:
        cats.append({"name": c["name"], "disp": c["name"], "q": _u.quote(c["name"]),
                     "total": c["total"], "enabled": c["enabled"], "is_uncat": False})
    if uncat and uncat["total"]:
        cats.append({"name": "", "disp": "未分類", "q": "",
                     "total": uncat["total"], "enabled": uncat["enabled"], "is_uncat": True})
    return templates.TemplateResponse(request, "categories.html", {"active": "tools", "cats": cats})


@app.get("/category", response_class=HTMLResponse)
def tools_category(request: Request, name: str = ""):
    tools = store.list_tools_in_category(name)
    named, _ = store.category_overview()
    cat_options = [c["name"] for c in named if c["name"] != name]
    return templates.TemplateResponse(request, "category.html", {
        "active": "tools",
        "name": name,
        "disp": "未分類" if name == "" else name,
        "tools": tools,
        "cat_options": cat_options,
        "cur_headers": json.dumps(tools[0]["headers"], ensure_ascii=False) if tools else "",
    })


@app.get("/tools/add", response_class=HTMLResponse)
def tool_add_form(request: Request):
    return templates.TemplateResponse(request, "tool_add.html", {"active": "tools", **_form_ctx()})


def _parse_tool_form(name, description, method, url_template, headers, params):
    """驗證並回傳 (headers_json, params_json) 或丟出錯誤訊息。"""
    import json as _j
    try:
        headers_obj = _j.loads(headers) if headers.strip() else {}
        assert isinstance(headers_obj, dict)
    except Exception:
        raise ValueError("Headers 必須是合法的 JSON 物件,例如 " + _HEADERS_EG)
    try:
        params_obj = _j.loads(params) if params.strip() else []
        assert isinstance(params_obj, list)
        for p in params_obj:
            assert "name" in p
    except Exception:
        raise ValueError("參數清單必須是合法的 JSON 陣列,每項要有 name,例如 " + _PARAMS_EG)
    return _j.dumps(headers_obj, ensure_ascii=False), _j.dumps(params_obj, ensure_ascii=False)


@app.post("/tools/add")
def tool_add(
    request: Request,
    name: str = Form(...), description: str = Form(""), method: str = Form("GET"),
    url_template: str = Form(...), headers: str = Form(""), params: str = Form(""),
    group: str = Form(""),
):
    import re
    name = name.strip()

    def _err(msg):
        return templates.TemplateResponse(request, "tool_add.html", {"active": "tools", **_form_ctx(error=msg)})

    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        return _err("工具名稱只能用英數、底線、連字號")
    conflict = _tool_name_conflict(name)
    if conflict:
        return _err(conflict)
    try:
        h, p = _parse_tool_form(name, description, method, url_template, headers, params)
    except ValueError as e:
        return _err(str(e))
    store.upsert_custom_tool(name, description, method, url_template, h, p, group.strip())
    return RedirectResponse(f"/tools/{name}", status_code=303)


@app.get("/tools/{name}", response_class=HTMLResponse)
def tool_detail(request: Request, name: str, msg: str = ""):
    t = store.get_custom_tool(name)
    if t is None:
        return templates.TemplateResponse(request, "message.html", {
            "active": "tools", "heading": f"找不到 {name}", "back_href": "/tools", "back_label": "← 回自訂工具"})
    ctx = {"active": "tools", "msg": msg, "enabled": t["enabled"], "needs_confirm": t["needs_confirm"]}
    ctx.update(_form_ctx(t))
    return templates.TemplateResponse(request, "tool_detail.html", ctx)


@app.post("/tools/{name}/save")
def tool_save(
    request: Request,
    name: str, description: str = Form(""), method: str = Form("GET"),
    url_template: str = Form(...), headers: str = Form(""), params: str = Form(""),
    group: str = Form(""),
):
    try:
        h, p = _parse_tool_form(name, description, method, url_template, headers, params)
    except ValueError as e:
        t = store.get_custom_tool(name)
        ctx = {"active": "tools", "msg": "", "enabled": t["enabled"], "needs_confirm": t["needs_confirm"]}
        ctx.update(_form_ctx(t, error=str(e)))
        return templates.TemplateResponse(request, "tool_detail.html", ctx)
    store.upsert_custom_tool(name, description, method, url_template, h, p, group.strip())
    return RedirectResponse(f"/tools/{name}?msg=已儲存", status_code=303)


@app.post("/tools/{name}/delete")
def tool_delete(name: str):
    store.delete_custom_tool(name)
    return RedirectResponse("/tools", status_code=303)


@app.post("/tools/{name}/toggle_enabled")
def tool_toggle_enabled(name: str):
    t = store.get_custom_tool(name)
    if t:
        store.set_custom_tool_flag(name, enabled=not t["enabled"])
    return RedirectResponse(f"/tools/{name}", status_code=303)


@app.post("/tools/{name}/toggle_confirm")
def tool_toggle_confirm(name: str):
    t = store.get_custom_tool(name)
    if t:
        store.set_custom_tool_flag(name, needs_confirm=not t["needs_confirm"])
    return RedirectResponse(f"/tools/{name}", status_code=303)


def _cat_redirect(cat):
    return RedirectResponse(f"/category?name={_u.quote(cat)}", status_code=303)


# 分類層級操作
@app.post("/category/create")
def category_create(name: str = Form("")):
    store.create_category(name)
    return RedirectResponse("/tools", status_code=303)


@app.post("/category/delete")
def category_delete(name: str = Form(...)):
    store.delete_category(name)  # 連同工具一起刪
    return RedirectResponse("/tools", status_code=303)


@app.post("/category/enable")
def category_enable(name: str = Form(...)):
    store.set_category_enabled(name, True)
    return RedirectResponse("/tools", status_code=303)


@app.post("/category/disable")
def category_disable(name: str = Form(...)):
    store.set_category_enabled(name, False)
    return RedirectResponse("/tools", status_code=303)


# 勾選(在分類內)層級操作
@app.post("/selected/delete")
def selected_delete(cat: str = Form(""), sel: list[str] = Form([])):
    store.delete_custom_tools(sel)
    return _cat_redirect(cat)


@app.post("/selected/enable")
def selected_enable(cat: str = Form(""), sel: list[str] = Form([])):
    store.set_custom_tools_enabled(sel, True)
    return _cat_redirect(cat)


@app.post("/selected/disable")
def selected_disable(cat: str = Form(""), sel: list[str] = Form([])):
    store.set_custom_tools_enabled(sel, False)
    return _cat_redirect(cat)


@app.post("/selected/move")
def selected_move(cat: str = Form(""), target: str = Form(""), sel: list[str] = Form([])):
    store.set_tools_category(sel, target)
    return _cat_redirect(target if sel else cat)


@app.post("/category/set_headers")
def category_set_headers(name: str = Form(...), headers: str = Form("")):
    try:
        obj = json.loads(headers) if headers.strip() else {}
        if not isinstance(obj, dict):
            raise ValueError
        store.set_category_headers(name, json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass  # JSON 壞掉就忽略(不動原本設定)
    return _cat_redirect(name)


@app.post("/tools/{name}/test", response_class=HTMLResponse)
async def tool_test(request: Request, name: str, args: str = Form("")):
    t = store.get_custom_tool(name)
    if t is None:
        return RedirectResponse("/tools", status_code=303)
    try:
        arguments = json.loads(args) if args.strip() else {}
        result = await custom.execute(t, arguments)
    except Exception as e:
        result = f"❌ 錯誤:{e}"
    return templates.TemplateResponse(request, "tool_test_result.html", {
        "active": "tools", "name": name, "args": args or "{}", "result": result,
        "back_href": f"/tools/{name}",
    })


# ── OpenAPI 匯入 ──────────────────────────────────────────
async def _resolve_spec(spec_url, spec_text):
    if spec_text and spec_text.strip():
        return openapi.load_spec(spec_text)
    return openapi.load_spec(await openapi.fetch_spec(spec_url))


@app.get("/import", response_class=HTMLResponse)
def import_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "import.html", {"active": "tools", "error": error})


@app.post("/import/preview", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    spec_url: str = Form(""), spec_text: str = Form(""), base_url: str = Form(""),
    prefix: str = Form(""), headers: str = Form(""), group: str = Form(""),
):
    try:
        spec = await _resolve_spec(spec_url, spec_text)
        ops = openapi.operations(spec)
    except Exception as e:
        return RedirectResponse(f"/import?error=解析失敗:{e}", status_code=303)
    if not ops:
        return RedirectResponse("/import?error=這份 spec 找不到任何端點", status_code=303)

    resolved_base = base_url.strip() or openapi.base_url_of(spec)
    ops_ctx = [{
        "op_id": o["op_id"], "method": o["method"], "path": o["path"], "summary": o["summary"],
        "nparams": len(o["params"]), "tags": o.get("tags", [])[:2],
        "text": f"{o['method']} {o['path']} {o['summary']} {' '.join(o.get('tags', []))}".lower(),
    } for o in ops]
    return templates.TemplateResponse(request, "import_preview.html", {
        "active": "tools", "base": resolved_base, "ops": ops_ctx,
        "carry": {"spec_url": spec_url, "spec_text": spec_text, "prefix": prefix, "group": group, "headers": headers},
    })


@app.post("/import/create")
async def import_create(
    spec_url: str = Form(""), spec_text: str = Form(""), base_url: str = Form(""),
    prefix: str = Form(""), headers: str = Form(""), group: str = Form(""), op: list[str] = Form([]),
):
    try:
        spec = await _resolve_spec(spec_url, spec_text)
    except Exception as e:
        return RedirectResponse(f"/import?error=解析失敗:{e}", status_code=303)

    resolved_base = base_url.strip() or openapi.base_url_of(spec)
    selected = set(op)
    headers_json = headers.strip() or "{}"
    # 分類名:你填的優先 → spec 標題 → 前綴 → 「匯入」
    category = group.strip() or openapi.title_of(spec) or prefix.strip().rstrip("_") or "匯入"
    store.create_category(category)
    for o in openapi.operations(spec):
        if o["op_id"] not in selected:
            continue
        name, desc, method, url_t, hdr, params = openapi.to_custom_tool(o, resolved_base, prefix, headers_json)
        store.upsert_custom_tool(name, desc, method, url_t, hdr, params, category)
    return _cat_redirect(category)


@app.post("/import/probe")
async def import_probe(
    request: Request,
    probe_url: str = Form(""), base_url: str = Form(""),
    prefix: str = Form(""), group: str = Form(""), headers: str = Form(""),
):
    if not probe_url.strip():
        return RedirectResponse("/import?error=請填服務根網址", status_code=303)
    found, spec, tried = await openapi.probe(probe_url)
    if not found:
        return RedirectResponse(
            f"/import?error=在 {probe_url} 找不到 OpenAPI spec(試過 {len(tried)} 個常見路徑)",
            status_code=303,
        )
    # 找到了 → 直接重用預覽流程(spec_url 用探到的網址,讓匯入時可再抓)
    return await import_preview(
        request, spec_url=found, spec_text="", base_url=base_url, prefix=prefix, headers=headers, group=group
    )


# ── 公開目錄搜尋(APIs.guru) ──────────────────────────────
@app.get("/directory", response_class=HTMLResponse)
async def directory_search(request: Request, q: str = ""):
    error, results, total = "", [], 0
    if q:
        try:
            results, total = await directory.search(q)
        except Exception as e:
            error = f"目錄載入失敗(需連外網):{e}"
    return templates.TemplateResponse(request, "directory.html", {
        "active": "tools", "q": q, "error": error, "results": results, "total": total,
    })


# ── 接入教學 ──────────────────────────────────────────────
@app.get("/connect", response_class=HTMLResponse)
def connect(request: Request):
    proj = str(Path(__file__).parent.parent)
    return templates.TemplateResponse(request, "connect.html",
                                      {"active": "connect", "py": sys.executable, "proj": proj})


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    proj = str(Path(__file__).parent.parent)
    return templates.TemplateResponse(request, "guide.html",
                                      {"active": "guide", "py": sys.executable, "proj": proj})


# ── Logs ──────────────────────────────────────────────────
_LOGS_PAGE = 50


@app.get("/logs", response_class=HTMLResponse)
def logs(request: Request, page: int = 1):
    total = store.count_logs()
    errors = store.count_logs(errors_only=True)      # 全期錯誤數
    pages = max(1, (total + _LOGS_PAGE - 1) // _LOGS_PAGE)
    page = max(1, min(page, pages))
    rows = []
    for r in store.list_logs(_LOGS_PAGE, (page - 1) * _LOGS_PAGE):
        st = r["status"]
        cls = "error" if st == "error" else ("ok" if st in ("ok", "executed_after_approval") else "off")
        ns = r["server_slug"] or ""          # 下游命名空間;自訂工具為空
        tname = r["tool"] or ""
        rows.append({
            "time": r["time"],
            "ns": ns,
            "tname": tname,
            "tool": f"{ns}__{tname}" if ns else tname,   # 供搜尋用
            "args": json.dumps(json.loads(r["arguments"]), ensure_ascii=False) if r["arguments"] else "",
            "status": st,
            "status_cls": cls,
            "dur": f"{r['duration_ms']}ms" if r["duration_ms"] is not None else "",
            "error": r["error"] or "",
        })
    return templates.TemplateResponse(request, "logs.html", {
        "active": "logs", "rows": rows, "total": total, "errors": errors,
        "page": page, "pages": pages,
    })


# ── 核准頁(v1,保留給第 4 步「可選確認」) ────────────────
@app.get("/a/{action_id}", response_class=HTMLResponse)
def approval_detail(request: Request, action_id: str):
    a = store.get_action(action_id)
    if a is None:
        return templates.TemplateResponse(request, "message.html", {
            "heading": f"找不到票券 {action_id}", "back_href": "/", "back_label": "← 回首頁"})
    return templates.TemplateResponse(request, "approval.html", {
        "a": a,
        "args": json.dumps(a["arguments"], ensure_ascii=False, indent=2),
        "result": json.dumps(a["result"], ensure_ascii=False, indent=2) if a["status"] == store.EXECUTED else "",
    })


@app.post("/a/{action_id}/approve")
def approve(action_id: str):
    store.decide(action_id, approve=True)
    return RedirectResponse(f"/a/{action_id}", status_code=303)


@app.post("/a/{action_id}/reject")
def reject(action_id: str):
    store.decide(action_id, approve=False)
    return RedirectResponse(f"/a/{action_id}", status_code=303)


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
