"""
本機檔案參數 —— 讓 AI 用「路徑」把檔案交給下游。

MCP 的工具參數只有 JSON。下游要收檔案只能收 base64 字串,但那串字得由 AI
一字一字打出來:1 MB 的圖就是一百三十多萬個字元,不可行。

Hub 跑在使用者的機器上,讀得到本機檔案,所以由它代勞:
  · 下游在 inputSchema 用 JSON Schema 標準的 `contentEncoding: "base64"` 標出「這欄是檔案內容」
  · 給 AI 看的 schema 把那欄改成「本機檔案的絕對路徑」
  · 轉發時 Hub 照路徑讀檔、換成 base64;呼叫記錄只留路徑,不會塞進幾 MB 的 base64

只走 properties 與 items(物件與陣列),不處理 anyOf / oneOf:那些分支沒辦法
從參數判斷該套哪一個,猜錯就會把一般字串當路徑去讀檔。
"""

import base64
import copy
import os

# 單一檔案上限。下游自己還會再檢查;這裡只是不讓一個誤填的路徑把整個檔案讀進記憶體。
MAX_BYTES = 25 * 1024 * 1024

HINT = "填本機檔案的絕對路徑,MCP Hub 會讀檔轉成 base64 再送出。"


class LocalFileError(Exception):
    """路徑不能用(不是絕對路徑、找不到、太大、讀不到)。訊息直接給 AI 看。"""


def _is_file_field(node):
    return (
        isinstance(node, dict)
        and node.get("type") == "string"
        and str(node.get("contentEncoding", "")).lower() == "base64"
    )


def _children(node):
    """(key, 子 schema) —— 只走 properties 與 items。"""
    props = node.get("properties")
    if isinstance(props, dict):
        for k, v in props.items():
            yield ("properties", k), v
    items = node.get("items")
    if isinstance(items, dict):
        yield ("items", None), items


def has_file_fields(schema):
    if _is_file_field(schema):
        return True
    if not isinstance(schema, dict):
        return False
    return any(has_file_fields(child) for _, child in _children(schema))


def _rewrite(node):
    if _is_file_field(node):
        out = {k: v for k, v in node.items() if k not in ("contentEncoding", "contentMediaType")}
        desc = (out.get("description") or "").rstrip("。. ")
        out["description"] = f"{desc}。{HINT}" if desc else HINT
        return out
    if not isinstance(node, dict):
        return node
    for (where, key), child in list(_children(node)):
        if where == "properties":
            node["properties"][key] = _rewrite(child)
        else:
            node["items"] = _rewrite(child)
    return node


def for_client(schema):
    """給 AI 看的 schema:檔案欄位改成收本機路徑。沒有檔案欄位就原樣回傳。"""
    if not has_file_fields(schema):
        return schema
    return _rewrite(copy.deepcopy(schema))


def read_base64(path):
    p = os.path.expanduser(str(path).strip())
    if not os.path.isabs(p):
        raise LocalFileError(f"「{path}」不是絕對路徑。檔案欄位要填本機檔案的絕對路徑。")
    if not os.path.isfile(p):
        raise LocalFileError(f"找不到檔案:{path}")
    size = os.path.getsize(p)
    if size > MAX_BYTES:
        raise LocalFileError(f"「{path}」有 {size / 1024 / 1024:.1f} MB,超過 Hub 的單檔上限 {MAX_BYTES // 1024 // 1024} MB")
    try:
        with open(p, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except OSError as e:
        raise LocalFileError(f"讀取「{path}」失敗:{e}") from e


def expand(schema, value):
    """照下游原始 schema 把檔案欄位的路徑換成 base64,回傳新的參數(不改原本那份)。"""
    if _is_file_field(schema):
        return read_base64(value) if isinstance(value, str) else value
    if not isinstance(schema, dict):
        return value
    props = schema.get("properties")
    if isinstance(value, dict) and isinstance(props, dict):
        return {k: expand(props[k], v) if k in props else v for k, v in value.items()}
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, dict):
        return [expand(items, v) for v in value]
    return value
