"""
JSON API —— 匯入下游、OpenAPI 匯入、目錄項、設定匯出。

與 api.py / api_tools.py 同一套原則:不含業務邏輯,一律呼叫既有函式。

有一處刻意**不與網頁版相同**:設定匯出。網頁版會把解密後的 bearer token 明文
寫進匯出檔,而 api.py 從一開始就把密鑰排除在回應之外。這裡沿用後者 —— 詳見
`export_config` 的說明。
"""

import json
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from gateway import store, openapi, directory, skills, clients
from gateway.api import _fail, publish, require_token

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


# ── 從 mcpServers JSON 匯入下游 ───────────────────────────
def _entry_to_server(name, entry):
    """把一個 mcpServers 條目轉成 add_server 的參數;無法辨識回 None。"""
    if not isinstance(entry, dict):
        return None
    # 跳過指向本 Hub 自己的設定,避免迴圈
    if "gateway.hub_server" in json.dumps(entry, ensure_ascii=False):
        return None

    if entry.get("command"):
        return dict(name=name, base_url="", auth_type="none", bearer_token=None,
                    transport="stdio", command=entry["command"],
                    args_json=json.dumps(entry.get("args") or [], ensure_ascii=False),
                    env_json=json.dumps(entry.get("env") or {}, ensure_ascii=False))

    url = entry.get("url") or entry.get("baseUrl") or entry.get("serverUrl")
    if not url:
        return None

    auth_type, token = "none", None
    headers = entry.get("headers") or {}
    authz = headers.get("Authorization") or headers.get("authorization") or ""
    if str(authz).lower().startswith("bearer "):
        auth_type, token = "bearer", str(authz)[7:]

    return dict(name=name, base_url=url, auth_type=auth_type, bearer_token=token,
                transport="http", command="", args_json="[]", env_json="{}")


def _extract_mcp_servers(obj):
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("mcpServers"), dict):
        return obj["mcpServers"]
    if "command" in obj or "url" in obj:
        return {"server": obj}
    return obj


def _add_servers(entries):
    """回傳 (新增的 slug 清單, 略過的名稱清單)。slug 已存在的一律略過,不覆寫。"""
    existing = {s["slug"] for s in store.list_servers()}
    added, skipped = [], []
    for name, entry in (entries or {}).items():
        params = _entry_to_server(name, entry)
        if params is None:
            skipped.append(name)
            continue
        slug = store.slugify(name)
        if slug in existing:
            skipped.append(name)
            continue
        existing.add(slug)
        store.add_server(slug, params["name"], params["base_url"], params["auth_type"],
                         params["bearer_token"], params["transport"], params["command"],
                         params["args_json"], params["env_json"])
        added.append(slug)
    return added, skipped


@router.post("/import/mcp-servers")
def import_mcp_servers(payload: dict):
    """貼上一份 mcpServers JSON(或整份 Claude 設定)直接匯入。"""
    config = payload.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception as e:
            _fail(400, "invalid_json", f"貼上的內容不是合法 JSON:{e}")
    if not isinstance(config, dict):
        _fail(400, "invalid_request", "config 必須是物件或 JSON 字串。")

    added, skipped = _add_servers(_extract_mcp_servers(config))
    if added:
        publish("server_changed", {"imported": len(added)})
    return {"added": added, "skipped": skipped}


# ── 接進 AI client ────────────────────────────────────────
# 讀 Claude 設定來匯入下游是一回事;把 Hub 自己寫進去是另一回事。
# 後者以前只能複製指令手動貼,而那正是最容易出錯的一步。

@router.get("/clients")
def list_clients():
    return clients.status()


@router.post("/clients/{client_id}/install")
def install_client(client_id: str):
    try:
        return clients.install(client_id)
    except clients.ClientError as e:
        _fail(400, "client_install_failed", str(e))


@router.delete("/clients/{client_id}")
def uninstall_client(client_id: str):
    try:
        return clients.uninstall(client_id)
    except clients.ClientError as e:
        _fail(400, "client_uninstall_failed", str(e))


def _claude_config_paths():
    """各平台的 Claude 設定檔位置。與 web.py 同一套,移除網頁時這裡會是唯一的一份。"""
    home = Path.home()
    paths = []
    if sys.platform == "darwin":
        paths.append(home / "Library/Application Support/Claude/claude_desktop_config.json")
    elif sys.platform.startswith("win"):
        import os
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(home / ".config/Claude/claude_desktop_config.json")
    paths.append(home / ".claude.json")
    return paths


@router.get("/import/claude-config")
def peek_claude_config():
    """先看 Claude 設定裡有什麼可以匯入,不實際寫入 —— 匯入前該讓人看清楚。"""
    entries, sources = _collect_claude_entries()
    existing = {s["slug"] for s in store.list_servers()}
    preview = []
    for name, entry in entries.items():
        params = _entry_to_server(name, entry)
        if params is None:
            continue
        slug = store.slugify(name)
        preview.append({
            "name": name, "slug": slug,
            "transport": params["transport"],
            "target": params["command"] or params["base_url"],
            "auth_type": params["auth_type"],
            "already_exists": slug in existing,
        })
    return {"sources": sources, "entries": sorted(preview, key=lambda e: e["name"])}


@router.post("/import/claude-config")
def import_claude_config():
    entries, sources = _collect_claude_entries()
    added, skipped = _add_servers(entries)
    if added:
        publish("server_changed", {"imported": len(added)})
    return {"sources": sources, "added": added, "skipped": skipped}


def _collect_claude_entries():
    """讀所有平台候選路徑,合併其中的 mcpServers。回傳 (entries, 實際讀到的檔案)。"""
    entries, sources = {}, []
    for path in _claude_config_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sources.append(str(path))
        if isinstance(data.get("mcpServers"), dict):
            entries.update(data["mcpServers"])
        for proj in (data.get("projects") or {}).values():   # .claude.json 的專案層
            if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
                entries.update(proj["mcpServers"])
    return entries, sources


# ── OpenAPI 匯入 ──────────────────────────────────────────
async def _resolve_spec(spec_url, spec_text):
    if spec_text and spec_text.strip():
        spec = openapi.load_spec(spec_text)
    elif (spec_url or "").strip():
        spec = openapi.load_spec(await openapi.fetch_spec(spec_url))
    else:
        _fail(400, "invalid_request", "要提供 spec_url 或 spec_text 其中之一。")

    # YAML 會把任意一行文字解析成字串而不報錯 —— 沒有這個檢查的話,
    # 貼進垃圾內容會在後面某處 AttributeError,使用者只看到 500。
    if not isinstance(spec, dict) or not openapi._looks_like_spec(spec):
        _fail(400, "invalid_spec", "這不是一份 OpenAPI / Swagger spec。")
    return spec


async def _parse_spec(payload):
    """解析 spec,把底層的例外轉成看得懂的 400。_fail 自己拋的不要再包一層。"""
    try:
        return await _resolve_spec(payload.get("spec_url"), payload.get("spec_text"))
    except HTTPException:
        raise
    except Exception as e:
        _fail(400, "invalid_spec", f"解析失敗:{e}")


@router.post("/openapi/probe")
async def probe_openapi(payload: dict):
    """給一個服務根網址,試幾個常見路徑找 OpenAPI spec。"""
    url = (payload.get("url") or "").strip()
    if not url:
        _fail(400, "invalid_request", "url 不可為空。")
    try:
        found, spec, tried = await openapi.probe(url)
    except HTTPException:
        raise
    except Exception as e:
        _fail(502, "probe_failed", f"探查失敗:{e}")
    if not found:
        _fail(404, "spec_not_found",
              f"在 {url} 找不到 OpenAPI spec(試過 {len(tried)} 個常見路徑)。")
    return {"spec_url": found, "tried": tried, "title": openapi.title_of(spec),
            "base_url": openapi.base_url_of(spec)}


@router.post("/openapi/preview")
async def preview_openapi(payload: dict):
    spec = await _parse_spec(payload)

    ops = openapi.operations(spec)
    if not ops:
        _fail(400, "no_operations", "這份 spec 找不到任何端點。")

    base = (payload.get("base_url") or "").strip() or openapi.base_url_of(spec)
    return {
        "title": openapi.title_of(spec),
        "base_url": base,
        "operations": [{
            "op_id": o["op_id"], "method": o["method"], "path": o["path"],
            "summary": o["summary"], "param_count": len(o["params"]), "tags": o["tags"][:2],
        } for o in ops],
    }


@router.post("/openapi/import", status_code=201)
async def import_openapi(payload: dict):
    spec = await _parse_spec(payload)

    wanted = set(payload.get("op_ids") or [])
    if not wanted:
        _fail(400, "invalid_request", "op_ids 不可為空。")

    headers = payload.get("headers") or {}
    if not isinstance(headers, dict):
        # 網頁版完全不驗證這個,壞掉的 headers 會存進去、讀取時才爆
        _fail(400, "invalid_request", "headers 必須是物件。")
    headers_json = json.dumps(headers, ensure_ascii=False)

    base = (payload.get("base_url") or "").strip() or openapi.base_url_of(spec)
    prefix = (payload.get("prefix") or "").strip()
    category = ((payload.get("group_name") or "").strip()
                or openapi.title_of(spec)
                or prefix.rstrip("_")
                or "匯入")
    store.create_category(category)

    from gateway.api_tools import _name_conflict   # 延遲匯入避免循環

    created, conflicts = [], []
    for op in openapi.operations(spec):
        if op["op_id"] not in wanted:
            continue
        name, desc, method, url_t, hdr, params = openapi.to_custom_tool(
            op, base, prefix, headers_json)
        # 網頁版沒有做這個檢查,匯入的名稱可能覆蓋既有工具或撞到保留字
        problem = _name_conflict(name)
        if problem:
            conflicts.append({"name": name, "reason": problem})
            continue
        store.upsert_custom_tool(name, desc, method, url_t, hdr, params, category)
        created.append(name)

    if created:
        publish("tools_changed", {"kind": "custom", "imported": len(created)})
    return {"category": category, "created": created, "conflicts": conflicts}


# ── 公開 API 目錄(APIs.guru)─────────────────────────────
@router.get("/directory/search")
async def search_directory(q: str = "", limit: int = 60):
    if not q.strip():
        return {"query": q, "results": [], "total": 0}
    try:
        results, total = await directory.search(q, limit=limit)
    except HTTPException:
        raise
    except Exception as e:
        _fail(502, "directory_unavailable", f"目錄載入失敗(需連外網):{e}")
    return {"query": q, "results": results, "total": total}


# ── 目錄項(directory_entries,網頁版稱為 registry)────────
def _entry_out(row):
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"],
        "transport": row["transport"], "base_url": row["base_url"] or "",
        "command": row["command"] or "",
        "args": json.loads(row["args"] or "[]"),
        "auth": row["auth"], "needs": row["needs"] or "",
        "doc_url": row["doc_url"] or "", "descr": row["descr"] or "",
        "builtin": bool(row["builtin"]),
    }


@router.get("/directory-entries")
def list_directory_entries():
    return [_entry_out(e) for e in store.list_directory_entries()]


@router.post("/directory-entries", status_code=201)
def add_directory_entry(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        _fail(400, "invalid_request", "name 不可為空。")

    args = payload.get("args") or []
    if not isinstance(args, list):
        # 網頁版把壞掉的 args 默默吞成 [],使用者不會知道自己填錯了
        _fail(400, "invalid_request", "args 必須是陣列。")

    transport = payload.get("transport") or "http"
    entry_id = store.slugify(payload.get("id") or name)
    store.add_directory_entry(
        entry_id, name, "remote" if transport == "http" else "local", transport,
        (payload.get("base_url") or "").strip(), (payload.get("command") or "").strip(),
        json.dumps(args, ensure_ascii=False), payload.get("auth") or "none",
        (payload.get("needs") or "").strip(), (payload.get("doc_url") or "").strip(),
        (payload.get("descr") or "").strip(),
    )
    return _entry_out(store.get_directory_entry(entry_id))


@router.delete("/directory-entries/{entry_id}", status_code=204)
def delete_directory_entry(entry_id: str):
    entry = store.get_directory_entry(entry_id)
    if entry is None:
        _fail(404, "directory_entry_not_found", f"找不到目錄項 {entry_id}。")
    if entry["builtin"]:
        # 網頁版靜默失敗(SQL 帶 builtin=0),使用者按了沒反應也不知道為什麼
        _fail(409, "builtin_entry", "內建目錄項不能刪除,重啟後也會被重新寫入。")
    store.delete_directory_entry(entry_id)


@router.post("/directory-entries/{entry_id}/install", status_code=201)
def install_directory_entry(entry_id: str):
    entry = store.get_directory_entry(entry_id)
    if entry is None:
        _fail(404, "directory_entry_not_found", f"找不到目錄項 {entry_id}。")
    if entry["kind"] == "ref" or not (entry["base_url"] or entry["command"]):
        # 網頁版遇到這種情況只是靜默轉址回目錄頁,使用者不知道發生什麼事
        _fail(400, "not_installable", "這個目錄項只是參考資料,沒有可安裝的連線設定。")

    existing = {s["slug"] for s in store.list_servers()}
    slug, i = store.slugify(entry["id"]), 2
    while slug in existing:
        slug, i = f"{store.slugify(entry['id'])}_{i}", i + 1

    auth_type = {"oauth": "oauth", "token": "bearer"}.get(entry["auth"], "none")
    store.add_server(slug, entry["name"], entry["base_url"] or "", auth_type, None,
                     entry["transport"], entry["command"] or "", entry["args"] or "[]", "{}")
    publish("server_changed", {"slug": slug})

    next_step = {
        "oauth": "已加入,請完成 OAuth 授權。",
        "bearer": "已加入,請編輯並填入 API token。",
    }.get(auth_type, "已加入,可以重新整理工具測試連線。")
    return {"slug": slug, "auth_type": auth_type, "next_step": next_step}


# ── 設定匯出 ──────────────────────────────────────────────
@router.get("/config/export")
def export_config(include_secrets: bool = False):
    """
    匯出成 mcpServers 格式。

    **與網頁版的差異**:網頁版一律把解密後的 bearer token 明文寫進匯出檔。
    這個專案的整個加密設計就是為了「有人讀到檔案也拿不到密鑰」,而一個會自動
    把密鑰攤平的匯出按鈕,等於在旁邊開了一道門。

    這裡預設**不含密鑰**,要帶必須明確要求(include_secrets=true)——
    使用者仍然做得到,但那是一個有意識的選擇,不是按一下就發生的副作用。
    """
    out, has_local = {}, False
    for s in store.list_servers():
        if s.get("transport") == "stdio":
            has_local = True
            entry = {"command": s["command"], "args": json.loads(s.get("args") or "[]")}
            env = json.loads(s.get("env") or "{}")
            if env:
                entry["env"] = env if include_secrets else {k: "" for k in env}
        else:
            entry = {"url": s["base_url"]}
            if s["auth_type"] == "bearer" and s.get("bearer_token"):
                token = s["bearer_token"] if include_secrets else "<填入你的 token>"
                entry["headers"] = {"Authorization": f"Bearer {token}"}
        out[s["slug"]] = entry

    payload = {"mcpServers": out}
    notes = []
    if has_local:
        notes.append("此設定含本機絕對路徑(Python 直譯器、家目錄等),搬到別台機器可能需要調整。")
    if not include_secrets:
        notes.append("密鑰已略過,請在目標機器重新填入。")
    if notes:
        payload = {"_note": " ".join(notes), "mcpServers": out}

    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="mcp-hub-servers.json"'},
    )


# ── Skill ZIP 下載 ────────────────────────────────────────
@router.post("/composite-tools/{name}/skill/download")
def download_skill(name: str, payload: dict):
    """
    回傳真正的 ZIP 位元組,不是 JSON —— 這是少數必須保持二進位的端點。
    """
    if store.get_composite_tool(name) is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        _fail(400, "invalid_request", "markdown 不可為空。")

    try:
        data = skills.skill_zip(name, markdown)
    except ValueError as e:
        _fail(400, "invalid_skill", f"無法匯出:{e}")

    filename = f"{skills.skill_name(name)}.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
