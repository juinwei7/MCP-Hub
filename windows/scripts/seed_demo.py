"""
種一組示範資料,讓 CI 渲染出來的畫面真的看得到設計語彙。

空資料庫只渲染得出空狀態 —— 而這份設計的主張全在「列」上:
狀態是列首的色條、等寬字代表機器產生的值、異常時第二行換成系統字的錯誤原因、
待確認著琥珀色。那些都要有資料才看得到。

跑法(MCP_HUB_DB / MCP_HUB_KEY 要指向 app 會用的同一份):
    python windows/scripts/seed_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from gateway import store  # noqa: E402
from gateway.console import use_utf8  # noqa: E402


def add(slug, name, url, *, enabled=True, status="ok", detail="", tools=0,
        auth="none"):
    store.add_server(slug, name, url, auth, None, "http", "", "[]", "{}")
    store.set_server_enabled(slug, enabled)
    store.set_server_status(slug, status, detail)
    if tools:
        # tool_count 是從快取的工具數來的,不是欄位
        store.cache_tools(slug, [(f"{slug}_tool_{i}", f"示範工具 {i}", {})
                                 for i in range(tools)])


def main():
    # Windows 的主控台預設是 cp1252,編不了中文 —— 不先切 UTF-8 的話,
    # print 本身就會丟 UnicodeEncodeError。專案裡早就有這個工具,
    # 新腳本忘了用就會再踩一次。
    use_utf8()
    store.init_db()

    # 健康 —— 綠色條,第二行是等寬的網址。用 oauth 讓「OAuth 授權」那顆
    # 按鈕也被渲染到 —— 只有 oauth 的下游才有它
    add("tracker", "Tracker", "https://mcp.example.com/mcp", tools=16,
        auth="oauth")

    # 停用 —— 不給顏色只是變淡。它不是壞掉,不該和異常同一個視覺層級
    add("intranet", "Intranet", "https://admin.example.org/mcp",
        enabled=False, tools=29)

    # 連不上 —— 紅色條,而且第二行從等寬的網址換成系統字的錯誤原因
    add("sandbox", "Sandbox", "http://localhost:8081/mcp",
        status="error", detail="All connection attempts failed", tools=16)

    # 待確認 —— 側邊欄的計數會著琥珀色
    for tool in ("tracker__get_issue_by_id", "confirm_weather"):
        store.create_action(tool, {"demo": True})

    print(f"已種 {len(store.list_servers())} 台下游、"
          f"{len(store.list_actions())} 筆待確認")


if __name__ == "__main__":
    main()
