"""
聚合器 MCP Server —— 對 Claude 是「一台 server」。

Claude 看到的工具 = 兩種來源合併:
  A. 下游 MCP 聚合來的      (slug__tool)             ← 角色 A:聚合器
  B. Hub 自己的自訂工具      (直接用工具名,打 HTTP API) ← 角色 B:轉接器
  另有 Hub 自己的 meta 工具 check_action(核准後取結果)

任何工具都可被標記 needs_confirm → 走票券核准流程,核准後才真的執行。
"""

import json
import asyncio

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from gateway import store, custom, composite, skills
from gateway.dispatch import execute_named_tool, text_block

SEP = "__"

server = Server("mcp-hub")


_CHECK_ACTION_TOOL = types.Tool(
    name="check_action",
    description="當某個操作回覆『需要人工確認』時,使用者在 MCP Hub app 按下核准後,用這個工具(帶 action_id)取得最終結果。",
    inputSchema={
        "type": "object",
        "properties": {"action_id": {"type": "string", "description": "待確認操作的票券 id"}},
        "required": ["action_id"],
    },
)

_SKILL_WORKBENCH_TOOL = types.Tool(
    name="skill_workbench",
    description="調適複合工具的 Skill。可查看定義、試跑、驗證並儲存 SKILL.md；使用者要求建立或調整 Skill 時使用。",
    inputSchema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "inspect", "preview", "validate", "save"]},
            "composite_name": {"type": "string", "description": "複合工具名稱；list 不需要"},
            "arguments": {"type": "object", "description": "preview 試跑時傳給複合工具的參數"},
            "purpose": {"type": "string", "description": "Skill 的用途與預期產出"},
            "skill_md": {"type": "string", "description": "validate/save 使用的完整 SKILL.md"},
        },
        "required": ["action"],
    },
)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    store.init_db()
    # 暫停時一個工具都不給。每次 list_tools 都重讀 DB,所以在 app 裡按下去就生效,
    # 不用重啟 Claude。
    if store.is_paused():
        return []
    out = []
    any_confirm = False  # 有沒有任何暴露出去的工具開了「需確認」

    # A. 下游聚合
    for srv in store.enabled_servers():
        slug = srv["slug"]
        try:
            from gateway import pool
            tools = await pool.fetch_tools(srv)
        except Exception:
            continue
        store.cache_tools(slug, [(t.name, t.description, t.inputSchema) for t in tools])
        by_name = {t.name: t for t in tools}
        for ct in store.list_cached_tools(slug):
            if not ct["enabled"]:
                continue
            t = by_name.get(ct["name"])
            if t is None:
                continue
            hint = " ⚠️需人工確認" if ct["needs_confirm"] else ""
            if ct["needs_confirm"]:
                any_confirm = True
            desc = ct.get("desc_override") or (t.description or "")  # 有策展描述就用覆寫
            out.append(types.Tool(
                name=f"{slug}{SEP}{t.name}",
                description=f"[{srv['name']}] {desc}{hint}",
                inputSchema=t.inputSchema,
            ))

    # B. 自訂工具
    for c in store.enabled_custom_tools():
        hint = " ⚠️需人工確認" if c["needs_confirm"] else ""
        if c["needs_confirm"]:
            any_confirm = True
        out.append(types.Tool(
            name=c["name"],
            description=f"{c['description'] or ''}{hint}",
            inputSchema=custom.build_schema(c["params"]),
        ))

    # C. 複合工具
    for c in store.enabled_composite_tools():
        hint = " ⚠️需人工確認" if c["needs_confirm"] else ""
        if c["needs_confirm"]:
            any_confirm = True
        out.append(types.Tool(
            name=c["name"],
            description=f"{c['description'] or ''}{hint}",
            inputSchema=composite.build_schema(c["params"]),
        ))

    # 只有真的有工具需要確認時,才暴露 check_action(否則它只是噪音)
    out.append(_SKILL_WORKBENCH_TOOL)
    if any_confirm:
        out.append(_CHECK_ACTION_TOOL)
    return out


def _needs_confirm(name):
    comp = store.get_composite_tool(name)
    if comp:
        return bool(comp["needs_confirm"])
    c = store.get_custom_tool(name)
    if c:
        return bool(c["needs_confirm"])
    slug, _, tool = name.partition(SEP)
    t = store.get_tool(slug, tool)
    return bool(t and t["needs_confirm"])


async def _resume(action_id):
    """核准後取結果;APPROVED 才真的執行,EXECUTED 後冪等。"""
    a = store.get_action(action_id)
    if a is None:
        return text_block(f"❌ 找不到票券 {action_id}")
    st = a["status"]
    if st == store.WAITING:
        return text_block(f"⏳ 票券 {action_id} 還在等你在 MCP Hub app 確認。核准後再呼叫我一次。")
    if st == store.REJECTED:
        return text_block(f"🚫 你已拒絕票券 {action_id}(操作沒有執行)。")
    if st == store.EXECUTED:
        return [types.TextContent(type="text", text=t) for t in a["result"]]
    if st == store.APPROVED:
        blocks = await execute_named_tool(a["tool"], a["arguments"])
        store.save_result(action_id, [b.text for b in blocks])
        store.log_call("(approved)", a["tool"], a["arguments"], "executed_after_approval")
        return blocks
    return text_block(f"❓ 票券 {action_id} 狀態異常:{st}")


_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["approve", "reject"],
            "description": "approve = 允許執行,reject = 拒絕",
        }
    },
    "required": ["decision"],
}


async def _try_elicit_confirm(name, arguments):
    """
    若 client 支援 elicitation,就在對話內直接跳確認框(免開網頁)。
    回 list[TextContent] = 已就地處理(同意→執行 / 拒絕→回絕);
    回 None = client 不支援或失敗 → 交給票券流程。
    """
    try:
        session = server.request_context.session
        if not getattr(session.client_params.capabilities, "elicitation", None):
            return None
    except Exception:
        return None

    try:
        result = await session.elicit(
            message=f"⚠️ 需人工確認:AI 想執行「{name}」,參數 {json.dumps(arguments, ensure_ascii=False)}。是否允許?",
            requestedSchema=_CONFIRM_SCHEMA,
        )
    except Exception:
        return None  # elicit 失敗 → 退回票券流程

    approved = result.action == "accept" and (result.content or {}).get("decision") == "approve"
    if approved:
        store.log_call("(confirm)", name, arguments, "elicit_approved")
        return await execute_named_tool(name, arguments)
    store.log_call("(confirm)", name, arguments, "elicit_rejected")
    return text_block("🚫 你拒絕了這個操作(未執行)。")


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    store.init_db()
    arguments = arguments or {}

    # client 可能還握著暫停前拿到的工具清單,所以呼叫這端也要擋。
    if store.is_paused():
        return text_block("MCP Hub 目前是暫停狀態,沒有可用的工具。到 MCP Hub 的選單列圖示按「繼續」即可恢復。")

    if name == "check_action":
        return await _resume(arguments.get("action_id"))
    if name == "skill_workbench":
        return text_block(await skills.execute_workbench(arguments, execute_named_tool))

    if _needs_confirm(name):
        # 優先:對話內彈窗確認(client 支援 elicitation 時)
        elicited = await _try_elicit_confirm(name, arguments)
        if elicited is not None:
            return elicited
        # 退回:票券流程,使用者到 app 的「待確認」處理
        action_id = store.create_action(name, arguments)
        store.log_call("(confirm)", name, arguments, "blocked_need_confirm")
        return text_block(
            f"⚠️ 「{name}」被設為需人工確認,已建立待確認票券。\n"
            f"請到 MCP Hub app 的「待確認」核准(票券 {action_id})。\n"
            f"核准後,呼叫 check_action(action_id=\"{action_id}\") 取得結果。"
        )

    return await execute_named_tool(name, arguments)


async def main():
    store.init_db()
    # 起連線池 worker,再跑 MCP server;兩者在同一個 task group 內
    async with anyio.create_task_group() as tg:
        from gateway import pool
        pool.bind(tg)  # 連線 task 都跑在這個 group 裡
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
        await pool.stop()  # server 結束 → 各連線優雅收尾並關閉 session


if __name__ == "__main__":
    asyncio.run(main())
