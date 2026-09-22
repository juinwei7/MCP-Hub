# 🧩 MCP Hub

**本機自架的 MCP 聚合器。** Claude 只連 **一個** Hub,Hub 把多台下游 MCP server 的工具合併曝露出來;原生 app 負責開關、歸類、加密金鑰、看每一次呼叫的記錄。

> 定位:基礎設施,重點是便利性。各自部署 —— 你跑你自己的 Hub,不經手別人的流量或金鑰。

[![CI](https://github.com/juinwei7/MCP-Hub/actions/workflows/ci.yml/badge.svg)](https://github.com/juinwei7/MCP-Hub/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## 為什麼要它

沒有 Hub:每接一個 MCP server 就要在 Claude 設定裡多一段,金鑰散落各處、難以總覽。

```
Claude ── redmine MCP
       ── git MCP
       ── fetch MCP
       ── 自訂 API…
```

有 Hub:Claude 只連 Hub,其餘都在 app 裡開關、歸類、看 log。

```
Claude ── MCP Hub ── redmine MCP(http)
                  ── git MCP(stdio)
                  ── fetch MCP(stdio)
                  ── 自訂 API(把 HTTP API 包成工具)
```

Hub 的雙角色:**對 Claude 是 MCP Server;對下游是 MCP Client**(連上就自動抓工具)。

---

## 安裝

到 [Releases](https://github.com/juinwei7/MCP-Hub/releases) 下載,不需要先裝 Python 或 .NET —— runtime 都內附了。

| 平台 | 檔案 | 需求 |
|---|---|---|
| macOS(Apple Silicon) | `MCP-Hub-<版本>-arm64.dmg` | macOS 14+ |
| Windows(x64) | `MCP-Hub-<版本>-win-x64.zip` | Windows 10 1809+ |

**第一次打開會被系統擋下來**,因為沒有付費的程式碼簽章:

- macOS 會說「已損毀」。它沒有損毀 —— 是沒有可驗證的開發者身分。拖進 `Applications` 後執行一次:
  ```bash
  xattr -dr com.apple.quarantine "/Applications/MCP Hub.app"
  ```
- Windows 的 SmartScreen 會攔:按「其他資訊」→「仍要執行」。

Intel Mac 沒有預先打包的版本(GitHub 的免費 Intel runner 已退役),要的話在 Intel 機器上自己建:`cd macos && make dist`。

### 接進 Claude

開啟 app,從系統匣 / 選單列圖示選 **「複製接入 Claude 的指令」**,貼到終端機執行一次。

> ⚠️ 不要自己手打那段指令。它帶著 `MCP_HUB_DB` 與 `MCP_HUB_KEY` 兩個環境變數 ——
> 少了它們,聚合器會去讀另一份空的資料庫,你在 app 裡的設定一個都不會生效,
> 而且**不會有任何錯誤訊息**,只是「工具怎麼都沒出現」。

---

## 功能

- **聚合下游** —— 遠端 **HTTP** 與本機 **stdio** 兩種 MCP server,工具以 `下游__工具名` 命名空間合併曝露。
- **自訂工具** —— 把任意 HTTP API 包成 MCP 工具(URL / 參數 / headers),或從 **OpenAPI** 規格匯入。
- **複合工具** —— 依序串起多個工具,Claude 呼叫一次就拿到全部結果。
- **總開關** —— 一個動作讓整個 Hub 停供。它**不動**每台下游的啟用狀態,恢復時你原本的開關還在。
- **人工確認** —— 把危險工具標成「需確認」,呼叫時走對話內確認或票券核准。
- **金鑰靜態加密** —— bearer token / env / headers / OAuth token 以 Fernet 加密存 SQLite,金鑰另存 `.secret_key`。
- **健康監測** —— 背景定時檢查下游,狀態以列首色條呈現,支援一鍵「全部檢查」。
- **呼叫記錄** —— 每次經 Hub 的呼叫都入庫,可分頁、只看錯誤。
- **匯入 / 匯出** —— 讀 Claude Desktop / Claude Code 的設定匯入,或貼 mcpServers JSON;匯出預設**不含**金鑰。

### 兩個 app 的差異

後端完全共用,介面各自原生。目前 Windows 少幾樣:

| | macOS | Windows |
|---|:---:|:---:|
| 下游 / 工具 / 記錄 / 待確認 | ✅ | ✅ |
| 自訂工具、複合工具 | ✅ | ✅ |
| 匯入下游、分類、匯出 | ✅ | ✅ |
| 總開關(選單列 + 視窗) | ✅ | ✅ |
| OAuth 授權 | ✅ | ✅ |
| 全域快捷鍵(⌃⌥⌘P 切換暫停) | ✅ | ❌ |
| OpenAPI 匯入、服務目錄 | ✅ | ❌ |
| Skill 調適與匯出 | ✅ | ❌ |
| 系統通知 | 需簽章 | ❌ |

> Windows app 的每個畫面都由 CI 渲染成 PNG 驗證過版面與深淺兩套配色,
> 但**還沒有人在真的 Windows 桌面上完整操作過一輪** —— 互動行為只經過編譯器與型別檢查。
> 遇到問題請附上 `%LOCALAPPDATA%\MCP Hub\crash.log`。

---

## 從原始碼跑

```bash
./start.sh
```

建 `.venv`、裝依賴、啟動後端。**這條路沒有圖形介面** —— 適合沒有桌面的機器,或開發後端時要看即時 log。其他部署方式見 **[DEPLOY.md](DEPLOY.md)**。

自己建 app:

```bash
cd macos   && make dist    # 產出 build/MCP Hub.app(內附 Python)
cd windows && dotnet publish MCPHub.App/MCPHub.App.csproj -c Release -r win-x64 --self-contained
             pwsh -File scripts/bundle-backend.ps1 -Dest MCPHub.App/bin/Release/net8.0-windows/win-x64/publish
```

---

## 兩個角色

| 角色 | 指令 | 說明 |
|------|------|------|
| 後端 | `python -m gateway.web` | JSON API + 背景健康檢查;app 會自己啟動它 |
| 聚合器(給 Claude) | `python -m gateway.hub_server` | 以 stdio 被 Claude 啟動,曝露合併後的工具 |

兩者共用同一份 `actions.db`,改完即時生效 —— 這也是那兩個環境變數非帶不可的原因。

```
Claude ─► hub_server(讀 DB、握下游連線、轉發、記 log)  ─┐
                                                        ├─► actions.db
原生 app ─► JSON API(加 / 開關 server、看 log)          ─┘
```

---

## 專案結構

```
gateway/            後端(Python)—— 兩個 app 共用
├── web.py          FastAPI:掛 JSON API、健康檢查、OAuth callback
├── hub_server.py   對 Claude 的聚合 MCP Server(stdio)
├── hub.py          單次連線 helper(開 session / 抓工具 / 健檢)
├── pool.py         下游常駐連線池(actor-per-connection,處理 anyio cancel-scope)
├── store.py        SQLite 狀態儲存(下游 / 工具 / log / 分類 / OAuth / 設定)
├── crypto.py       金鑰靜態加密(Fernet)
├── custom.py       自訂工具:把 HTTP API 包成 MCP 工具
├── composite.py    複合工具:多步驟串接
├── openapi.py      OpenAPI 規格匯入
├── auth.py         OAuth 2.1 + PKCE 授權
└── api*.py         JSON API(下游 / 工具 / 匯入匯出)
macos/              SwiftUI app + 打包腳本(make dist / make sign)
windows/            WPF app(MCPHub.App)+ 可測試的 Core(MCPHub.Core)
tests/              Python 測試(標準庫 unittest,免裝 pytest)
```

---

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `MCP_HUB_HOST` | `127.0.0.1` | 後端監聽位址(預設只綁本機) |
| `MCP_HUB_PORT` | `8765` | 後端 port(OAuth redirect_uri 綁在上面,不建議改) |
| `MCP_HUB_DB` | `actions.db` | SQLite 路徑 |
| `MCP_HUB_KEY` | `.secret_key` | 加密金鑰檔路徑 |

app 會把前兩個以外的都指向自己的資料目錄:
macOS `~/Library/Application Support/MCP Hub/`、Windows `%LOCALAPPDATA%\MCP Hub\`。

---

## 開發

```bash
./.venv/bin/python -m unittest discover -s tests    # Python:153
cd macos   && make test                             # Swift 16 + 生命週期驗收 10
cd windows && dotnet test MCPHub.Core.Tests/...     # .NET:24(需 Windows)
```

Python 測試涵蓋 store CRUD、金鑰加解密、每個 API 端點的成功與失敗路徑、**密鑰不會出現在任何回應中**,以及路由防呆(自動偵測參數路由遮蔽字面路由)。用臨時 DB / 金鑰,不污染正式資料。

`make test` 會實際啟動 app、殺掉程序、檢查有無殘留 —— 孤兒程序是這個專案真實發生過的問題。

### CI

每次 push 跑七個 job:

- 後端測試在 **Linux / macOS / Windows** 三平台各跑一次,而且從 `requirements.txt` **從零安裝** ——「本機剛好裝過」的依賴會當場被抓出來(`jinja2` 就是這樣現形的)。
- 金鑰檔權限**實機驗證**:POSIX 檢查權限位元,Windows 解析 ACL。
- macOS app 的生命週期驗收。
- **Windows app 把每個畫面渲染成 PNG**。開發機是 macOS,這個 app 在本機執行不了 —— 編譯器抓得到型別錯誤,抓不到版面跑掉或深色下看不見。用 WPF 的 `Measure`/`Arrange` + `RenderTargetBitmap`,不需要桌面。

推一個 `v*` 的 tag 就發版:驗證 → 打包兩個平台 → 建 GitHub Release。

---

## 安全備註

- JSON API 需要 token(app 啟動時隨機產生,不落地),且只綁 `127.0.0.1`。
- `.secret_key` 是解開 DB 內密鑰的金鑰,**請單獨備份**;遺失則 token 解不回來。`.gitignore` 已擋掉它與 `actions.db`。權限在 macOS / Linux 設為 0600,Windows 改用 ACL(`icacls`)—— `os.chmod` 在 Windows 只認 read-only 旗標,照抄等於沒保護。
- OAuth 授權一律開系統瀏覽器,不用內嵌 webview:webview 看起來比較整合,但使用者沒辦法確認網址列,那正是釣魚最愛的形狀。
- 匯出設定預設**不含**金鑰。匯出檔很容易被順手貼到別的地方。
- 匯出含本機絕對路徑(stdio 下游),搬機器可能要改(匯出檔內已附 `_note` 提醒)。

---

## 尚未完成

- Windows:全域快捷鍵、OpenAPI 匯入、服務目錄、Skill 調適、系統通知
- Windows app 的實機互動驗證(目前只有 CI 的版面渲染)
- 程式碼簽章(macOS notarization / Windows Authenticode)—— 兩者都要付費憑證
- Intel macOS 的預先打包版本
- OAuth client 預先註冊(無 DCR 服務的 client_id / secret 設定)
- 遠端存取的驗證設計(目前只綁本機)
- Hub 以 HTTP 對外(讓 client 貼一個 URL 就連,免 stdio)

---

## License

[MIT](LICENSE)
