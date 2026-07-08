"""
複合工具 —— 依序執行多個既有 Hub 工具,並組裝成單一結果。
"""

import json
import re

from gateway import custom

_INPUT_REF = re.compile(r"\{\{\s*input\.([a-zA-Z0-9_-]+)\s*\}\}")


def build_schema(params):
    return custom.build_schema(params)


def _resolve_string(template, arguments):
    matches = list(_INPUT_REF.finditer(template))
    if not matches:
        return template

    # 只有一個完整佔位時,保留原型別(例如數字 / bool)。
    if len(matches) == 1 and matches[0].span() == (0, len(template)):
        return arguments.get(matches[0].group(1))

    def repl(match):
        val = arguments.get(match.group(1), "")
        return "" if val is None else str(val)

    return _INPUT_REF.sub(repl, template)


def resolve_args(value, arguments):
    if isinstance(value, str):
        return _resolve_string(value, arguments)
    if isinstance(value, list):
        return [resolve_args(v, arguments) for v in value]
    if isinstance(value, dict):
        return {k: resolve_args(v, arguments) for k, v in value.items()}
    return value


def collect_texts(step_results):
    packed = {}
    for step_id, texts in step_results.items():
        packed[step_id] = texts[0] if len(texts) == 1 else texts
    return json.dumps(packed, ensure_ascii=False, indent=2)


def render_skill_draft(tool_def):
    name = tool_def["name"]
    params = tool_def.get("params") or []
    param_lines = []
    for p in params:
        desc = p.get("description") or p["name"]
        req = "必填" if p.get("required") else "選填"
        param_lines.append(f"- `{p['name']}`: {req}，{desc}")
    if not param_lines:
        param_lines.append("- 這個工具不需要額外參數。")

    desc = (tool_def.get("description") or "").strip() or f"需要整體彙整時可使用 `{name}`。"
    return "\n".join([
        f"當需求符合以下描述時，優先使用 `{name}`：{desc}",
        "",
        "使用規則：",
        f"- 若需求是整體彙整或多步查詢，優先使用 `{name}`，不要自行拆成多個底層工具呼叫。",
        f"- 若使用者只問單一資料點，且不需要整體整理，優先考慮更直接的底層工具，不要使用 `{name}`。",
        "- 若工具回傳多個區塊，先整理成精簡摘要，再依需求補充細節。",
        "",
        "參數：",
        *param_lines,
    ])
