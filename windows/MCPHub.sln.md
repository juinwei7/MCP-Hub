# Windows client

macOS 版的對應實作。業務邏輯共用同一套 Python 引擎,這裡只有介面與系統整合。

目前完成的是 **Core**(可自動化驗證的部分):

| 檔案 | 對應 macOS | 說明 |
|---|---|---|
| `HubClient.cs` | `HubClient.swift` | JSON API client,端點與欄位完全相同 |
| `NotificationGate.cs` | `NotificationGate.swift` | 通知去重規則,純邏輯 |
| `BackendSupervisor.cs` | `BackendSupervisor.swift` | 後端程序生命週期,用 Job Object |

**尚未實作**:WinUI 介面、系統匣、Toast 通知、打包。
那些需要實機互動才驗證得了,CI 只能確認編譯與邏輯。

開發者沒有 Windows 機器時,CI 的 `windows-client` job 就是編譯器與測試機。
