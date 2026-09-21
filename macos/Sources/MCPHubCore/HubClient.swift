import Foundation

/// Hub 後端 JSON API 的 client。
///
/// 只負責傳輸與解碼 —— 任何「要做什麼」的判斷都留在 Python 端。
/// Windows 版之後會有一份對等的實作,共用同一組端點。
struct HubClient {
    let token: String
    var port: Int = BackendSupervisor.port

    private var base: URL { URL(string: "http://127.0.0.1:\(port)/api/v1")! }

    struct Server: Decodable, Identifiable {
        let slug: String
        let name: String
        let baseURL: String
        let transport: String
        let authType: String
        let enabled: Bool
        let status: String
        let statusDetail: String
        let toolCount: Int?
        let hasToken: Bool

        var id: String { slug }
        var isHealthy: Bool { status == "ok" }
        var isErrored: Bool { status == "error" }

        enum CodingKeys: String, CodingKey {
            case slug, name, transport, enabled, status
            case baseURL = "base_url"
            case authType = "auth_type"
            case statusDetail = "status_detail"
            case toolCount = "tool_count"
            case hasToken = "has_token"
        }
    }

    struct APIError: Error, Decodable {
        let error: String
        let message: String
    }

    enum ClientError: Error, LocalizedError {
        case transport(String)
        case api(APIError)
        case decoding(String)

        var errorDescription: String? {
            switch self {
            case .transport(let m): return m
            case .api(let e): return e.message
            case .decoding(let m): return "回應格式無法解讀:\(m)"
            }
        }
    }

    struct Tool: Decodable, Identifiable {
        let name: String
        let description: String
        let descOverride: String
        let enabled: Bool
        let needsConfirm: Bool

        var id: String { name }
        var shownDescription: String { descOverride.isEmpty ? description : descOverride }

        enum CodingKeys: String, CodingKey {
            case name, description, enabled
            case descOverride = "desc_override"
            case needsConfirm = "needs_confirm"
        }
    }

    struct LogPage: Decodable {
        let rows: [LogRow]
        let page: Int
        let pages: Int
        let total: Int
        let errors: Int
    }

    struct LogRow: Decodable, Identifiable {
        let id: Int
        let time: String
        let serverSlug: String
        let tool: String
        let status: String
        let durationMs: Int?
        let error: String

        var isError: Bool { status == "error" }
        var qualifiedName: String { serverSlug.isEmpty ? tool : "\(serverSlug)__\(tool)" }

        enum CodingKeys: String, CodingKey {
            case id, time, tool, status, error
            case serverSlug = "server_slug"
            case durationMs = "duration_ms"
        }
    }

    struct Action: Decodable, Identifiable {
        let id: String
        let tool: String
        let status: String
        let createdAt: String
        let decidedAt: String

        var isWaiting: Bool { status == "WAITING" }

        enum CodingKeys: String, CodingKey {
            case id, tool, status
            case createdAt = "created_at"
            case decidedAt = "decided_at"
        }
    }

    struct RefreshResult: Decodable {
        let toolCount: Int
        let detail: String

        enum CodingKeys: String, CodingKey {
            case detail
            case toolCount = "tool_count"
        }
    }

    struct CheckResult: Decodable {
        let status: String
        let detail: String
        let toolCount: Int

        enum CodingKeys: String, CodingKey {
            case status, detail
            case toolCount = "tool_count"
        }
    }

    // MARK: - 下游

    func servers() async throws -> [Server] {
        try await get("/servers")
    }

    func setEnabled(_ slug: String, _ enabled: Bool) async throws -> Server {
        try await send("PATCH", "/servers/\(slug)/enabled", body: ["enabled": enabled])
    }

    func checkAll() async throws -> [String: Int] {
        try await send("POST", "/servers/check-all", body: nil)
    }

    func check(_ slug: String) async throws -> CheckResult {
        try await send("POST", "/servers/\(slug)/check", body: nil)
    }

    func refresh(_ slug: String) async throws -> RefreshResult {
        try await send("POST", "/servers/\(slug)/refresh", body: nil)
    }

    // MARK: - 工具

    func tools(_ slug: String) async throws -> [Tool] {
        try await get("/servers/\(slug)/tools")
    }

    func setTool(_ slug: String, _ tool: String,
                 enabled: Bool? = nil, needsConfirm: Bool? = nil) async throws -> Tool {
        var body: [String: Any] = [:]
        if let enabled { body["enabled"] = enabled }
        if let needsConfirm { body["needs_confirm"] = needsConfirm }
        return try await send("PATCH", "/servers/\(slug)/tools/\(tool)", body: body)
    }

    func setAllTools(_ slug: String, enabled: Bool) async throws -> [Tool] {
        try await send("PATCH", "/servers/\(slug)/tools", body: ["enabled": enabled])
    }

    // MARK: - 記錄與待確認

    func logs(page: Int, perPage: Int = 50, errorsOnly: Bool = false) async throws -> LogPage {
        try await get("/logs?page=\(page)&per_page=\(perPage)&errors_only=\(errorsOnly)")
    }

    func actions() async throws -> [Action] {
        try await get("/actions")
    }

    func decide(_ id: String, approve: Bool) async throws -> Action {
        try await send("POST", "/actions/\(id)/decision", body: ["approve": approve])
    }

    // MARK: - 傳輸

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try await send("GET", path, body: nil)
    }

    private func send<T: Decodable>(_ method: String, _ path: String,
                                    body: [String: Any]?) async throws -> T {
        // 不能用 appendingPathComponent —— 它會把查詢字串的 ? 與 & 當成路徑字元編碼掉
        guard let url = URL(string: base.absoluteString + path) else {
            throw ClientError.transport("網址組不起來:\(path)")
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 15
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw ClientError.transport(error.localizedDescription)
        }

        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            // 錯誤一律是扁平的 {"error", "message"}
            if let apiError = try? JSONDecoder().decode(APIError.self, from: data) {
                throw ClientError.api(apiError)
            }
            throw ClientError.transport("HTTP \(code)")
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw ClientError.decoding(String(describing: error))
        }
    }
}
