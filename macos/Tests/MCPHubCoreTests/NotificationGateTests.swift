import XCTest
@testable import MCPHubCore

/// 對應 Spec 004 約束 C-1:不重複通知。
///
/// 這些規則不能靠「盯著螢幕看通知有沒有跳」來驗證 —— 那既不可重複、也測不到
/// 「第二次不該跳」這種否定條件。所以決策邏輯特意與系統 API 分開。
final class NotificationGateTests: XCTestCase {

    // MARK: - 測試替身

    private func action(_ id: String, _ tool: String = "srv__tool",
                        status: String = "WAITING") -> HubClient.Action {
        decode(HubClient.Action.self, """
            {"id":"\(id)","tool":"\(tool)","status":"\(status)",
             "created_at":"2026-09-21T10:00:00","decided_at":""}
            """)
    }

    private func server(_ slug: String, status: String = "ok",
                        enabled: Bool = true, detail: String = "") -> HubClient.Server {
        decode(HubClient.Server.self, """
            {"slug":"\(slug)","name":"\(slug.capitalized)","base_url":"http://x/mcp",
             "transport":"http","auth_type":"none","enabled":\(enabled),
             "status":"\(status)","status_detail":"\(detail)","checked_at":"",
             "created_at":"2026-09-21T10:00:00","command":"","args":[],
             "has_token":false,"has_env":false}
            """)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) -> T {
        try! JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - 首次載入

    func testFirstEvaluateOnlyPrimesAndNotifiesNothing() {
        // 開 app 時不該被既有的待確認與異常洗版
        var gate = NotificationGate()
        let out = gate.evaluate(actions: [action("a1"), action("a2")],
                                servers: [server("s1", status: "error")])
        XCTAssertTrue(out.isEmpty, "第一次呼叫只該記錄現況,不該產生通知")
        XCTAssertTrue(gate.primed)
    }

    // MARK: - 待確認票券

    func testNewActionNotifiesOnce() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [])

        let first = gate.evaluate(actions: [action("a1", "srv__drop_table")], servers: [])
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(first[0].actionID, "a1")
        XCTAssertEqual(first[0].body, "srv__drop_table")

        // 同一筆再評估一次不該再通知(約束 C-1)
        let second = gate.evaluate(actions: [action("a1", "srv__drop_table")], servers: [])
        XCTAssertTrue(second.isEmpty, "同一筆票券不該重複通知")
    }

    func testDecidedActionStopsNotifying() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [])
        _ = gate.evaluate(actions: [action("a1")], servers: [])

        let after = gate.evaluate(actions: [action("a1", status: "APPROVED")], servers: [])
        XCTAssertTrue(after.isEmpty, "已決定的票券不該再通知")
    }

    func testSameIDReturningToWaitingNotifiesAgain() {
        // 票券離開 WAITING 後就該被忘掉,否則萬一同一個 id 再次待確認就會靜默
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [])
        _ = gate.evaluate(actions: [action("a1")], servers: [])
        _ = gate.evaluate(actions: [action("a1", status: "REJECTED")], servers: [])

        let again = gate.evaluate(actions: [action("a1")], servers: [])
        XCTAssertEqual(again.count, 1, "重新回到待確認應該再次通知")
    }

    func testMultipleNewActionsEachNotify() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [])
        let out = gate.evaluate(actions: [action("a1"), action("a2"), action("a3")], servers: [])
        XCTAssertEqual(Set(out.compactMap(\.actionID)), ["a1", "a2", "a3"])
    }

    // MARK: - 下游健康

    func testServerGoingErroredNotifiesOnce() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [server("s1")])

        let first = gate.evaluate(actions: [],
                                  servers: [server("s1", status: "error", detail: "連不上")])
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(first[0].body, "連不上")
        XCTAssertNil(first[0].actionID, "下游通知沒有核准按鈕")

        let second = gate.evaluate(actions: [],
                                   servers: [server("s1", status: "error", detail: "連不上")])
        XCTAssertTrue(second.isEmpty, "持續異常不該重複通知")
    }

    func testRecoveredThenErroredNotifiesAgain() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [server("s1")])
        _ = gate.evaluate(actions: [], servers: [server("s1", status: "error")])
        _ = gate.evaluate(actions: [], servers: [server("s1", status: "ok")])

        let again = gate.evaluate(actions: [], servers: [server("s1", status: "error")])
        XCTAssertEqual(again.count, 1, "恢復後再度異常應該再次通知")
    }

    func testDisabledServerNeverNotifies() {
        // 使用者自己停用的下游,不該跳通知說它壞了
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [server("s1", enabled: false)])
        let out = gate.evaluate(actions: [],
                                servers: [server("s1", status: "error", enabled: false)])
        XCTAssertTrue(out.isEmpty, "停用中的下游不該通知")
    }

    func testEmptyDetailFallsBackToGenericMessage() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [server("s1")])
        let out = gate.evaluate(actions: [], servers: [server("s1", status: "error", detail: "")])
        XCTAssertEqual(out.first?.body, "連線失敗", "沒有細節時要有可讀的預設訊息")
    }

    // MARK: - 混合

    func testActionsAndServersNotifyIndependently() {
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [server("s1")])
        let out = gate.evaluate(actions: [action("a1")],
                                servers: [server("s1", status: "error")])
        XCTAssertEqual(out.count, 2)
        XCTAssertEqual(out.filter { $0.actionID != nil }.count, 1)
    }

    func testNotificationIDsAreStablePerSubject() {
        // 通知 id 決定系統會不會蓋掉舊的那則,不能每次都變
        var gate = NotificationGate()
        gate.prime(actions: [], servers: [server("s1")])
        let out = gate.evaluate(actions: [action("a1")],
                                servers: [server("s1", status: "error")])
        XCTAssertEqual(Set(out.map(\.id)), ["action.a1", "server.s1"])
    }
}
