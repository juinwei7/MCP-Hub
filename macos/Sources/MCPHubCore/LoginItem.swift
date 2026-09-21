import Foundation
import ServiceManagement

/// 開機自動啟動。
///
/// 跟通知一樣需要正式的 bundle —— 開發時直接跑的執行檔不適用,此時整個功能停用
/// 而不是報錯。
enum LoginItem {

    static var available: Bool { Bundle.main.bundleIdentifier != nil }

    static var isEnabled: Bool {
        guard available else { return false }
        return SMAppService.mainApp.status == .enabled
    }

    /// 回傳 nil 代表成功,否則是可以顯示給使用者看的原因。
    @discardableResult
    static func set(_ enabled: Bool) -> String? {
        guard available else { return "需要以 app 形式執行才能設定開機啟動。" }
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            return nil
        } catch {
            // 使用者可能在「系統設定 → 一般 → 登入項目」把它擋掉了
            return "設定失敗:\(error.localizedDescription)"
        }
    }
}
