import Foundation

/// 決定「該不該通知」的純邏輯。
///
/// 特意與系統 API 分開:通知能不能送出取決於簽章與使用者授權,那是環境問題;
/// 但「同一筆票券不該通知兩次」是規則問題,必須能被測試。
/// 把兩者混在一起的話,唯一的驗證方式就是盯著螢幕看通知有沒有跳 —— 那不算驗證。
struct NotificationGate {

    struct Item: Equatable {
        let id: String
        let title: String
        let body: String
        /// 有值代表這是待確認票券,通知上要附核准 / 拒絕按鈕
        let actionID: String?
    }

    private var notifiedActions: Set<String> = []
    private var erroredServers: Set<String> = []
    private(set) var primed = false

    /// 第一次載入時只記錄現況,不產生通知 —— 否則開 app 就被既有狀態洗版。
    mutating func prime(actions: [HubClient.Action], servers: [HubClient.Server]) {
        notifiedActions = Set(Self.waitingIDs(actions))
        erroredServers = Set(Self.erroredSlugs(servers))
        primed = true
    }

    /// 回傳這一輪該送出的通知。呼叫後內部狀態即更新,同樣的輸入再呼叫一次會回空陣列。
    mutating func evaluate(actions: [HubClient.Action],
                           servers: [HubClient.Server]) -> [Item] {
        guard primed else {
            prime(actions: actions, servers: servers)
            return []
        }

        var out: [Item] = []

        let waiting = Set(Self.waitingIDs(actions))
        for a in actions where a.isWaiting && !notifiedActions.contains(a.id) {
            out.append(Item(id: "action.\(a.id)",
                            title: "有工具需要你確認",
                            body: a.tool,
                            actionID: a.id))
        }
        // 已離開待確認的就忘掉 —— 萬一同一個 id 之後又回到 WAITING,應該要再通知
        notifiedActions = waiting

        let nowErrored = Set(Self.erroredSlugs(servers))
        for slug in nowErrored.subtracting(erroredServers).sorted() {
            guard let s = servers.first(where: { $0.slug == slug }) else { continue }
            out.append(Item(id: "server.\(slug)",
                            title: "下游異常:\(s.name)",
                            body: s.statusDetail.isEmpty ? "連線失敗" : s.statusDetail,
                            actionID: nil))
        }
        erroredServers = nowErrored

        return out
    }

    private static func waitingIDs(_ actions: [HubClient.Action]) -> [String] {
        actions.filter(\.isWaiting).map(\.id)
    }

    /// 只看啟用中的下游 —— 使用者自己停用的不該跳通知說它壞了。
    private static func erroredSlugs(_ servers: [HubClient.Server]) -> [String] {
        servers.filter { $0.enabled && $0.isErrored }.map(\.slug)
    }
}
