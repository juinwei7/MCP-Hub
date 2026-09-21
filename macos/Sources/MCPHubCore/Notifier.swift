import Foundation
import UserNotifications

/// 系統通知 —— 這是原生相對於網頁最實際的價值。
///
/// 網頁管理台只有在你想到要開它的時候才存在。所以票券會躺著沒人處理,下游掛了
/// 也不會有人知道。通知讓 Hub 能在你沒看著它的時候引起你注意。
///
/// 兩個設計重點:
///   1. 不重複通知(約束 C-1)—— 反覆跳通知會讓人直接關掉通知,結果比沒有更糟
///   2. 沒有 bundle 就靜默跳過(約束 C-2)—— UNUserNotificationCenter 在未打包的
///      執行檔上會拋 NSException,而 Swift 攔不住,只能事前擋下來
@MainActor
final class Notifier: NSObject {

    static let approvalCategory = "mcphub.approval"
    static let approveAction = "mcphub.approve"
    static let rejectAction = "mcphub.reject"

    /// 通知需要正式的 bundle identifier。開發時直接跑 swift build 的執行檔沒有,
    /// 此時整個通知功能停用 —— 不是壞掉,是本來就不適用。
    let available: Bool = Bundle.main.bundleIdentifier != nil

    private(set) var authorized = false
    /// 「該不該通知」的規則抽在 NotificationGate,可單獨測試(約束 C-1)。
    private var gate = NotificationGate()

    /// 通知送不出去時要讓呼叫端知道,好改用選單列提示(約束 C-3)。
    var canNotify: Bool { available && authorized }

    /// 核准 / 拒絕的實際動作由 AppState 提供 —— 通知層不碰業務邏輯。
    var onDecision: ((_ actionID: String, _ approve: Bool) -> Void)?

    func start() {
        guard available else {
            log("未打包成 .app(沒有 bundle identifier),通知功能停用")
            return
        }

        let center = UNUserNotificationCenter.current()
        center.delegate = self

        // 通知上直接能決定,不必先開視窗(使用者故事 2)
        let approve = UNNotificationAction(identifier: Self.approveAction,
                                           title: "核准", options: [.authenticationRequired])
        let reject = UNNotificationAction(identifier: Self.rejectAction,
                                          title: "拒絕", options: [.destructive])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: Self.approvalCategory,
                                   actions: [approve, reject],
                                   intentIdentifiers: [], options: [])
        ])

        center.requestAuthorization(options: [.alert, .sound]) { [weak self] granted, error in
            Task { @MainActor in
                self?.authorized = granted
                if let error {
                    self?.log("通知授權失敗:\(error.localizedDescription)")
                } else {
                    self?.log(granted ? "通知已授權" : "使用者未允許通知")
                }
            }
        }
    }

    /// 診斷用 —— 通知失敗的原因(沒 bundle、沒授權、沒簽章)從畫面上看不出來。
    private func log(_ message: String) {
        FileHandle.standardError.write(Data("[Notifier] \(message)\n".utf8))
    }

    // MARK: - 觸發

    /// 依目前狀態決定要送哪些通知。去重規則在 NotificationGate。
    func sync(actions: [HubClient.Action], servers: [HubClient.Server]) {
        for item in gate.evaluate(actions: actions, servers: servers) {
            post(id: item.id, title: item.title, body: item.body,
                 category: item.actionID == nil ? nil : Self.approvalCategory,
                 userInfo: item.actionID.map { ["actionID": $0] } ?? [:])
        }
    }

    // MARK: - 送出

    private func post(id: String, title: String, body: String,
                      category: String? = nil, userInfo: [String: Any] = [:]) {
        guard available, authorized else {
            log("略過通知「\(title)」:available=\(available) authorized=\(authorized)")
            return
        }
        log("送出通知:\(title)")

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.userInfo = userInfo
        if let category { content.categoryIdentifier = category }

        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: id, content: content, trigger: nil))
    }
}

extension Notifier: UNUserNotificationCenterDelegate {
    /// app 在前景時也要顯示,否則使用者正在看視窗反而收不到。
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo
        guard let actionID = info["actionID"] as? String else { return }

        let approve: Bool
        switch response.actionIdentifier {
        case Self.approveAction: approve = true
        case Self.rejectAction: approve = false
        default: return   // 點通知本體 —— 不當成決定
        }

        await MainActor.run { self.onDecision?(actionID, approve) }
    }
}
