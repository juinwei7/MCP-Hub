import SwiftUI

/// 自訂工具 —— 把一個 HTTP API 包成 MCP 工具。
///
/// 版面是「左清單、右編輯」而不是網頁版的「列表頁 + 獨立編輯頁」。
/// 理由:調整一個工具通常要來回改幾次然後試跑,每次都要往返兩個頁面很煩。
/// 原生視窗夠寬,兩邊並列反而是最自然的做法。
struct CustomToolsTab: View {
    @ObservedObject var state: AppState
    @State private var selection: String?
    @State private var draft: ToolDraft?
    @State private var banner: (String, Banner.Kind)?
    @State private var testResult: String?
    @State private var testArgs = "{}"
    @State private var busy = false

    var body: some View {
        HSplitView {
            list.frame(minWidth: 220, idealWidth: 260, maxWidth: 340)
            detail.frame(minWidth: 380)
        }
        .task { await state.loadCustomTools() }
    }

    // ── 左:清單 ──────────────────────────────────────────
    private var list: some View {
        VStack(spacing: 0) {
            List(selection: $selection) {
                ForEach(groupedTools, id: \.0) { group, tools in
                    Section(group.isEmpty ? "未分類" : group) {
                        ForEach(tools) { tool in
                            row(tool).tag(tool.name)
                        }
                    }
                }
            }
            .listStyle(.sidebar)

            ActionBar {
                Text("\(state.customTools.count) 個")
                    .font(.caption).foregroundStyle(.secondary)
            } trailing: {
                Button {
                    selection = nil
                    draft = ToolDraft()
                    testResult = nil
                } label: {
                    Label("新增", systemImage: "plus")
                }
            }
        }
    }

    private func row(_ tool: HubClient.CustomTool) -> some View {
        HStack(spacing: Style.Space.row) {
            StatusDot(kind: tool.enabled ? .ok : .off,
                      help: tool.enabled ? "啟用中" : "已停用")
            VStack(alignment: .leading, spacing: 1) {
                Text(tool.name).lineLimit(1)
                Text(tool.urlTemplate)
                    .font(.caption2).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer()
            if tool.needsConfirm {
                Image(systemName: "checkmark.shield")
                    .font(.caption).foregroundStyle(.orange)
                    .help("呼叫前需要人工確認")
            }
        }
        .padding(.vertical, 1)
    }

    private var groupedTools: [(String, [HubClient.CustomTool])] {
        Dictionary(grouping: state.customTools, by: \.groupName)
            .sorted { lhs, rhs in
                // 未分類排最後 —— 有名字的分類是使用者刻意建立的,更重要
                if lhs.key.isEmpty { return false }
                if rhs.key.isEmpty { return true }
                return lhs.key.localizedCompare(rhs.key) == .orderedAscending
            }
            .map { ($0.key, $0.value.sorted { $0.name < $1.name }) }
    }

    // ── 右:編輯 ──────────────────────────────────────────
    @ViewBuilder
    private var detail: some View {
        if let draft {
            editor(draft)
        } else if let name = selection,
                  let tool = state.customTools.first(where: { $0.name == name }) {
            editor(ToolDraft(tool), existing: tool)
        } else {
            EmptyState(
                icon: "wrench.and.screwdriver",
                title: state.customTools.isEmpty ? "還沒有自訂工具" : "選一個工具來編輯",
                hint: state.customTools.isEmpty
                    ? "自訂工具把任意 HTTP API 包成 MCP 工具,Claude 就能直接呼叫它。也可以從設定分頁用 OpenAPI 規格批次匯入。"
                    : "",
                actionTitle: state.customTools.isEmpty ? "新增第一個" : nil,
                action: state.customTools.isEmpty ? { draft = ToolDraft() } : nil)
        }
    }

    private func editor(_ initial: ToolDraft, existing: HubClient.CustomTool? = nil) -> some View {
        ToolEditor(
            state: state,
            initial: initial,
            existing: existing,
            banner: $banner,
            testResult: $testResult,
            testArgs: $testArgs,
            busy: $busy,
            onSaved: { name in
                draft = nil
                selection = name
                Task { await state.loadCustomTools() }
            },
            onDeleted: {
                draft = nil
                selection = nil
                Task { await state.loadCustomTools() }
            },
            onCancel: { draft = nil })
    }
}

/// 編輯中的表單內容。與 HubClient.CustomTool 分開,因為表單允許中間狀態
/// (例如 JSON 還沒打完),而那種狀態不該污染已儲存的資料。
struct ToolDraft {
    var name = ""
    var description = ""
    var method = "GET"
    var urlTemplate = ""
    var groupName = ""
    var paramsJSON = "[]"
    /// 新增或修改的 header。空值代表刪除那個 key —— 與 API 的語意一致。
    var headerEdits: [String: String] = [:]
    var existingHeaderNames: [String] = []

    init() {}

    init(_ tool: HubClient.CustomTool) {
        name = tool.name
        description = tool.description
        method = tool.method
        urlTemplate = tool.urlTemplate
        groupName = tool.groupName
        existingHeaderNames = tool.headerNames
        if let data = try? JSONEncoder().encode(tool.params),
           let pretty = try? JSONSerialization.jsonObject(with: data),
           let out = try? JSONSerialization.data(withJSONObject: pretty,
                                                 options: [.prettyPrinted, .withoutEscapingSlashes]) {
            paramsJSON = String(decoding: out, as: UTF8.self)
        }
    }
}

private struct ToolEditor: View {
    @ObservedObject var state: AppState
    let initial: ToolDraft
    let existing: HubClient.CustomTool?
    @Binding var banner: (String, Banner.Kind)?
    @Binding var testResult: String?
    @Binding var testArgs: String
    @Binding var busy: Bool
    let onSaved: (String) -> Void
    let onDeleted: () -> Void
    let onCancel: () -> Void

    @State private var d = ToolDraft()
    @State private var newHeaderKey = ""
    @State private var newHeaderValue = ""
    @State private var confirmingDelete = false

    private var isNew: Bool { existing == nil }

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                Banner(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }

            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.section) {
                    basics
                    Divider()
                    headers
                    Divider()
                    params
                    Divider()
                    testSection
                }
                .padding(Style.Space.section)
            }

            ActionBar {
                if !isNew {
                    Button(role: .destructive) {
                        confirmingDelete = true
                    } label: {
                        Label("刪除", systemImage: "trash")
                    }
                    .confirmationDialog("確定要刪除「\(d.name)」?",
                                        isPresented: $confirmingDelete) {
                        Button("刪除", role: .destructive) { delete() }
                    } message: {
                        Text("刪除後無法復原。Claude 會立刻看不到這個工具。")
                    }
                }
            } trailing: {
                if isNew { Button("取消", action: onCancel) }
                Button(isNew ? "建立" : "儲存") { save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || d.name.isEmpty || d.urlTemplate.isEmpty)
            }
        }
        .onAppear { d = initial }
        .onChange(of: existing?.name) { _, _ in d = initial }
    }

    // ── 基本資訊 ──────────────────────────────────────────
    private var basics: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            FormRow(label: "名稱", hint: isNew ? "英數 _ -" : "") {
                if isNew {
                    TextField("weather", text: $d.name).textFieldStyle(.roundedBorder)
                } else {
                    // 改名等於換一個工具 —— 後端是以 name 為主鍵。
                    Text(d.name).font(Style.monoFont)
                    Spacer()
                }
            }
            FormRow(label: "說明", hint: "給 AI 看的") {
                TextField("查詢某城市的天氣", text: $d.description)
                    .textFieldStyle(.roundedBorder)
            }
            FormRow(label: "方法") {
                Picker("", selection: $d.method) {
                    ForEach(["GET", "POST", "PUT", "PATCH", "DELETE"], id: \.self) {
                        Text($0)
                    }
                }
                .labelsHidden().frame(width: 110)
                Spacer()
            }
            FormRow(label: "網址", hint: "{參數} 會代入") {
                TextField("https://api.example.com/weather?q={city}",
                          text: $d.urlTemplate)
                    .textFieldStyle(.roundedBorder).font(Style.monoFont)
            }
            FormRow(label: "分類", hint: "可留空") {
                TextField("", text: $d.groupName).textFieldStyle(.roundedBorder)
            }
        }
    }

    // ── Headers ───────────────────────────────────────────
    private var headers: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            HStack {
                Text("Headers").font(.headline)
                Pill(text: "含金鑰", tone: .warn)
                Spacer()
            }
            Text("已存在的 header 只顯示名稱 —— 值是金鑰,不會送回這個畫面。留空儲存代表刪除它。")
                .font(.caption).foregroundStyle(.secondary)

            ForEach(d.existingHeaderNames, id: \.self) { key in
                HStack(spacing: Style.Space.row) {
                    Text(key).font(Style.monoCaption).frame(width: 150, alignment: .leading)
                    SecureField("已設定(留空不動)",
                                text: Binding(
                                    get: { d.headerEdits[key] ?? "" },
                                    set: { d.headerEdits[key] = $0 }))
                        .textFieldStyle(.roundedBorder)
                    Button {
                        d.headerEdits[key] = ""          // 空字串 = 刪除
                        d.existingHeaderNames.removeAll { $0 == key }
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless).help("移除這個 header")
                }
            }

            HStack(spacing: Style.Space.row) {
                TextField("Authorization", text: $newHeaderKey)
                    .textFieldStyle(.roundedBorder).frame(width: 150)
                SecureField("Bearer …", text: $newHeaderValue).textFieldStyle(.roundedBorder)
                Button {
                    let key = newHeaderKey.trimmingCharacters(in: .whitespaces)
                    guard !key.isEmpty else { return }
                    d.headerEdits[key] = newHeaderValue
                    if !d.existingHeaderNames.contains(key) {
                        d.existingHeaderNames.append(key)
                    }
                    newHeaderKey = ""; newHeaderValue = ""
                } label: {
                    Image(systemName: "plus.circle")
                }
                .buttonStyle(.borderless)
                .disabled(newHeaderKey.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }

    // ── 參數 ──────────────────────────────────────────────
    private var params: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            Text("參數").font(.headline)
            Text("每項要有 name。出現在網址 {} 裡的會代入網址,其餘 GET 放 query、POST 放 body。")
                .font(.caption).foregroundStyle(.secondary)
            CodeEditor(
                placeholder: #"[{"name":"city","type":"string","required":true,"description":"城市名"}]"#,
                text: $d.paramsJSON)
        }
    }

    // ── 試跑 ──────────────────────────────────────────────
    private var testSection: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            HStack {
                Text("試跑").font(.headline)
                Spacer()
                Button {
                    runTest()
                } label: {
                    Label("執行", systemImage: "play.fill")
                }
                .disabled(busy || isNew)
                .help(isNew ? "先儲存才能試跑" : "會真的送出一次 HTTP 請求")
            }
            CodeEditor(placeholder: #"{"city": "台北"}"#, text: $testArgs, minHeight: 60)

            if let testResult {
                ScrollView {
                    Text(testResult)
                        .font(Style.monoCaption)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(Style.Space.row)
                }
                .frame(maxHeight: 180)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: Style.cornerRadius))
            }
        }
    }

    // ── 動作 ──────────────────────────────────────────────
    private func save() {
        guard let params = parsedParams() else { return }
        Task {
            busy = true
            defer { busy = false }
            var body: [String: Any] = [
                "description": d.description,
                "method": d.method,
                "url_template": d.urlTemplate,
                "params": params,
                "group_name": d.groupName,
            ]
            if !d.headerEdits.isEmpty { body["headers"] = d.headerEdits }

            do {
                if isNew {
                    body["name"] = d.name
                    _ = try await state.client.createCustomTool(body)
                } else {
                    _ = try await state.client.updateCustomTool(d.name, body)
                }
                banner = ("已儲存", .success)
                onSaved(d.name)
            } catch {
                banner = (error.localizedDescription, .error)
            }
        }
    }

    private func parsedParams() -> [Any]? {
        let text = d.paramsJSON.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return [] }
        guard let data = text.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data) as? [Any] else {
            banner = ("參數必須是合法的 JSON 陣列", .error)
            return nil
        }
        return parsed
    }

    private func delete() {
        Task {
            busy = true
            defer { busy = false }
            do {
                try await state.client.deleteCustomTool(d.name)
                onDeleted()
            } catch {
                banner = (error.localizedDescription, .error)
            }
        }
    }

    private func runTest() {
        Task {
            busy = true
            defer { busy = false }
            let text = testArgs.trimmingCharacters(in: .whitespacesAndNewlines)
            let args = (text.isEmpty ? [:] : (try? JSONSerialization.jsonObject(
                with: Data(text.utf8)) as? [String: Any]) ?? [:]) ?? [:]
            do {
                testResult = try await state.client.testCustomTool(d.name, arguments: args).display
            } catch {
                testResult = "❌ \(error.localizedDescription)"
            }
        }
    }
}
