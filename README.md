# 🧩 MCP Hub

**一個本機自架的 MCP 聚合器**——Claude 只連 **一個** Hub,Hub 幫你把多個下游 MCP server 的工具合併曝露出來,並附一個網頁管理台統一整理、加密金鑰、記錄每次呼叫。

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

有 Hub:Claude 只連 Hub,其餘都在管理台裡開關、歸類、看 log。

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
- **可選人工確認**:把危險工具標為「需確認」,呼叫時走 in-chat 確認或票券核准。
- **金鑰靜態加密**:bearer token / env / headers / OAuth token 以 Fernet 加密存 SQLite,金鑰另存 `.secret_key`(0600 權限)。
- **健康監測**:背景定時檢查下游,綠燈 / 紅燈一目了然,支援一鍵「全部檢查」。
- **呼叫記錄**:每次經 Hub 的工具呼叫都入庫,管理台可分頁、篩選、只看錯誤、自動更新。
- **匯入 / 匯出**:貼上 mcpServers JSON 或匯入 Claude 設定;可匯出可攜設定。
- **MCP Skill 調適**:Claude 可透過 `skill_workbench` 查看、試跑、驗證並儲存複合工具的 Skill,管理台負責預覽與 ZIP 匯出。

---

## 快速開始

```bash
./start.sh
```

自動建 `.venv`、裝依賴、開管理台 **http://localhost:8765**,並印出接入 Claude 的指令:

```bash
claude mcp add my-hub -e PYTHONPATH="<專案路徑>" -- "<專案>/.venv/bin/python" -m gateway.hub_server
```

接上後,Claude 的工具清單就會出現 Hub 聚合的所有下游工具(如 `orgpulse__…`)。

> Docker 部署與容器內接 Claude 的方式見 **[DEPLOY.md](DEPLOY.md)**。

---

## 兩個角色

| 角色 | 指令 | 說明 |
|------|------|------|
| 管理台(網頁) | `python -m gateway.web` | 瀏覽器管理下游、工具、看 logs |
| 聚合器(給 Claude) | `python -m gateway.hub_server` | 以 stdio 被 Claude 啟動,曝露合併後的工具 |

兩者共用同一份 `actions.db`,改完即時生效。

```
Claude ─► hub_server(讀 DB、握下游連線、轉發、記 log)  ─┐
                                                        ├─► actions.db
瀏覽器 ─► web 管理台(加 / 開關 server、看 log)          ─┘
```

---

## 專案結構

```
gateway/
├── web.py          管理台(FastAPI + Jinja2)
├── hub_server.py   對 Claude 的聚合 MCP Server(stdio)
├── hub.py          單次連線 helper(開 session / 抓工具 / 健檢)
├── pool.py         下游常駐連線池(actor-per-connection,處理 anyio cancel-scope)
├── store.py        SQLite 狀態儲存(下游 / 工具 / log / 分類 / OAuth)
├── crypto.py       金鑰靜態加密(Fernet)
├── custom.py       自訂工具:把 HTTP API 包成 MCP 工具
├── openapi.py      OpenAPI 規格匯入
├── directory.py    公開 API 目錄搜尋(APIs.guru)
├── config.py       共用設定(可用環境變數覆寫)
└── auth.py         OAuth 骨架
tests/test_smoke.py 冒煙測試(標準庫 unittest,免裝 pytest)
start.sh · Dockerfile · docker-compose.yml · DEPLOY.md
```

---

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `MCP_HUB_HOST` | `127.0.0.1` | 管理台監聽位址(Docker 內設 `0.0.0.0`) |
| `MCP_HUB_PORT` | `8765` | 管理台 port |
| `MCP_HUB_DB` | `actions.db` | SQLite 路徑 |
| `MCP_HUB_KEY` | `.secret_key` | 加密金鑰檔路徑 |

---

## 開發

```bash
./.venv/bin/python -m unittest tests.test_smoke -v
```

冒煙測試涵蓋 store CRUD、金鑰加解密、解析 helper、每頁 GET 200、以及**路由防呆**(自動偵測參數路由遮蔽字面路由)。測試用臨時 DB / 金鑰,不污染正式資料。

---

## 安全備註

- 管理台**沒有登入**,靠「只綁 `127.0.0.1`」保護——單人本機用足夠;要開遠端才需要加驗證。
- `.secret_key` 是解開 DB 內密鑰的金鑰,**請單獨備份**;遺失則 token 解不回來。`.gitignore` 已擋掉它與 `actions.db`。
- 匯出設定含本機絕對路徑(stdio 下游),搬機器可能要改(匯出檔內已附 `_note` 提醒)。

---

## 尚未完成(Roadmap)

- OAuth 2.1 授權流程(目前為骨架,需真實 OAuth 服務才跑得完)
- 管理台登入驗證(供遠端部署)
- Hub 以 HTTP 對外(讓 client 貼一個 URL 就連,免 stdio)
