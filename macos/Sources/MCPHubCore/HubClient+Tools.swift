import Foundation

/// HubClient 的自訂工具 / 複合工具 / 分類 / 匯入部分。
///
/// 與 HubClient.swift 分開只是檔案大小考量。欄位名稱一律對齊後端,
/// 不為了 Swift 慣例改名 —— 兩個平台共用同一份 API 契約。
extension HubClient {

    // ── 資料形狀 ──────────────────────────────────────────

    /// headers 只回報「有哪些 key、有沒有值」,不含內容 —— 那是金鑰。
    struct HeaderSlot: Decodable, Sendable {
        let set: Bool
    }

    struct ToolParam: Codable, Identifiable, Sendable {
        var name: String
        var type: String?
        var required: Bool?
        var description: String?

        var id: String { name }
        var typeOrDefault: String { type ?? "string" }
        var isRequired: Bool { required ?? false }

        init(name: String, type: String? = "string",
                    required: Bool? = false, description: String? = "") {
            self.name = name; self.type = type
            self.required = required; self.description = description
        }
    }

    struct CustomTool: Decodable, Identifiable, Sendable {
        let name: String
        let description: String
        let method: String
        let urlTemplate: String
        let headers: [String: HeaderSlot]
        let params: [ToolParam]
        let enabled: Bool
        let needsConfirm: Bool
        let groupName: String

        var id: String { name }
        var headerNames: [String] { headers.keys.sorted() }

        enum CodingKeys: String, CodingKey {
            case name, description, method, headers, params, enabled
            case urlTemplate = "url_template"
            case needsConfirm = "needs_confirm"
            case groupName = "group_name"
        }
    }

    struct CompositeStep: Codable, Identifiable, Sendable {
        var id: String
        var tool: String
        var args: [String: JSONValue]?

        init(id: String, tool: String, args: [String: JSONValue]? = nil) {
            self.id = id; self.tool = tool; self.args = args
        }
    }

    struct CompositeTool: Decodable, Identifiable, Sendable {
        let name: String
        let description: String
        let params: [ToolParam]
        let steps: [CompositeStep]
        let output: String
        let enabled: Bool
        let needsConfirm: Bool
        let groupName: String

        var id: String { name }

        enum CodingKeys: String, CodingKey {
            case name, description, params, steps, output, enabled
            case needsConfirm = "needs_confirm"
            case groupName = "group_name"
        }
    }

    struct Category: Decodable, Identifiable, Sendable {
        let name: String
        let total: Int
        let enabled: Int

        var id: String { name }
        var isUncategorized: Bool { name.isEmpty }
        var displayName: String { name.isEmpty ? "未分類" : name }
    }

    struct CategoryOverview: Decodable, Sendable {
        let categories: [Category]
        let uncategorized: Category?

        /// 未分類只在真的有工具時才顯示 —— 否則是一個永遠空著的假項目。
        var all: [Category] {
            guard let u = uncategorized, u.total > 0 else { return categories }
            return categories + [u]
        }
    }

    struct TestResult: Decodable, Sendable {
        let ok: Bool
        let texts: [String]?
        let error: String?

        var display: String {
            if ok { return (texts ?? []).joined(separator: "\n\n") }
            return "失敗:\(error ?? "未知錯誤")"
        }
    }

    struct SkillDoc: Decodable, Sendable {
        let name: String
        let markdown: String
        let purpose: String
        let isDraft: Bool
        let problem: String

        enum CodingKeys: String, CodingKey {
            case name, markdown, purpose, problem
            case isDraft = "is_draft"
        }
    }

    struct ImportResult: Decodable, Sendable {
        let added: [String]
        let skipped: [String]
        let sources: [String]?
    }

    struct ClaudeEntry: Decodable, Identifiable, Sendable {
        let name: String
        let slug: String
        let transport: String
        let target: String
        let authType: String
        let alreadyExists: Bool

        var id: String { slug }

        enum CodingKeys: String, CodingKey {
            case name, slug, transport, target
            case authType = "auth_type"
            case alreadyExists = "already_exists"
        }
    }

    struct ClaudePreview: Decodable, Sendable {
        let sources: [String]
        let entries: [ClaudeEntry]
    }

    struct DirectoryEntry: Decodable, Identifiable, Sendable {
        let id: String
        let name: String
        let kind: String
        let transport: String
        let baseURL: String
        let command: String
        let auth: String
        let needs: String
        let docURL: String
        let descr: String
        let builtin: Bool

        var target: String { command.isEmpty ? baseURL : command }
        var isInstallable: Bool { kind != "ref" && !target.isEmpty }

        enum CodingKeys: String, CodingKey {
            case id, name, kind, transport, command, auth, needs, descr, builtin
            case baseURL = "base_url"
            case docURL = "doc_url"
        }
    }

    struct InstallResult: Decodable, Sendable {
        let slug: String
        let nextStep: String

        enum CodingKeys: String, CodingKey {
            case slug
            case nextStep = "next_step"
        }
    }

    // ── 下游的新增 / 編輯 / 刪除 ──────────────────────────

    func createServer(_ body: [String: Any]) async throws -> Server {
        try await request("POST", "/servers", body: body)
    }

    func updateServer(_ slug: String, _ body: [String: Any]) async throws -> Server {
        try await request("PATCH", "/servers/\(escaped(slug))", body: body)
    }

    func deleteServer(_ slug: String) async throws {
        try await requestVoid("DELETE", "/servers/\(escaped(slug))")
    }

    func refreshServer(_ slug: String) async throws -> RefreshResult {
        try await request("POST", "/servers/\(escaped(slug))/refresh")
    }

    struct OAuthStart: Decodable, Sendable {
        let authorizationURL: String
        enum CodingKeys: String, CodingKey { case authorizationURL = "authorization_url" }
    }

    func startOAuth(_ slug: String) async throws -> OAuthStart {
        try await request("POST", "/servers/\(escaped(slug))/oauth/start", body: [:])
    }

    // ── 自訂工具 ──────────────────────────────────────────

    func customTools() async throws -> [CustomTool] {
        try await request("GET", "/custom-tools")
    }

    func createCustomTool(_ body: [String: Any]) async throws -> CustomTool {
        try await request("POST", "/custom-tools", body: body)
    }

    func updateCustomTool(_ name: String, _ body: [String: Any]) async throws -> CustomTool {
        try await request("PATCH", "/custom-tools/\(escaped(name))", body: body)
    }

    func deleteCustomTool(_ name: String) async throws {
        try await requestVoid("DELETE", "/custom-tools/\(escaped(name))")
    }

    func testCustomTool(_ name: String, arguments: [String: Any]) async throws -> TestResult {
        try await request("POST", "/custom-tools/\(escaped(name))/test",
                          body: ["arguments": arguments])
    }

    func bulkCustomTools(names: [String], action: String,
                                category: String? = nil) async throws {
        var body: [String: Any] = ["names": names, "action": action]
        if let category { body["category"] = category }
        let _: [String: JSONValue] = try await request("POST", "/custom-tools/bulk", body: body)
    }

    struct StepToolInfo: Decodable, Identifiable, Sendable {
        let name: String
        let description: String
        let params: [ToolParam]
        let kind: String

        var id: String { name }
    }

    func stepTools() async throws -> [StepToolInfo] {
        try await request("GET", "/step-tools")
    }

    // ── 複合工具 ──────────────────────────────────────────

    func compositeTools() async throws -> [CompositeTool] {
        try await request("GET", "/composite-tools")
    }

    func createCompositeTool(_ body: [String: Any]) async throws -> CompositeTool {
        try await request("POST", "/composite-tools", body: body)
    }

    func updateCompositeTool(_ name: String, _ body: [String: Any]) async throws -> CompositeTool {
        try await request("PATCH", "/composite-tools/\(escaped(name))", body: body)
    }

    func deleteCompositeTool(_ name: String) async throws {
        try await requestVoid("DELETE", "/composite-tools/\(escaped(name))")
    }

    func testCompositeTool(_ name: String, arguments: [String: Any]) async throws -> TestResult {
        try await request("POST", "/composite-tools/\(escaped(name))/test",
                          body: ["arguments": arguments])
    }

    func skill(_ name: String) async throws -> SkillDoc {
        try await request("GET", "/composite-tools/\(escaped(name))/skill")
    }

    func saveSkill(_ name: String, markdown: String, purpose: String) async throws -> SkillDoc {
        try await request("PUT", "/composite-tools/\(escaped(name))/skill",
                          body: ["markdown": markdown, "purpose": purpose])
    }

    /// 回傳 ZIP 的位元組 —— 這個端點刻意不是 JSON。
    func downloadSkill(_ name: String, markdown: String) async throws -> Data {
        try await requestData("POST", "/composite-tools/\(escaped(name))/skill/download",
                              body: ["markdown": markdown])
    }

    // ── 分類 ──────────────────────────────────────────────

    func categories() async throws -> CategoryOverview {
        try await request("GET", "/categories")
    }

    func createCategory(_ name: String) async throws -> CategoryOverview {
        try await request("POST", "/categories", body: ["name": name])
    }

    func deleteCategory(_ name: String) async throws {
        try await requestVoid("DELETE", "/categories/\(escaped(name))")
    }

    func setCategoryEnabled(_ name: String, _ enabled: Bool) async throws -> CategoryOverview {
        try await request("PATCH", "/categories/\(escaped(name))", body: ["enabled": enabled])
    }

    func setCategoryHeaders(_ name: String, _ headers: [String: String]) async throws -> CategoryOverview {
        try await request("PATCH", "/categories/\(escaped(name))", body: ["headers": headers])
    }

    func toolsInCategory(_ name: String) async throws -> [CustomTool] {
        try await request("GET", "/categories/\(escaped(name))/tools")
    }

    // ── 匯入 / 匯出 ───────────────────────────────────────

    func importMCPServers(config: String) async throws -> ImportResult {
        try await request("POST", "/import/mcp-servers", body: ["config": config])
    }

    func peekClaudeConfig() async throws -> ClaudePreview {
        try await request("GET", "/import/claude-config")
    }

    func importClaudeConfig() async throws -> ImportResult {
        try await request("POST", "/import/claude-config", body: [:])
    }

    func exportConfig(includeSecrets: Bool) async throws -> Data {
        try await requestData("GET", "/config/export?include_secrets=\(includeSecrets)")
    }

    // ── 目錄項 ────────────────────────────────────────────

    func directoryEntries() async throws -> [DirectoryEntry] {
        try await request("GET", "/directory-entries")
    }

    func installDirectoryEntry(_ id: String) async throws -> InstallResult {
        try await request("POST", "/directory-entries/\(escaped(id))/install", body: [:])
    }

    func deleteDirectoryEntry(_ id: String) async throws {
        try await requestVoid("DELETE", "/directory-entries/\(escaped(id))")
    }
}
