"""
自訂工具(角色 B:轉接器)—— 把一個 HTTP API 包成 MCP 工具。

一個自訂工具定義:
    name / description
    method        GET | POST
    url_template  可含 {參數},例如 https://api.x.com/weather?q={city}
    headers       靜態 headers(json),可放金鑰,例如 {"Authorization": "Bearer ..."}
    params        參數清單,例如 [{"name":"city","type":"string","required":true,"description":"城市"}]

呼叫時的參數對應規則(直覺、夠用):
    · 出現在 url_template 裡的 {參數} → 代入網址(自動 URL-encode)
    · 其餘參數 → GET 放 query string;POST 放 JSON body
"""

import json
import urllib.parse

import httpx


def build_schema(params):
    """把參數清單轉成給 LLM 看的 inputSchema。"""
    props, required = {}, []
    for p in params or []:
        spec = {"type": p.get("type", "string")}
        if p.get("description"):
            spec["description"] = p["description"]
        props[p["name"]] = spec
        if p.get("required"):
            required.append(p["name"])
    return {"type": "object", "properties": props, "required": required}


async def execute(tool_def, arguments):
    """實際去打這個 API,回傳字串結果。"""
    arguments = arguments or {}
    url = tool_def["url_template"]

    # 1) 代入 URL 裡的 {參數}
    used = set()
    for k, v in arguments.items():
        placeholder = "{" + k + "}"
        if placeholder in url:
            url = url.replace(placeholder, urllib.parse.quote(str(v)))
            used.add(k)

    # 2) 其餘參數 → query(GET)或 body(POST)
    rest = {k: v for k, v in arguments.items() if k not in used}
    headers = tool_def.get("headers") or {}
    method = (tool_def.get("method") or "GET").upper()

    # 有 body 的方法把其餘參數放 JSON body;其餘放 query
    body_methods = {"POST", "PUT", "PATCH"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        if method in body_methods:
            resp = await client.request(method, url, json=rest or None, headers=headers)
        else:
            resp = await client.request(method, url, params=rest or None, headers=headers)

    resp.raise_for_status()

    # 3) 回傳:是 JSON 就排版,否則原文
    if "json" in resp.headers.get("content-type", ""):
        try:
            return json.dumps(resp.json(), ensure_ascii=False, indent=2)
        except Exception:
            pass
    return resp.text
