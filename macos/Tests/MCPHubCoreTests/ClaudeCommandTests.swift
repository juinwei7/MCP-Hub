import XCTest
@testable import MCPHubCore

/// 接入 Claude 的指令。
///
/// 這串指令少了環境變數的話,使用者會遇到最難查的那種問題:Claude 啟動的
/// 聚合器讀到專案目錄那份空的 actions.db,app 裡的設定一個都不生效,而且
/// 完全沒有錯誤訊息 —— 只是「工具怎麼都沒出現」。所以每個變數都要測。
final class ClaudeCommandTests: XCTestCase {

    private func command() -> String {
        BackendSupervisor.claudeAddCommand(
            dataDir: "/Users/me/Library/Application Support/MCP Hub",
            python: "/proj/.venv/bin/python",
            repo: "/proj")
    }

    func testPointsAtTheAppsDatabase() {
        // 最關鍵的一項:少了它,聚合器會讀到另一份 DB
        XCTAssertTrue(command().contains(
            #"MCP_HUB_DB="/Users/me/Library/Application Support/MCP Hub/actions.db""#))
    }

    func testPointsAtTheAppsKey() {
        // DB 指對但金鑰指錯的話,token 全部解不開
        XCTAssertTrue(command().contains(
            #"MCP_HUB_KEY="/Users/me/Library/Application Support/MCP Hub/.secret_key""#))
    }

    func testIncludesPythonPath() {
        XCTAssertTrue(command().contains(#"PYTHONPATH="/proj""#))
    }

    func testLaunchesTheAggregatorNotTheAdmin() {
        // gateway.web 是管理台,Claude 要的是 gateway.hub_server
        XCTAssertTrue(command().contains("-m gateway.hub_server"))
        XCTAssertFalse(command().contains("gateway.web"))
    }

    func testQuotesPathsThatMayContainSpaces() {
        // Application Support 本身就含空白,沒引號會被拆成兩個參數
        let quoted = command().components(separatedBy: "\"").count - 1
        XCTAssertGreaterThanOrEqual(quoted, 8, "每個路徑都該被引號包住")
    }
}
