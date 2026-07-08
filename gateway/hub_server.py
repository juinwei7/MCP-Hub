"""
聚合器 MCP Server —— 對 Claude 是「一台 server」。

Claude 看到的工具 = 兩種來源合併:
  A. 下游 MCP 聚合來的      (slug__tool)             ← 角色 A:聚合器
  B. Hub 自己的自訂工具      (直接用工具名,打 HTTP API) ← 角色 B:轉接器
  另有 Hub 自己的 meta 工具 check_action(核准後取結果)

任何工具都可被標記 needs_confirm → 走票券核准流程,核准後才真的執行。
"""

import time
import json
import asyncio

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from gateway import hub, store, custom, pool
from gateway.config import BASE_URL

SEP = "__"

server = Server("mcp-hub")


# ── 小工具 ────────────────────────────────────────────────
def _result_to_texts(result):
    return [b.text for b in result.content if getattr(b, "type", None) == "text"]


def _texts_to_blocks(texts):
    blocks = [types.TextContent(type="text", text=t) for t in (texts or [])]
    return blocks or [types.TextContent(type="text", text="(沒有回傳內容)")]


def _text(s):
    return [types.TextContent(type="text", text=s)]


_CHECK_ACTION_TOOL = types.Tool(
    name="check_action",
    description="當某個操作回覆『需要人工確認』時,使用者在核准頁按下核准後,用這個工具(帶 action_id)取得最終結果。",
    inputSchema={
        "type": "object",
        "properties": {"action_id": {"type": "string", "description": "待確認操作的票券 id"}},
        "required": ["action_id"],
    },
)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    store.init_db()
    out = []
    any_confirm = False  # 有沒有任何暴露出去的工具開了「需確認」

    # A. 下游聚合
    for srv in store.enabled_servers():
        slug = srv["slug"]
        try:
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

    # 只有真的有工具需要確認時,才暴露 check_action(否則它只是噪音)
    if any_confirm:
        out.append(_CHECK_ACTION_TOOL)
    return out


async def _forward(slug, tool, arguments):
    """轉發給下游 MCP,並記 log。"""
    srv = store.get_server(slug)
    start = time.perf_counter()
    try:
        result = await pool.call_tool(srv, tool, arguments)
        store.log_call(slug, tool, arguments, "ok", int((time.perf_counter() - start) * 1000))
        return _texts_to_blocks(_result_to_texts(result))
    except Exception as e:
        store.log_call(slug, tool, arguments, "error", int((time.perf_counter() - start) * 1000), str(e))
        return _text(f"❌ 呼叫下游失敗:{e}")


async def _dispatch(name, arguments):
    """實際執行(不含確認判斷):自訂工具 → 打 API;否則 → 轉發下游。"""
    c = store.get_custom_tool(name)
    if c:
        start = time.perf_counter()
        try:
            text = await custom.execute(c, arguments)
            store.log_call("(custom)", name, arguments, "ok", int((time.perf_counter() - start) * 1000))
            return _text(text)
        except Exception as e:
            store.log_call("(custom)", name, arguments, "error", int((time.perf_counter() - start) * 1000), str(e))
            return _text(f"❌ 自訂工具執行失敗:{e}")

    slug, _, tool = name.partition(SEP)
    srv = store.get_server(slug)
    if srv is None or not srv["enabled"]:
        return _text(f"❌ 未知或已停用的工具:{name}")
    return await _forward(slug, tool, arguments)


def _needs_confirm(name):
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
        return _text(f"❌ 找不到票券 {action_id}")
    st = a["status"]
    if st == store.WAITING:
        return _text(f"⏳ 票券 {action_id} 還在等你在核准頁確認。核准後再呼叫我一次。")
    if st == store.REJECTED:
        return _text(f"🚫 你已拒絕票券 {action_id}(操作沒有執行)。")
    if st == store.EXECUTED:
        return _texts_to_blocks(a["result"])
    if st == store.APPROVED:
        blocks = await _dispatch(a["tool"], a["arguments"])
        store.save_result(action_id, [b.text for b in blocks])
        store.log_call("(approved)", a["tool"], a["arguments"], "executed_after_approval")
        return blocks
    return _text(f"❓ 票券 {action_id} 狀態異常:{st}")


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
        return await _dispatch(name, arguments)
    store.log_call("(confirm)", name, arguments, "elicit_rejected")
    return _text("🚫 你拒絕了這個操作(未執行)。")


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    store.init_db()
    arguments = arguments or {}

    if name == "check_action":
        return await _resume(arguments.get("action_id"))

    if _needs_confirm(name):
        # 優先:對話內彈窗確認(client 支援 elicitation 時)
        elicited = await _try_elicit_confirm(name, arguments)
        if elicited is not None:
            return elicited
        # 退回:票券 + 核准頁流程
        action_id = store.create_action(name, arguments)
        store.log_call("(confirm)", name, arguments, "blocked_need_confirm")
        return _text(
            f"⚠️ 「{name}」被設為需人工確認,已建立待確認票券。\n"
            f"請開啟核准頁:{BASE_URL}/a/{action_id}\n"
            f"核准後,呼叫 check_action(action_id=\"{action_id}\") 取得結果。"
        )

    return await _dispatch(name, arguments)


async def main():
    store.init_db()
    # 起連線池 worker,再跑 MCP server;兩者在同一個 task group 內
    async with anyio.create_task_group() as tg:
        pool.bind(tg)  # 連線 task 都跑在這個 group 裡
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
        await pool.stop()  # server 結束 → 各連線優雅收尾並關閉 session


if __name__ == "__main__":
    asyncio.run(main())
