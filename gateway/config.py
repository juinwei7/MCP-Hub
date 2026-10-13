"""
共用設定 —— MCP Server(server.py)和核准網站(web.py)都讀這裡,
確保兩邊講的是同一個 port、同一個資料庫。
"""

import os
from pathlib import Path

# 核准網站要監聽的位址
# 預設只綁本機 —— 管理台沒有登入,這是唯一的保護。要對外開放得明確覆寫。
HOST = os.environ.get("MCP_HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_HUB_PORT", "8765"))
BASE_URL = f"http://localhost:{PORT}"

# 兩個程序共用的狀態儲存(SQLite 檔案,放專案根目錄)
# 可用環境變數 MCP_HUB_DB 覆寫(測試會指到臨時檔,避免污染正式 actions.db)
DB_PATH = Path(os.environ.get("MCP_HUB_DB") or (Path(__file__).parent.parent / "actions.db"))

# 靜態加密的金鑰檔(0600 權限、不進版控)。放在 DB 旁邊,但兩者要分開保管:
# 拿到 DB 但沒拿到這把金鑰 → 解不開密鑰欄位。可用 MCP_HUB_KEY 覆寫。
SECRET_KEY_PATH = Path(os.environ.get("MCP_HUB_KEY") or (DB_PATH.parent / ".secret_key"))

# JSON API 的存取 token(給原生 client 用)。
# 沒設就等於不開放 API —— 網頁管理台照常運作,但 /api/* 一律拒絕。
# 由原生 app 在啟動後端子程序時隨機產生並以環境變數傳入,不落地存檔。
API_TOKEN = os.environ.get("MCP_HUB_API_TOKEN", "")

# 管理台背景健康檢查的間隔(秒)
HEALTH_INTERVAL = 30

# 單一下游操作(連線 initialize / list_tools / call_tool)的逾時(秒)
# 防止某台下游「連得上但不回應」時卡住整個 Hub。
DOWNSTREAM_TIMEOUT = 20

# 建立一條下游連線的上限(OAuth 換票 + TCP + initialize 全部算在內)。
# 這段時間 Claude 是在等我們回 tools/list 或 tools/call,所以不能沒有上限。
CONNECT_TIMEOUT = DOWNSTREAM_TIMEOUT

# 等下游回覆的最後防線。連線池收攤時會把排隊中的請求補回錯誤,這個上限是為了
# 萬一有漏網的請求 —— 寧可回一個錯誤,也不要讓 Claude 等到自己斷線。
# 比單次操作逾時寬鬆,因為請求可能排在同一條連線的其他請求後面。
REPLY_TIMEOUT = DOWNSTREAM_TIMEOUT * 2

# 一次下游呼叫從頭到尾的上限(連線 + 排隊 + 執行 + 斷線重試全部算在內)。
# Claude 給單一工具呼叫的耐心大約一分鐘,超時它會自己放棄 —— 而它放棄的方式
# 是把整條連線判死。與其這樣,不如我們自己先回一個講得清楚的錯誤。
TOTAL_TIMEOUT = 50

# 一次 tools/list 花在「連出去問下游」的總預算(秒,所有下游共用)。
# Claude Code 給 MCP server 的連線預算是 30 秒,逾時的代價是整場工作階段斷線。
# 所以預算用完就改用上一次的快取:給「稍微舊的工具清單」遠比斷線好。
LIST_TOOLS_BUDGET = 15
