"""
共用設定 —— MCP Server(server.py)和核准網站(web.py)都讀這裡,
確保兩邊講的是同一個 port、同一個資料庫。
"""

import os
from pathlib import Path

# 核准網站要監聽的位址
# 預設只綁本機(安全);Docker 內需綁 0.0.0.0 才能被 host 連到 → 用 MCP_HUB_HOST 覆寫。
HOST = os.environ.get("MCP_HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_HUB_PORT", "8765"))
BASE_URL = f"http://localhost:{PORT}"

# 兩個程序共用的狀態儲存(SQLite 檔案,放專案根目錄)
# 可用環境變數 MCP_HUB_DB 覆寫(測試會指到臨時檔,避免污染正式 actions.db)
DB_PATH = Path(os.environ.get("MCP_HUB_DB") or (Path(__file__).parent.parent / "actions.db"))

# 靜態加密的金鑰檔(0600 權限、不進版控)。放在 DB 旁邊,但兩者要分開保管:
# 拿到 DB 但沒拿到這把金鑰 → 解不開密鑰欄位。可用 MCP_HUB_KEY 覆寫。
SECRET_KEY_PATH = Path(os.environ.get("MCP_HUB_KEY") or (DB_PATH.parent / ".secret_key"))

# JSON API 的存取 token(給原生 client 用,見 specs/001-json-api)。
# 沒設就等於不開放 API —— 網頁管理台照常運作,但 /api/* 一律拒絕。
# 由原生 app 在啟動後端子程序時隨機產生並以環境變數傳入,不落地存檔。
API_TOKEN = os.environ.get("MCP_HUB_API_TOKEN", "")

# 管理台背景健康檢查的間隔(秒)
HEALTH_INTERVAL = 30

# 單一下游操作(連線 initialize / list_tools / call_tool)的逾時(秒)
# 防止某台下游「連得上但不回應」時卡住整個 Hub。
DOWNSTREAM_TIMEOUT = 20
