import MCPHubCore

// 執行檔只做一件事:把控制權交給 library target。
// 可測試的邏輯全部住在 MCPHubCore,才能用 @testable import 驗證。
runApp()
