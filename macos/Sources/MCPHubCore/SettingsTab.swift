import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// 設定 —— 匯入、匯出、分類、目錄。
///
/// 網頁版把這些散在四個頁面(匯入、API 目錄、Registry、分類)。它們的共通點是
/// 「不常用、但需要時得找得到」,所以收在同一個分頁、用側邊欄切換,
/// 而不是佔掉主分頁列的四個位置。
struct SettingsTab: View {
    @ObservedObject var state: AppState
    @State private var section: Section = .importServers

    enum Section: String, CaseIterable, Identifiable {
        case importServers = "匯入下游"
        case openapi = "OpenAPI 匯入"
        case catalog = "服務目錄"
        case categories = "分類"
        case export = "匯出設定"

        var id: String { rawValue }

        var icon: String {
            switch self {
            case .importServers: return "square.and.arrow.down"
            case .openapi: return "doc.badge.gearshape"
            case .catalog: return "books.vertical"
            case .categories: return "folder"
            case .export: return "square.and.arrow.up"
            }
        }
    }

    var body: some View {
        HSplitView {
            List(Section.allCases, selection: $section) { s in
                Label(s.rawValue, systemImage: s.icon).tag(s)
            }
            .listStyle(.sidebar)
            .frame(minWidth: 150, idealWidth: 170, maxWidth: 220)

            Group {
                switch section {
                case .importServers: ImportServersView(state: state)
                case .openapi: OpenAPIImportView(state: state)
                case .catalog: CatalogView(state: state)
                case .categories: CategoriesView(state: state)
                case .export: ExportView(state: state)
                }
            }
            .frame(minWidth: 420)
        }
    }
}

// ── 匯入下游 ──────────────────────────────────────────────
private struct ImportServersView: View {
    @ObservedObject var state: AppState
    @State private var pasted = ""
    @State private var preview: HubClient.ClaudePreview?
    @State private var banner: (String, AlertLine.Kind)?
    @State private var busy = false

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }
            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.block) {
                    claudeSection
                    Divider()
                    pasteSection
                }
                .padding(Style.Space.section)
            }
        }
        .task { await loadPreview() }
    }

    private var claudeSection: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            Text("從 Claude 設定匯入").font(.headline)
            Text("讀取這台機器上 Claude Desktop 與 Claude Code 的設定,把裡面的 MCP server 加進來。")
                .font(.caption).foregroundStyle(.secondary)

            if let preview {
                if preview.entries.isEmpty {
                    Text("找不到可匯入的項目。")
                        .font(.callout).foregroundStyle(.secondary)
                        .padding(.vertical, Style.Space.row)
                } else {
                    // 先讓人看清楚會發生什麼,再按匯入 —— 網頁版是按下去才知道結果
                    VStack(spacing: 0) {
                        ForEach(preview.entries) { e in
                            HubRow(health: e.alreadyExists ? .off : .ok,
                                   title: e.name,
                                   detail: e.target,
                                   dimmed: e.alreadyExists) {
                                Pill(text: e.transport,
                                     tone: e.transport == "stdio" ? .neutral : .accent)
                                if e.authType != "none" {
                                    Pill(text: e.authType, tone: .warn)
                                }
                                if e.alreadyExists {
                                    Text("已存在")
                                        .font(Style.Face.meta).foregroundStyle(Palette.ink3)
                                }
                            }
                        }
                    }

                    let newCount = preview.entries.filter { !$0.alreadyExists }.count
                    HStack {
                        Text("\(newCount) 個可新增,\(preview.entries.count - newCount) 個已存在")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("匯入") { importClaude() }
                            .buttonStyle(.borderedProminent)
                            .disabled(busy || newCount == 0)
                    }
                    .padding(.top, Style.Space.row)
                }

                if !preview.sources.isEmpty {
                    Text("來源:" + preview.sources.joined(separator: "、"))
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            } else {
                ProgressView().controlSize(.small)
            }
        }
    }

    private var pasteSection: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            Text("貼上設定").font(.headline)
            Text("貼一份 mcpServers JSON,或整份 Claude 設定檔的內容。")
                .font(.caption).foregroundStyle(.secondary)
            CodeEditor(placeholder: #"{"mcpServers": {"my-server": {"url": "https://…/mcp"}}}"#,
                       text: $pasted, minHeight: 130)
            HStack {
                Spacer()
                Button("匯入") { importPasted() }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || pasted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }

    private func loadPreview() async {
        do { preview = try await state.client.peekClaudeConfig() }
        catch { banner = (error.localizedDescription, .problem) }
    }

    private func importClaude() {
        Task {
            busy = true
            defer { busy = false }
            do {
                let r = try await state.client.importClaudeConfig()
                banner = (summary(r), r.added.isEmpty ? .info : .success)
                await state.refresh()
                await loadPreview()
            } catch { banner = (error.localizedDescription, .problem) }
        }
    }

    private func importPasted() {
        Task {
            busy = true
            defer { busy = false }
            do {
                let r = try await state.client.importMCPServers(config: pasted)
                banner = (summary(r), r.added.isEmpty ? .info : .success)
                if !r.added.isEmpty { pasted = "" }
                await state.refresh()
            } catch { banner = (error.localizedDescription, .problem) }
        }
    }

    private func summary(_ r: HubClient.ImportResult) -> String {
        if r.added.isEmpty {
            return "沒有新增任何下游" + (r.skipped.isEmpty ? "。" : ",\(r.skipped.count) 個已存在或格式不符。")
        }
        var text = "已新增 \(r.added.count) 台:" + r.added.joined(separator: "、")
        if !r.skipped.isEmpty { text += ",略過 \(r.skipped.count) 個" }
        return text
    }
}

// ── OpenAPI 匯入 ──────────────────────────────────────────
private struct OpenAPIImportView: View {
    @ObservedObject var state: AppState
    @State private var url = ""
    @State private var pastedSpec = ""
    @State private var preview: OpenAPIPreview?
    @State private var selected: Set<String> = []
    @State private var query = ""
    @State private var groupName = ""
    @State private var headersJSON = ""
    @State private var banner: (String, AlertLine.Kind)?
    @State private var busy = false

    struct OpenAPIPreview: Decodable {
        let title: String
        let baseURL: String
        let operations: [Operation]

        struct Operation: Decodable, Identifiable {
            let opID: String
            let method: String
            let path: String
            let summary: String
            let paramCount: Int

            var id: String { opID }
            var searchText: String { "\(method) \(path) \(summary)".lowercased() }

            enum CodingKeys: String, CodingKey {
                case method, path, summary
                case opID = "op_id"
                case paramCount = "param_count"
            }
        }

        enum CodingKeys: String, CodingKey {
            case title, operations
            case baseURL = "base_url"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }
            if preview == nil { sourceForm } else { operationPicker }
        }
    }

    private var sourceForm: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Style.Space.section) {
                Text("從 OpenAPI 規格匯入").font(.headline)
                Text("給一個服務網址自動探查,或直接貼規格內容。每個端點會變成一個自訂工具。")
                    .font(.caption).foregroundStyle(.secondary)

                FormRow(label: "服務網址", hint: "自動探查") {
                    TextField("https://api.example.com", text: $url)
                        .textFieldStyle(.roundedBorder).font(Style.Face.monoBody)
                }

                VStack(alignment: .leading, spacing: Style.Space.tight) {
                    Text("或直接貼規格").font(.caption).foregroundStyle(.secondary)
                    CodeEditor(placeholder: "{ \"openapi\": \"3.0.0\", … }",
                               text: $pastedSpec, minHeight: 110)
                }

                HStack {
                    Spacer()
                    Button("探查並預覽") { loadPreview() }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || (url.isEmpty && pastedSpec.isEmpty))
                }
            }
            .padding(Style.Space.section)
        }
    }

    private var operationPicker: some View {
        VStack(spacing: 0) {
            HStack(spacing: Style.Space.row) {
                Button { preview = nil; selected = [] } label: {
                    Label("換一份", systemImage: "chevron.left")
                }
                .buttonStyle(.borderless)
                Text(preview?.title ?? "").font(.headline).lineLimit(1)
                Spacer()
                TextField("搜尋端點", text: $query)
                    .textFieldStyle(.roundedBorder).frame(width: 180)
            }
            .padding(Style.Space.row)

            List(filtered, selection: $selected) { op in
                HStack(spacing: Style.Space.row) {
                    Pill(text: op.method, tone: op.method == "GET" ? .neutral : .accent)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(op.path).font(Style.Face.mono)
                        if !op.summary.isEmpty {
                            Text(op.summary).font(.caption2).foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                    Spacer()
                    if op.paramCount > 0 {
                        Text("\(op.paramCount) 參數")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .tag(op.opID)
            }

            VStack(alignment: .leading, spacing: Style.Space.row) {
                Divider()
                FormRow(label: "分類", hint: "可留空") {
                    TextField(preview?.title ?? "", text: $groupName)
                        .textFieldStyle(.roundedBorder)
                }
                FormRow(label: "共用 headers", hint: "JSON,可放金鑰") {
                    TextField(#"{"Authorization": "Bearer …"}"#, text: $headersJSON)
                        .textFieldStyle(.roundedBorder).font(Style.Face.mono)
                }
                HStack {
                    Button(selected.count == filtered.count ? "取消全選" : "全選") {
                        selected = selected.count == filtered.count
                            ? [] : Set(filtered.map(\.opID))
                    }
                    .buttonStyle(.borderless)
                    Text("已選 \(selected.count) / \(preview?.operations.count ?? 0)")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("匯入所選") { performImport() }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || selected.isEmpty)
                }
            }
            .padding(Style.Space.row)
        }
    }

    private var filtered: [OpenAPIPreview.Operation] {
        guard let ops = preview?.operations else { return [] }
        let q = query.lowercased().trimmingCharacters(in: .whitespaces)
        return q.isEmpty ? ops : ops.filter { $0.searchText.contains(q) }
    }

    private func loadPreview() {
        Task {
            busy = true
            defer { busy = false }
            do {
                var body: [String: Any] = [:]
                if !pastedSpec.isEmpty {
                    body["spec_text"] = pastedSpec
                } else {
                    // 先探查拿到 spec 網址,再要預覽
                    let probe: ProbeResult = try await state.client.request(
                        "POST", "/openapi/probe", body: ["url": url])
                    body["spec_url"] = probe.specURL
                }
                preview = try await state.client.request("POST", "/openapi/preview", body: body)
                selected = []
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }

    private struct ProbeResult: Decodable {
        let specURL: String
        enum CodingKeys: String, CodingKey { case specURL = "spec_url" }
    }

    private struct ImportResult: Decodable {
        let category: String
        let created: [String]
        let conflicts: [Conflict]
        struct Conflict: Decodable { let name: String; let reason: String }
    }

    private func performImport() {
        Task {
            busy = true
            defer { busy = false }
            var body: [String: Any] = ["op_ids": Array(selected)]
            if !pastedSpec.isEmpty { body["spec_text"] = pastedSpec }
            if !groupName.isEmpty { body["group_name"] = groupName }
            if let base = preview?.baseURL, !base.isEmpty { body["base_url"] = base }
            if !headersJSON.isEmpty,
               let parsed = try? JSONSerialization.jsonObject(
                   with: Data(headersJSON.utf8)) as? [String: Any] {
                body["headers"] = parsed
            }
            do {
                let r: ImportResult = try await state.client.request(
                    "POST", "/openapi/import", body: body)
                var text = "已匯入 \(r.created.count) 個工具到「\(r.category)」"
                if !r.conflicts.isEmpty {
                    text += ",\(r.conflicts.count) 個因名稱衝突略過"
                }
                banner = (text, r.created.isEmpty ? .info : .success)
                await state.loadCustomTools()
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }
}

// ── 服務目錄 ──────────────────────────────────────────────
private struct CatalogView: View {
    @ObservedObject var state: AppState
    @State private var entries: [HubClient.DirectoryEntry] = []
    @State private var banner: (String, AlertLine.Kind)?
    @State private var busy = false

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }
            List(entries) { e in
                HStack(spacing: Style.Space.row) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: Style.Space.tight) {
                            Text(e.name)
                            Pill(text: e.transport,
                                 tone: e.transport == "stdio" ? .neutral : .accent)
                            if e.auth != "none" { Pill(text: e.auth, tone: .warn) }
                        }
                        if !e.descr.isEmpty {
                            Text(e.descr).font(.caption).foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        if !e.needs.isEmpty {
                            Text("需要:\(e.needs)")
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    Spacer()
                    if e.isInstallable {
                        Button("加入") { install(e) }.disabled(busy)
                    } else {
                        Text("僅供參考").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .padding(.vertical, 3)
            }
            ActionBar {
                Text("\(entries.count) 個服務").font(.caption).foregroundStyle(.secondary)
            } trailing: {
                Button("重新整理") { Task { await load() } }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do { entries = try await state.client.directoryEntries() }
        catch { banner = (error.localizedDescription, .problem) }
    }

    private func install(_ e: HubClient.DirectoryEntry) {
        Task {
            busy = true
            defer { busy = false }
            do {
                let r = try await state.client.installDirectoryEntry(e.id)
                banner = ("\(e.name) — \(r.nextStep)", .success)
                await state.refresh()
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }
}

// ── 分類 ──────────────────────────────────────────────────
private struct CategoriesView: View {
    @ObservedObject var state: AppState
    @State private var newName = ""
    @State private var banner: (String, AlertLine.Kind)?
    @State private var deleting: String?
    @State private var headerTarget: String?
    @State private var headersJSON = ""

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }

            if let overview = state.categories, !overview.all.isEmpty {
                List(overview.all) { cat in
                    HStack(spacing: Style.Space.row) {
                        Image(systemName: cat.isUncategorized ? "tray" : "folder")
                            .foregroundStyle(.secondary)
                        Text(cat.displayName)
                        Spacer()
                        Text("\(cat.enabled)/\(cat.total) 啟用")
                            .font(.caption).foregroundStyle(.secondary)
                        if !cat.isUncategorized {
                            Button("套用金鑰") {
                                headerTarget = cat.name
                                headersJSON = ""
                            }
                            .buttonStyle(.borderless)
                            .help("把同一組 headers 套到這個分類的所有工具")
                            Button(role: .destructive) { deleting = cat.name } label: {
                                Image(systemName: "trash")
                            }
                            .buttonStyle(.borderless)
                        }
                    }
                    .padding(.vertical, 2)
                }
            } else {
                EmptyState(icon: "folder", title: "還沒有分類",
                           hint: "分類用來整理自訂工具,也可以把同一把金鑰一次套用到整組工具。")
            }

            ActionBar {
                HStack(spacing: Style.Space.row) {
                    TextField("新分類名稱", text: $newName)
                        .textFieldStyle(.roundedBorder).frame(width: 160)
                    Button("建立") { create() }
                        .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            } trailing: {
                Button("重新整理") { Task { await state.loadCategories() } }
            }
        }
        .task { await state.loadCategories() }
        .confirmationDialog("確定要刪除分類「\(deleting ?? "")」?",
                            isPresented: .constant(deleting != nil)) {
            Button("刪除分類和裡面的工具", role: .destructive) { performDelete() }
            Button("取消", role: .cancel) { deleting = nil }
        } message: {
            Text("分類裡的自訂工具會一起被刪除,無法復原。")
        }
        .sheet(isPresented: .constant(headerTarget != nil)) { headerSheet }
    }

    private var headerSheet: some View {
        VStack(alignment: .leading, spacing: Style.Space.section) {
            Text("把 headers 套到「\(headerTarget ?? "")」").font(.headline)
            Text("這組 headers 會覆寫該分類**所有**工具的既有 headers。常用在整組共用同一把 API 金鑰。")
                .font(.caption).foregroundStyle(.secondary)
            CodeEditor(placeholder: #"{"Authorization": "Bearer YOUR_KEY"}"#,
                       text: $headersJSON, minHeight: 110)
            HStack {
                Spacer()
                Button("取消") { headerTarget = nil }
                Button("套用") { applyHeaders() }
                    .buttonStyle(.borderedProminent)
                    .disabled(headersJSON.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(Style.Space.block)
        .frame(width: 460)
    }

    private func create() {
        Task {
            do {
                _ = try await state.client.createCategory(
                    newName.trimmingCharacters(in: .whitespaces))
                newName = ""
                await state.loadCategories()
            } catch { banner = (error.localizedDescription, .problem) }
        }
    }

    private func performDelete() {
        guard let name = deleting else { return }
        deleting = nil
        Task {
            do {
                try await state.client.deleteCategory(name)
                await state.loadCategories()
                await state.loadCustomTools()
            } catch { banner = (error.localizedDescription, .problem) }
        }
    }

    private func applyHeaders() {
        guard let name = headerTarget else { return }
        guard let parsed = try? JSONSerialization.jsonObject(
                with: Data(headersJSON.utf8)) as? [String: String] else {
            banner = ("headers 必須是 JSON 物件,值都是字串", .problem)
            return
        }
        headerTarget = nil
        Task {
            do {
                _ = try await state.client.setCategoryHeaders(name, parsed)
                banner = ("已套用到「\(name)」的所有工具", .success)
                await state.loadCustomTools()
            } catch { banner = (error.localizedDescription, .problem) }
        }
    }
}

// ── 匯出 ──────────────────────────────────────────────────
private struct ExportView: View {
    @ObservedObject var state: AppState
    @State private var includeSecrets = false
    @State private var banner: (String, AlertLine.Kind)?

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }
            VStack(alignment: .leading, spacing: Style.Space.section) {
                Text("匯出下游設定").font(.headline)
                Text("匯出成 mcpServers 格式,可以貼到別台機器的 Hub 或 Claude 設定裡。")
                    .font(.caption).foregroundStyle(.secondary)

                Toggle("包含金鑰", isOn: $includeSecrets)

                // 預設不含金鑰是刻意的 —— 整個加密設計就是為了「檔案外流也拿不到密鑰」,
                // 一個自動攤平密鑰的匯出按鈕會讓那個設計失效。
                AlertLine(
                    text: includeSecrets
                        ? "匯出檔會含明文的 bearer token 與 stdio 環境變數。請當成密碼檔保管,不要傳到聊天室或版控。"
                        : "金鑰不會被匯出,目標機器需要重新填入。",
                    kind: includeSecrets ? .problem : .info)

                HStack {
                    Spacer()
                    Button("匯出…") { export() }.buttonStyle(.borderedProminent)
                }
                Spacer()
            }
            .padding(Style.Space.section)
        }
    }

    private func export() {
        Task {
            do {
                let data = try await state.client.exportConfig(includeSecrets: includeSecrets)
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "mcp-hub-servers.json"
                panel.allowedContentTypes = [.json]
                if panel.runModal() == .OK, let url = panel.url {
                    try data.write(to: url)
                    banner = ("已匯出到 \(url.lastPathComponent)", .success)
                }
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }
}
