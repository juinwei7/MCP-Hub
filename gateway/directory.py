"""
公開 API 目錄 —— 接 APIs.guru(唯一開放且機器可讀的 OpenAPI 目錄)。

流程:搜尋 → 選一個 → 拿到它的 spec URL → 交給現有的匯入預覽流程(勾選端點 → 生成工具)。

APIs.guru 的 list.json 很大(~9MB),所以抓一次就快取在記憶體(process 生命週期 + TTL)。
"""

import time

import httpx

_LIST_URL = "https://api.apis.guru/v2/list.json"
_TTL = 3600  # 秒
_cache = {"items": None, "ts": 0.0}


async def _load():
    """抓 + 攤平 APIs.guru 清單,快取起來。回傳 list[{id,title,provider,description,spec_url}]。"""
    if _cache["items"] is not None and (time.time() - _cache["ts"] < _TTL):
        return _cache["items"]

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        resp = await client.get(_LIST_URL)
        resp.raise_for_status()
        data = resp.json()

    items = []
    for api_id, entry in data.items():
        versions = entry.get("versions", {}) or {}
        pref = entry.get("preferred")
        v = versions.get(pref) or (next(iter(versions.values()), None) if versions else None)
        if not v:
            continue
        info = v.get("info", {}) or {}
        spec_url = v.get("swaggerUrl") or v.get("swaggerYamlUrl") or ""
        if not spec_url:
            continue
        desc = info.get("description", "") or ""
        items.append({
            "id": api_id,
            "title": info.get("title", api_id),
            "provider": info.get("x-providerName", ""),
            "description": desc[:160].replace("\n", " "),
            "spec_url": spec_url,
        })

    items.sort(key=lambda x: x["title"].lower())
    _cache["items"] = items
    _cache["ts"] = time.time()
    return items


async def search(query, limit=60):
    """依關鍵字搜尋(比對 id / 標題 / 描述)。空字串回前 limit 筆。"""
    items = await _load()
    q = (query or "").lower().strip()
    if not q:
        return items[:limit], len(items)
    hits = [
        it for it in items
        if q in it["id"].lower() or q in it["title"].lower() or q in it["description"].lower()
    ]
    return hits[:limit], len(hits)
