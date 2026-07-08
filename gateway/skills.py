"""Skill validation, packaging, and MCP-driven tuning workflow."""

import io
import json
import re
import zipfile

from gateway import store


def skill_name(name):
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (value or "composite-tool")[:64].rstrip("-")


def starter_skill(tool_def):
    name = skill_name(tool_def["name"])
    description = (tool_def.get("description") or f"Use {tool_def['name']} for its composite workflow.").strip()
    return "\n".join([
        "---",
        f"name: {name}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        "---",
        "",
        f"# {name}",
        "",
        f"Call the composite MCP tool `{tool_def['name']}` once, then synthesize its collected results.",
        "Do not call its underlying tools separately.",
    ])


def validate_skill_markdown(markdown, expected_name=None):
    # HTML <textarea> 送出時會把換行正規化成 CRLF，先統一成 LF 再檢查。
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if not markdown.strip().startswith("---\n"):
        raise ValueError("SKILL.md 缺少 YAML frontmatter")
    parts = markdown.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter 格式不完整")
    frontmatter = parts[1]
    if not re.search(r"(?m)^name:\s*\S+", frontmatter):
        raise ValueError("SKILL.md 缺少 name")
    if not re.search(r"(?m)^description:\s*.+", frontmatter):
        raise ValueError("SKILL.md 缺少 description")
    if expected_name and not re.search(rf"(?m)^name:\s*{re.escape(expected_name)}\s*$", frontmatter):
        raise ValueError(f"Skill name 必須是 {expected_name}")


def skill_zip(name, markdown):
    normalized_name = skill_name(name)
    validate_skill_markdown(markdown, normalized_name)
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{normalized_name}/SKILL.md", markdown)
    return output.getvalue()


def _inspect_payload(tool):
    return {
        "composite": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "params": tool.get("params") or [],
            "steps": tool.get("steps") or [],
            "output": tool.get("output") or "collect",
        },
        "required_skill_name": skill_name(tool["name"]),
        "instructions": [
            "先向使用者確認 Skill 的讀者、觸發情境、語言與輸出格式。",
            f"SKILL.md 只應呼叫複合工具 {tool['name']}，不要拆開呼叫底層步驟。",
            "需要查看真實回傳時，使用 preview 操作試跑。",
            "完成後先用 validate，再用 save 儲存完整 SKILL.md。",
        ],
    }


async def execute_workbench(arguments, run_tool):
    action = arguments.get("action") or "list"
    name = (arguments.get("composite_name") or "").strip()
    if action == "list":
        items = [{"name": t["name"], "description": t.get("description") or ""}
                 for t in store.list_composite_tools() if t.get("enabled")]
        return json.dumps({"composites": items}, ensure_ascii=False, indent=2)

    tool = store.get_composite_tool(name)
    if not tool:
        return json.dumps({"ok": False, "error": f"找不到複合工具 {name}"}, ensure_ascii=False)
    if action == "inspect":
        return json.dumps(_inspect_payload(tool), ensure_ascii=False, indent=2)
    if action == "preview":
        blocks = await run_tool(name, arguments.get("arguments") or {})
        return "\n\n".join(getattr(block, "text", "") for block in blocks)

    markdown = arguments.get("skill_md") or ""
    try:
        validate_skill_markdown(markdown, skill_name(name))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    if action == "validate":
        return json.dumps({"ok": True, "message": "SKILL.md 格式正確，可以儲存。"}, ensure_ascii=False)
    if action == "save":
        store.save_skill_draft(name, markdown, arguments.get("purpose") or "")
        return json.dumps({
            "ok": True,
            "message": "Skill 已儲存，可在 Hub 管理台預覽並下載 ZIP。",
            "url": f"http://localhost:8765/composites/{name}/skill",
        }, ensure_ascii=False)
    return json.dumps({"ok": False, "error": f"不支援的 action: {action}"}, ensure_ascii=False)
