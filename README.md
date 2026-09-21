# 🧩 MCP Hub

**一個本機自架的 MCP 聚合器**——Claude 只連 **一個** Hub,Hub 幫你把多個下游 MCP server 的工具合併曝露出來,並附一個原生 app 統一整理、加密金鑰、記錄每次呼叫。

> 定位:底層基礎設施,重點是**便利性**。各自部署(每個人跑自己的 Hub),不經手別人的流量或金鑰。

---

## 為什麼要它

沒有 Hub:每接一個 MCP server,就要在 Claude 設定裡多一段,金鑰散落各處、難以總覽。

```
Claude ── redmine MCP
       ── git MCP
       ── fetch MCP
       ── 自訂 API…
```

有 Hub:Claude 只連 Hub,其餘都在原生 app 裡開關、歸類、看 log。

```
Claude ── MCP Hub ── redmine MCP(http)
                  ── git MCP(stdio)
                  ── fetch MCP(stdio)
                  ── 自訂 API(把 HTTP API 包成工具)
```

Hub 的雙角色:**對 Claude 是 MCP Server;對下游是 MCP Client**(連上就自動抓工具)。

---

## 功能

- **聚合下游**:同時支援遠端 **HTTP** 與本機 **stdio** 兩種 MCP server,工具以 `下游__工具名` 命名空間合併曝露。
- **自訂工具**:把任意 HTTP API 直接包成 MCP 工具(填 URL / 參數 / headers),或從 **OpenAPI** 規格匯入;可分類、批次套用金鑰 headers。
- **原生 app**:macOS 的 SwiftUI app,七個分頁涵蓋全部功能,不需要瀏覽器。
- **可選人工確認**:把危險工具標為「需確認」,呼叫時走 in-chat 確認或票券核准。
- **金鑰靜態加密**:bearer token / env / headers / OAuth token 以 Fernet 加密存 SQLite,金鑰另存 `.secret_key`(0600 權限)。
- **健康監測**:背景定時檢查下游,綠燈 / 紅燈一目了然,支援一鍵「全部檢查」。
- **呼叫記錄**:每次經 Hub 的工具呼叫都入庫,app 可分頁、篩選、只看錯誤。
- **匯入 / 匯出**:貼上 mcpServers JSON 或匯入 Claude 設定;可匯出可攜設定。
- **MCP Skill 調適**:Claude 可透過 `skill_workbench` 查看、試跑、驗證並儲存複合工具的 Skill,app 負責預覽與 ZIP 匯出。

---

## 快速開始

```bash
./start.sh
```

自動建 `.venv`、裝依賴、啟動後端 **http://localhost:8765**,並印出接入 Claude 的指令:

```bash
claude mcp add my-hub -e PYTHONPATH="<專案路徑>" -- "<專案>/.venv/bin/python" -m gateway.hub_server
```

接上後,Claude 的工具清單就會出現 Hub 聚合的所有下游工具(如 `orgpulse__…`)。

> 其他部署方式與 stdio 下游的執行環境需求見 **[DEPLOY.md](DEPLOY.md)**。

### macOS 原生 app

日常使用就是這個 —— 不用記得開、不用佔終端機、選單列直接看得到狀態。

```bash
cd macos && make dist
open "build/MCP Hub.app"
```

產出的 `.app` 內附 Python runtime,複製到任何路徑都能執行,不需要先裝 Python。
> 系統通知與開機自動啟動需要 Apple Developer ID 簽章(`make sign`);
> 沒有憑證時 app 仍可使用,狀態改由選單列顯示。

---

## 兩個角色

| 角色 | 指令 | 說明 |
|------|------|------|
| 後端 | `python -m gateway.web` | JSON API + 背景健康檢查;原生 app 會自己啟動它 |
| 聚合器(給 Claude) | `python -m gateway.hub_server` | 以 stdio 被 Claude 啟動,曝露合併後的工具 |

兩者共用同一份 `actions.db`,改完即時生效。

```
Claude ─► hub_server(讀 DB、握下游連線、轉發、記 log)  ─┐
                                                        ├─► actions.db
原生 app ─► JSON API(加 / 開關 server、看 log)          ─┘
```

---

## 專案結構

```
gateway/
├── web.py          後端(FastAPI):掛 JSON API、健康檢查、OAuth callback
├── hub_server.py   對 Claude 的聚合 MCP Server(stdio)
├── hub.py          單次連線 helper(開 session / 抓工具 / 健檢)
├── pool.py         下游常駐連線池(actor-per-connection,處理 anyio cancel-scope)
├── store.py        SQLite 狀態儲存(下游 / 工具 / log / 分類 / OAuth)
├── crypto.py       金鑰靜態加密(Fernet)
├── custom.py       自訂工具:把 HTTP API 包成 MCP 工具
├── openapi.py      OpenAPI 規格匯入
├── directory.py    公開 API 目錄搜尋(APIs.guru)
├── config.py       共用設定(可用環境變數覆寫)
├── auth.py         OAuth 2.1 + PKCE 授權
├── api.py          JSON API:下游、工具、記錄、待確認、OAuth
├── api_tools.py    JSON API:自訂工具、複合工具、分類
└── api_import.py   JSON API:匯入、OpenAPI、目錄、匯出
macos/              macOS 原生 app(SwiftUI;後端仍是上面那套 Python)
tests/              冒煙測試 + API 測試(標準庫 unittest,免裝 pytest)
start.sh · DEPLOY.md
```

---

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `MCP_HUB_HOST` | `127.0.0.1` | 後端監聽位址(預設只綁本機) |
| `MCP_HUB_PORT` | `8765` | 後端 port(OAuth redirect_uri 綁在上面,不建議改) |
| `MCP_HUB_DB` | `actions.db` | SQLite 路徑 |
| `MCP_HUB_KEY` | `.secret_key` | 加密金鑰檔路徑 |

---

## 開發

```bash
./.venv/bin/python -m unittest discover -s tests   # Python:82 個
cd macos && make test                              # Swift + app 生命週期驗收
```

冒煙測試涵蓋 store CRUD、金鑰加解密、解析 helper、每頁 GET 200、以及**路由防呆**(自動偵測參數路由遮蔽字面路由)。API 測試涵蓋每個端點的成功與失敗路徑,並驗證密鑰不會出現在任何回應中。測試用臨時 DB / 金鑰,不污染正式資料。

`make test` 會跑 Swift 單元測試,以及實際啟動 app、殺掉程序、檢查有無殘留的端對端驗收。

每次 push 由 [CI](.github/workflows/ci.yml) 在 **macOS / Windows / Linux** 三個平台跑同一套測試,
並從 `requirements.txt` 從零安裝 —— 「本機剛好裝過」的依賴會當場被抓出來。
CI 另外實機驗證金鑰檔的存取權限(POSIX 檢查權限位元,Windows 檢查 ACL)。

---

## 安全備註

- JSON API 需要 token(原生 app 啟動時隨機產生,不落地),且只綁 `127.0.0.1`。
- `.secret_key` 是解開 DB 內密鑰的金鑰,**請單獨備份**;遺失則 token 解不回來。`.gitignore` 已擋掉它與 `actions.db`。權限在 macOS / Linux 設為 0600,在 Windows 改用 ACL 限制(`icacls`)—— `os.chmod` 在 Windows 只認 read-only 旗標,照抄等於沒保護。
- 匯出設定含本機絕對路徑(stdio 下游),搬機器可能要改(匯出檔內已附 `_note` 提醒)。

---

## 尚未完成(Roadmap)

- OAuth client 預先註冊(無 DCR 服務的 client_id / secret 設定)
- 遠端存取的驗證設計(目前只綁本機)
- Hub 以 HTTP 對外(讓 client 貼一個 URL 就連,免 stdio)
- Windows 原生 app(共用 `gateway/api.py`,只需重做介面)
