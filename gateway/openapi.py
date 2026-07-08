"""
OpenAPI / Swagger 匯入 —— 把一份 API 規格轉成一批「自訂工具」定義。

支援:OpenAPI 3(paths + servers)與 Swagger 2(host + basePath)基本情況;JSON 或 YAML。
對應規則:
    路徑參數 /issues/{id}  → 我們的 {id}(execute 會代入網址)
    query 參數            → GET 的 query string
    requestBody 頂層屬性   → POST/PUT/PATCH 的 JSON body
授權:匯入時給一組共用 headers,套到所有生成的工具。

注意(邊角):requestBody 若是 $ref 參照到複雜 schema,這裡只做最佳努力(可能抓不到 body 參數),
生成後可在自訂工具頁手動補。
"""

import json
import re

import httpx
import yaml


def load_spec(text):
    """吃 JSON 或 YAML 文字,回傳 dict。"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        return yaml.safe_load(text)


async def fetch_spec(url):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


# 常見的 OpenAPI / Swagger spec 路徑(自動探測用)
_PROBE_PATHS = [
    "/openapi.json", "/openapi.yaml", "/swagger.json",
    "/v3/api-docs", "/api-docs", "/v2/api-docs",
    "/swagger/v1/swagger.json", "/.well-known/openapi.json",
]


def _looks_like_spec(spec):
    return isinstance(spec, dict) and "paths" in spec and ("openapi" in spec or "swagger" in spec)


async def probe(base_url):
    """
    給一個服務網址,自動試常見 spec 路徑。
    回傳 (found_url, spec, tried_list);找不到則 found_url/spec 為 None。
    """
    base = (base_url or "").strip().rstrip("/")
    candidates, seen = [], set()
    for u in [base] + [base + p for p in _PROBE_PATHS]:   # 先試網址本身(可能已是 spec)
        if u and u not in seen:
            seen.add(u)
            candidates.append(u)
    tried = []
    for url in candidates:
        tried.append(url)
        try:
            spec = load_spec(await fetch_spec(url))
            if _looks_like_spec(spec):
                return url, spec, tried
        except Exception:
            continue
    return None, None, tried


def title_of(spec):
    """spec 的 API 標題(匯入時當分類名的 fallback)。"""
    return ((spec.get("info") or {}).get("title") or "").strip()


def base_url_of(spec):
    """從 spec 推出 base URL。"""
    # OpenAPI 3
    servers = spec.get("servers")
    if servers and isinstance(servers, list) and servers[0].get("url"):
        return servers[0]["url"].rstrip("/")
    # Swagger 2
    host = spec.get("host")
    if host:
        scheme = (spec.get("schemes") or ["https"])[0]
        base = spec.get("basePath", "") or ""
        return f"{scheme}://{host}{base}".rstrip("/")
    return ""


def _param_type(p):
    if "schema" in p and isinstance(p["schema"], dict):
        return p["schema"].get("type", "string")
    return p.get("type", "string")


def operations(spec):
    """列出所有端點。每筆:op_id / method / path / summary / params[]。"""
    out = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters", []) or []
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue

            params = []
            for p in shared + (op.get("parameters", []) or []):
                if not isinstance(p, dict) or "$ref" in p or "name" not in p:
                    continue
                params.append({
                    "name": p["name"],
                    "type": _param_type(p),
                    "required": bool(p.get("required", p.get("in") == "path")),
                    "description": p.get("description", ""),
                })

            # requestBody(OpenAPI 3)頂層屬性
            rb = op.get("requestBody")
            if isinstance(rb, dict):
                schema = (((rb.get("content") or {}).get("application/json") or {}).get("schema") or {})
                required = set(schema.get("required") or [])
                for pname, pspec in (schema.get("properties") or {}).items():
                    params.append({
                        "name": pname,
                        "type": pspec.get("type", "string") if isinstance(pspec, dict) else "string",
                        "required": pname in required,
                        "description": pspec.get("description", "") if isinstance(pspec, dict) else "",
                    })

            out.append({
                "op_id": op.get("operationId") or f"{method}_{path}",
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary") or op.get("description") or "",
                "params": params,
                "tags": op.get("tags", []) or [],
            })
    return out


def sanitize_name(name):
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return s or "tool"


def to_custom_tool(op, base_url, prefix="", shared_headers_json="{}"):
    """把一個 operation 轉成 (name, description, method, url_template, headers_json, params_json)。"""
    name = sanitize_name(f"{prefix}{op['op_id']}")
    url_template = base_url.rstrip("/") + op["path"]  # path 已含 {param}
    params_json = json.dumps(op["params"], ensure_ascii=False)
    return (name, op["summary"], op["method"], url_template, shared_headers_json or "{}", params_json)
