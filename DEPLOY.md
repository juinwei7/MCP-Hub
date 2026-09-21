# 部署 MCP Hub

Hub 有兩個角色:
- **管理台**:`gateway.web`,提供網頁介面與給原生 app 用的 JSON API。
- **聚合器**:`gateway.hub_server`,以 **stdio** 被 Claude 啟動,把所有下游工具合併曝露。

兩者共用同一份 `actions.db`。

---

## 方式 A:原生 app(推薦)

```bash
cd macos && make dist
open "build/MCP Hub.app"
```

app 會自己啟動並監督後端,不需要開終端機。產出的 `.app` 內附 Python runtime,
複製到任何路徑都能執行。資料放在 `~/Library/Application Support/MCP Hub/`。

接入 Claude 的指令在 app 裡看得到,也可以手動:

```bash
claude mcp add my-hub \
  -e PYTHONPATH="<專案路徑>" \
  -e MCP_HUB_DB="$HOME/Library/Application Support/MCP Hub/actions.db" \
  -e MCP_HUB_KEY="$HOME/Library/Application Support/MCP Hub/.secret_key" \
  -- "<專案>/.venv/bin/python" -m gateway.hub_server
```

> **兩邊要指向同一個 DB。** `hub_server` 是 Claude 獨立啟動的,不經過 app,
> 所以那兩個環境變數不能省 —— 少了它們,聚合器會讀到另一份空的資料庫,
> 你在 app 裡的設定一個都不會生效。

系統通知與開機自動啟動需要 Developer ID 簽章(`cd macos && make sign`);
沒有憑證時 app 仍可使用,狀態改由選單列顯示。

---

## 方式 B:直接跑後端(開發、或不想用 app 時)

```bash
./start.sh
```

建 `.venv` → 裝依賴 → 開管理台(http://localhost:8765),並印出接入指令。
這是開發時最方便的方式,也是 Linux 目前唯一的選擇(沒有原生 Linux app)。

自訂 port:`MCP_HUB_PORT=9000 ./start.sh`

> **port 不建議亂改。** OAuth 下游的 redirect_uri 綁在 port 上,換了會讓既有
> 授權失效,需要重新授權。後端啟動時會警告。

---

## stdio 下游的執行環境

stdio 下游是在**你的機器上**被啟動的,所以它需要的執行環境必須先裝好:

| 下游類型 | 需要 |
|---|---|
| `mcp-server-git` 之類 | `git` |
| `npx -y <套件>` 之類 | Node.js |

沒裝的話健康檢查會直接說,例如
`[Errno 2] No such file or directory: 'npx'` —— 看到這種訊息就是缺執行環境,
不是 Hub 的問題。

---

## 安全備註

- 管理台**沒有登入**,靠「只綁 `127.0.0.1`」保護 —— 單人本機用足夠。要開遠端才需要加驗證。
  JSON API 另有 token 驗證(原生 app 啟動時隨機產生,不落地)。
- `.secret_key` 是解開 DB 內密鑰的金鑰,**請單獨備份**;遺失則 `actions.db` 裡的 token 解不回來。
  權限在 macOS / Linux 是 0600,Windows 用 ACL 限制。
- 匯出的設定含本機絕對路徑(stdio 下游),搬機器可能要改(匯出檔內已有 `_note` 提醒)。
