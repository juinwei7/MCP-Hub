// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MCPHub",
    platforms: [.macOS(.v14)],
    targets: [
        // 可測試的邏輯全部在這裡。執行檔只負責啟動。
        .target(name: "MCPHubCore", path: "Sources/MCPHubCore"),
        .executableTarget(name: "MCPHub", dependencies: ["MCPHubCore"], path: "Sources/MCPHub"),
        .testTarget(name: "MCPHubCoreTests", dependencies: ["MCPHubCore"],
                    path: "Tests/MCPHubCoreTests"),
    ]
)
