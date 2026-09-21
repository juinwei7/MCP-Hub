"""
測試共用的環境設定 —— 每個測試檔都必須在 import gateway 之前先 import 這個模組。

為什麼要集中:`gateway.config` 在 import 當下就把環境變數讀成模組常數,所以
「誰先 import gateway,誰就決定了整個行程的設定」。若每個測試檔各設一次,
後 import 的那份會被默默忽略,測試結果就取決於執行順序 —— 單獨跑會過、
一起跑會爆。集中在這裡設定一次,順序就不再有影響。

臨時檔由 atexit 統一清除,不能放在各自的 tearDownModule:那會在整個測試
跑完之前就把別的模組還在用的 DB 刪掉。
"""
import atexit
import os
import tempfile

DB_PATH = tempfile.mkstemp(prefix="mcphub_test_", suffix=".db")[1]
KEY_PATH = tempfile.mkstemp(prefix="mcphub_key_", suffix="")[1]
os.remove(KEY_PATH)   # 讓 crypto 自己產生

os.environ["MCP_HUB_DB"] = DB_PATH
os.environ["MCP_HUB_KEY"] = KEY_PATH
os.environ["MCP_HUB_API_TOKEN"] = "test-token-abc"

API_TOKEN = os.environ["MCP_HUB_API_TOKEN"]
AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


@atexit.register
def _cleanup():
    for f in (DB_PATH, KEY_PATH):
        try:
            os.remove(f)
        except OSError:
            pass
