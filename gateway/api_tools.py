"""
JSON API —— 自訂工具、複合工具、分類。

與 api.py 分開只是為了檔案大小,設計原則完全相同:只做驗證、參數轉換、序列化,
業務邏輯一律呼叫既有的 store / custom / composite / dispatch / skills。

這裡最需要小心的是**自訂工具的 headers**:它是加密欄位,通常放 API 金鑰。
store 回傳時已經解密,直接序列化就會把金鑰送出去 —— 一律只回報「有哪些 key、
有沒有值」,不回傳內容。更新時省略某個 key 代表不動,傳空字串代表刪除。
"""

import json
import re

from fastapi import APIRouter, Depends

from gateway import store, custom, composite, skills
from gateway.api import _fail, _loads, publish, require_token
from gateway.dispatch import blocks_to_texts, execute_named_tool

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


# ── 序列化 ────────────────────────────────────────────────
def _headers_out(headers):
    """
    headers 可能含 API 金鑰 —— 只回報有哪些 key、有沒有值,不回傳內容。
    前端要顯示「已設定 Authorization」時夠用,而金鑰不會離開這台機器。
    """
    return {k: {"set": bool(v)} for k, v in (headers or {}).items()}


def _custom_out(row):
    return {
        "name": row["name"],
        "description": row["description"] or "",
        "method": row["method"],
        "url_template": row["url_template"],
        "headers": _headers_out(row.get("headers")),
        "params": row.get("params") or [],
        "enabled": bool(row["enabled"]),
        "needs_confirm": bool(row["needs_confirm"]),
        "group_name": row.get("group_name") or "",
    }


def _composite_out(row):
    return {
        "name": row["name"],
        "description": row["description"] or "",
        "params": row.get("params") or [],
        "steps": row.get("steps") or [],
        "output": row.get("output") or "collect",
        "enabled": bool(row["enabled"]),
        "needs_confirm": bool(row["needs_confirm"]),
        "group_name": row.get("group_name") or "",
    }


# ── 名稱衝突檢查 ──────────────────────────────────────────
RESERVED_NAMES = {"check_action", "skill_workbench"}
_NAME_RE = re.compile(r"[a-zA-Z0-9_-]+")


def _name_conflict(name, *, allow_existing=None):
    """
    與 web.py 的 _tool_name_conflict 同一套規則。

    複製這段是刻意的:web.py 即將被移除,而規則本身屬於這一層。
    移除網頁時 web.py 那份會一起消失,不會留下兩份。
    """
    if not _NAME_RE.fullmatch(name):
        return "工具名稱只能用英數、底線、連字號"
    if name in RESERVED_NAMES:
        return f"工具名稱不能使用保留字 {name}"
    if "__" in name:
        return "工具名稱不能包含 __,避免與下游的 slug__tool 命名空間衝突"
    existing = store.get_custom_tool(name) or store.get_composite_tool(name)
    if existing and name != allow_existing:
        return f"名稱 {name} 已存在"
    for s in store.list_servers():
        if store.get_tool(s["slug"], name):
            return f"名稱 {name} 與既有的下游工具衝突"
    return ""


def _require_name(payload, *, allow_existing=None):
    name = (payload.get("name") or "").strip()
    if not name:
        _fail(400, "invalid_request", "name 不可為空。")
    problem = _name_conflict(name, allow_existing=allow_existing)
    if problem:
        _fail(409, "name_conflict", problem)
    return name


def _require_list(payload, key, default=None):
    value = payload.get(key, default)
    if value is None:
        return []
    if not isinstance(value, list):
        _fail(400, "invalid_request", f"{key} 必須是陣列。")
    return value


# ── 自訂工具 ──────────────────────────────────────────────
@router.get("/custom-tools")
def list_custom_tools():
    return [_custom_out(t) for t in store.list_custom_tools()]


@router.get("/custom-tools/{name}")
def get_custom_tool(name: str):
    tool = store.get_custom_tool(name)
    if tool is None:
        _fail(404, "custom_tool_not_found", f"找不到自訂工具 {name}。")
    return _custom_out(tool)


@router.post("/custom-tools", status_code=201)
def create_custom_tool(payload: dict):
    name = _require_name(payload)
    url = (payload.get("url_template") or "").strip()
    if not url:
        _fail(400, "invalid_request", "url_template 不可為空。")

    headers = payload.get("headers") or {}
    if not isinstance(headers, dict):
        _fail(400, "invalid_request", "headers 必須是物件。")

    store.upsert_custom_tool(
        name, (payload.get("description") or "").strip(),
        (payload.get("method") or "GET").upper(), url,
        json.dumps(headers, ensure_ascii=False),
        json.dumps(_require_list(payload, "params"), ensure_ascii=False),
        (payload.get("group_name") or "").strip(),
    )
    publish("tools_changed", {"kind": "custom", "name": name})
    return _custom_out(store.get_custom_tool(name))


@router.patch("/custom-tools/{name}")
def update_custom_tool(name: str, payload: dict):
    tool = store.get_custom_tool(name)
    if tool is None:
        _fail(404, "custom_tool_not_found", f"找不到自訂工具 {name}。")

    # 只改旗標時不必帶其他欄位
    if "enabled" in payload or "needs_confirm" in payload:
        store.set_custom_tool_flag(
            name,
            enabled=None if "enabled" not in payload else bool(payload["enabled"]),
            needs_confirm=None if "needs_confirm" not in payload else bool(payload["needs_confirm"]),
        )

    content_keys = {"description", "method", "url_template", "headers", "params", "group_name"}
    if content_keys & payload.keys():
        store.upsert_custom_tool(
            name,
            (payload.get("description", tool["description"]) or "").strip(),
            (payload.get("method", tool["method"]) or "GET").upper(),
            (payload.get("url_template", tool["url_template"]) or "").strip(),
            json.dumps(_merge_headers(tool.get("headers"), payload.get("headers")),
                       ensure_ascii=False),
            json.dumps(payload.get("params", tool.get("params") or []), ensure_ascii=False),
            (payload.get("group_name", tool.get("group_name")) or "").strip(),
        )
    publish("tools_changed", {"kind": "custom", "name": name})
    return _custom_out(store.get_custom_tool(name))


def _merge_headers(current, incoming):
    """
    省略某個 header 代表「不動」,傳空字串代表「刪除」。

    這是因為 API 不會把既有的 header 值送給 client(那是金鑰),所以 client
    無法把完整的 headers 送回來 —— 只能表達差異。
    """
    merged = dict(current or {})
    if incoming is None:
        return merged
    if not isinstance(incoming, dict):
        _fail(400, "invalid_request", "headers 必須是物件。")
    for key, value in incoming.items():
        if value == "":
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


@router.delete("/custom-tools/{name}", status_code=204)
def delete_custom_tool(name: str):
    if store.get_custom_tool(name) is None:
        _fail(404, "custom_tool_not_found", f"找不到自訂工具 {name}。")
    store.delete_custom_tool(name)
    publish("tools_changed", {"kind": "custom", "name": name, "deleted": True})


@router.post("/custom-tools/{name}/test")
async def test_custom_tool(name: str, payload: dict | None = None):
    tool = store.get_custom_tool(name)
    if tool is None:
        _fail(404, "custom_tool_not_found", f"找不到自訂工具 {name}。")
    return await _run_tool(name, (payload or {}).get("arguments") or {})


# ── 複合工具 ──────────────────────────────────────────────
@router.get("/composite-tools")
def list_composite_tools():
    return [_composite_out(t) for t in store.list_composite_tools()]


@router.get("/composite-tools/{name}")
def get_composite_tool(name: str):
    tool = store.get_composite_tool(name)
    if tool is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")
    return _composite_out(tool)


@router.post("/composite-tools", status_code=201)
def create_composite_tool(payload: dict):
    name = _require_name(payload)
    steps = _validate_steps(_require_list(payload, "steps"))
    _validate_output(payload.get("output"))

    store.upsert_composite_tool(
        name, (payload.get("description") or "").strip(),
        json.dumps(_require_list(payload, "params"), ensure_ascii=False),
        json.dumps(steps, ensure_ascii=False),
        payload.get("output") or "collect",
        (payload.get("group_name") or "").strip(),
    )
    publish("tools_changed", {"kind": "composite", "name": name})
    return _composite_out(store.get_composite_tool(name))


@router.patch("/composite-tools/{name}")
def update_composite_tool(name: str, payload: dict):
    tool = store.get_composite_tool(name)
    if tool is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")

    if "enabled" in payload or "needs_confirm" in payload:
        store.set_composite_tool_flag(
            name,
            enabled=None if "enabled" not in payload else bool(payload["enabled"]),
            needs_confirm=None if "needs_confirm" not in payload else bool(payload["needs_confirm"]),
        )

    content_keys = {"description", "params", "steps", "output", "group_name"}
    if content_keys & payload.keys():
        steps = _validate_steps(payload.get("steps", tool.get("steps") or []))
        _validate_output(payload.get("output", tool.get("output")))
        store.upsert_composite_tool(
            name,
            (payload.get("description", tool["description"]) or "").strip(),
            json.dumps(payload.get("params", tool.get("params") or []), ensure_ascii=False),
            json.dumps(steps, ensure_ascii=False),
            payload.get("output", tool.get("output")) or "collect",
            (payload.get("group_name", tool.get("group_name")) or "").strip(),
        )
    publish("tools_changed", {"kind": "composite", "name": name})
    return _composite_out(store.get_composite_tool(name))


@router.delete("/composite-tools/{name}", status_code=204)
def delete_composite_tool(name: str):
    if store.get_composite_tool(name) is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")
    store.delete_composite_tool(name)   # 連同 skill 草稿一起刪
    publish("tools_changed", {"kind": "composite", "name": name, "deleted": True})


@router.post("/composite-tools/{name}/test")
async def test_composite_tool(name: str, payload: dict | None = None):
    if store.get_composite_tool(name) is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")
    return await _run_tool(name, (payload or {}).get("arguments") or {})


async def _run_tool(name, arguments):
    """試跑一個工具。錯誤要回得出來,不能只給「失敗了」。"""
    try:
        blocks = await execute_named_tool(name, arguments)
        return {"ok": True, "texts": blocks_to_texts(blocks)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


def _validate_steps(steps):
    """每個步驟要有 id / tool,args 若有必須是物件 —— 與網頁版同一套規則。"""
    if not isinstance(steps, list) or not steps:
        _fail(400, "invalid_request", "複合工具至少要有一個步驟。")
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("id") or not s.get("tool"):
            _fail(400, "invalid_request", f"第 {i + 1} 個步驟缺少 id 或 tool。")
        if not isinstance(s.get("args", {}), dict):
            _fail(400, "invalid_request", f"第 {i + 1} 個步驟的 args 必須是物件。")
    return steps


def _validate_output(output):
    if (output or "collect") != "collect":
        _fail(400, "invalid_request", "目前只支援 collect output。")


# ── 複合工具的 Skill ──────────────────────────────────────
@router.get("/composite-tools/{name}/skill")
def get_skill(name: str):
    tool = store.get_composite_tool(name)
    if tool is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")
    draft = store.get_skill_draft(name)
    markdown = (draft or {}).get("skill_md") or skills.starter_skill(tool)
    # validate 是拋例外不是回傳清單 —— 這裡要的是「告訴 client 哪裡不對」,不是中斷
    try:
        skills.validate_skill_markdown(markdown, expected_name=skills.skill_name(name))
        problem = ""
    except ValueError as e:
        problem = str(e)
    return {
        "name": skills.skill_name(name),
        "markdown": markdown,
        "purpose": (draft or {}).get("purpose") or tool["description"] or "",
        "is_draft": draft is not None,
        "problem": problem,
    }


@router.put("/composite-tools/{name}/skill")
def save_skill(name: str, payload: dict):
    if store.get_composite_tool(name) is None:
        _fail(404, "composite_tool_not_found", f"找不到複合工具 {name}。")
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        _fail(400, "invalid_request", "markdown 不可為空。")

    try:
        skills.validate_skill_markdown(markdown, expected_name=skills.skill_name(name))
    except ValueError as e:
        _fail(400, "invalid_skill", str(e))

    store.save_skill_draft(name, markdown, (payload.get("purpose") or "").strip())
    return get_skill(name)


# ── 分類 ──────────────────────────────────────────────────
@router.get("/categories")
def list_categories():
    named, uncategorized = store.category_overview()
    return {"categories": named, "uncategorized": uncategorized}


@router.post("/categories", status_code=201)
def create_category(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        _fail(400, "invalid_request", "name 不可為空。")
    store.create_category(name)
    return list_categories()


@router.delete("/categories/{name}", status_code=204)
def delete_category(name: str):
    """刪分類會連同裡面的自訂工具一起刪 —— 與網頁版行為一致。"""
    store.delete_category(name)
    publish("tools_changed", {"kind": "category", "name": name, "deleted": True})


@router.patch("/categories/{name}")
def update_category(name: str, payload: dict):
    if "enabled" in payload:
        store.set_category_enabled(name, bool(payload["enabled"]))
    if "headers" in payload:
        headers = payload["headers"]
        if not isinstance(headers, dict):
            _fail(400, "invalid_request", "headers 必須是物件。")
        # 分類的 headers 是整批覆寫,不是合併 —— 這是「把同一把金鑰套到整組工具」的操作
        store.set_category_headers(name, json.dumps(headers, ensure_ascii=False))
    publish("tools_changed", {"kind": "category", "name": name})
    return list_categories()


@router.get("/categories/{name}/tools")
def tools_in_category(name: str):
    return [_custom_out(t) for t in store.list_tools_in_category(name)]


# ── 可用的步驟工具 ────────────────────────────────────────
def _schema_to_params(schema):
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    return [{"name": k, "type": v.get("type", "string"),
             "required": k in required, "description": v.get("description", "")}
            for k, v in props.items()]


@router.get("/step-tools")
def available_step_tools():
    """
    複合工具的步驟可以挑哪些工具。

    只列啟用中的 —— 步驟指向一個停用的工具,執行時才會失敗,那時已經太遲。
    複合工具本身不列入,避免互相引用繞不出來。
    """
    out = []
    for srv in store.enabled_servers():
        for t in store.list_cached_tools(srv["slug"]):
            if not t["enabled"]:
                continue
            out.append({
                "name": f"{srv['slug']}__{t['name']}",
                "description": t.get("desc_override") or t.get("description") or "",
                "params": _schema_to_params(_loads(t.get("input_schema"), {})),
                "kind": "downstream",
            })
    for t in store.enabled_custom_tools():
        out.append({"name": t["name"], "description": t["description"] or "",
                    "params": t.get("params") or [], "kind": "custom"})
    return sorted(out, key=lambda t: t["name"].lower())


# ── 批次操作 ──────────────────────────────────────────────
@router.post("/custom-tools/bulk")
def bulk_custom_tools(payload: dict):
    """
    對一組自訂工具做同一件事。

    action 是明確的動詞而非 toggle —— 批次 toggle 在多 client 下結果不可預期。
    """
    names = _require_list(payload, "names")
    if not names:
        _fail(400, "invalid_request", "names 不可為空。")

    action = payload.get("action")
    if action == "enable":
        store.set_custom_tools_enabled(names, True)
    elif action == "disable":
        store.set_custom_tools_enabled(names, False)
    elif action == "delete":
        store.delete_custom_tools(names)
    elif action == "move":
        store.set_tools_category(names, payload.get("category") or "")
    else:
        _fail(400, "invalid_request",
              "action 必須是 enable / disable / delete / move 其中之一。")

    publish("tools_changed", {"kind": "custom", "bulk": action})
    return {"affected": len(names), "action": action}
