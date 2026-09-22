"""
Hub 共用執行器 —— 給 hub_server 與管理台試跑共用。
"""

import time

import mcp.types as types

from gateway import composite, custom, pool, store

SEP = "__"


def result_to_texts(result):
    return [b.text for b in result.content if getattr(b, "type", None) == "text"]


def texts_to_blocks(texts):
    blocks = [types.TextContent(type="text", text=t) for t in (texts or [])]
    return blocks or [types.TextContent(type="text", text="(沒有回傳內容)")]


def text_block(s):
    return [types.TextContent(type="text", text=s)]


def blocks_to_texts(blocks):
    return [b.text for b in blocks if getattr(b, "type", None) == "text"]


async def _forward(slug, tool, arguments):
    srv = store.get_server(slug)
    start = time.perf_counter()
    try:
        result = await pool.call_tool(srv, tool, arguments)
        store.log_call(slug, tool, arguments, "ok", int((time.perf_counter() - start) * 1000))
        return texts_to_blocks(result_to_texts(result))
    except Exception as e:
        store.log_call(slug, tool, arguments, "error", int((time.perf_counter() - start) * 1000), str(e))
        return text_block(f"呼叫下游失敗:{e}")


async def dispatch_tool(name, arguments):
    """實際執行單一步驟(不含 composite / 確認判斷)。"""
    c = store.get_custom_tool(name)
    if c:
        if not c["enabled"]:
            return text_block(f"已停用的工具:{name}")
        start = time.perf_counter()
        try:
            text = await custom.execute(c, arguments)
            store.log_call("(custom)", name, arguments, "ok", int((time.perf_counter() - start) * 1000))
            return text_block(text)
        except Exception as e:
            store.log_call("(custom)", name, arguments, "error", int((time.perf_counter() - start) * 1000), str(e))
            return text_block(f"自訂工具執行失敗:{e}")

    slug, _, tool = name.partition(SEP)
    srv = store.get_server(slug)
    if srv is None or not srv["enabled"]:
        return text_block(f"未知或已停用的工具:{name}")
    return await _forward(slug, tool, arguments)


async def execute_composite_tool(tool_def, arguments):
    step_results = {}
    for step in tool_def.get("steps") or []:
        step_args = composite.resolve_args(step.get("args") or {}, arguments or {})
        blocks = await dispatch_tool(step["tool"], step_args)
        step_results[step["id"]] = blocks_to_texts(blocks)

    output = (tool_def.get("output") or "collect").lower()
    if output != "collect":
        return text_block(f"不支援的 composite output:{output}")
    return text_block(composite.collect_texts(step_results))


async def execute_named_tool(name, arguments):
    comp = store.get_composite_tool(name)
    if comp:
        if not comp["enabled"]:
            return text_block(f"已停用的工具:{name}")
        start = time.perf_counter()
        try:
            blocks = await execute_composite_tool(comp, arguments)
            store.log_call("(composite)", name, arguments, "ok", int((time.perf_counter() - start) * 1000))
            return blocks
        except Exception as e:
            store.log_call("(composite)", name, arguments, "error", int((time.perf_counter() - start) * 1000), str(e))
            return text_block(f"複合工具執行失敗:{e}")
    return await dispatch_tool(name, arguments)
