import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// 複合工具 —— 依序執行多個既有工具,把結果組成一個。
///
/// 網頁版用一個 JSON textarea 讓使用者手打步驟。那能動,但要記住每個工具的
/// 完整名稱、參數形狀,打錯了要送出才知道。這裡改成一列一步驟的結構化編輯:
/// 工具從下拉選單挑(名單是後端實際可用的),參數仍是 JSON 但範圍縮小到單一步驟,
/// 錯了也只影響那一列。
struct CompositeToolsTab: View {
    @ObservedObject var state: AppState
    @State private var selection: String?
    @State private var creating = false
    @State private var banner: (String, AlertLine.Kind)?

    var body: some View {
        Group {
            if state.compositeTools.isEmpty, !creating {
                detail   // 理由同 CustomToolsTab:沒東西可列就不留空欄
            } else {
                // 固定寬度,理由同 CustomToolsTab
                HStack(spacing: 0) {
                    list.frame(width: 252)
                    Divider()
                    detail.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .task {
            await state.loadCompositeTools()
            await state.loadAvailableStepTools()
        }
    }

    private var list: some View {
        VStack(spacing: 0) {
            List(selection: $selection) {
                ForEach(state.compositeTools) { tool in
                    HubRow(health: tool.enabled ? .ok : .off,
                           title: tool.name,
                           detail: "\(tool.steps.count) 個步驟",
                           detailIsMachine: false,
                           dimmed: !tool.enabled) {
                        if tool.needsConfirm {
                            Image(systemName: "checkmark.shield")
                                .font(.system(size: 11)).foregroundStyle(Palette.warn)
                                .help("呼叫前需要人工確認")
                        }
                    }
                    .tag(tool.name)
                }
            }
            .listStyle(.sidebar)
            // .sidebar 在非 sidebar 位置會變成半透明材質,把桌布透進來。
            // 這欄是視窗內部的清單,要有自己的底。
            .scrollContentBackground(.hidden)
            .background(Palette.sunken)

            ActionBar {
                Text("\(state.compositeTools.count) 個")
                    .font(.caption).foregroundStyle(.secondary)
            } trailing: {
                Button {
                    selection = nil
                    creating = true
                } label: {
                    Label("新增", systemImage: "plus")
                }
            }
        }
    }

    @ViewBuilder
    private var detail: some View {
        if creating {
            CompositeEditor(state: state, existing: nil, banner: $banner,
                            onSaved: { name in
                                creating = false
                                selection = name
                                Task { await state.loadCompositeTools() }
                            },
                            onDeleted: {},
                            onCancel: { creating = false })
        } else if let name = selection,
                  let tool = state.compositeTools.first(where: { $0.name == name }) {
            CompositeEditor(state: state, existing: tool, banner: $banner,
                            onSaved: { _ in Task { await state.loadCompositeTools() } },
                            onDeleted: {
                                selection = nil
                                Task { await state.loadCompositeTools() }
                            },
                            onCancel: {})
            .id(tool.name)
        } else {
            EmptyState(
                icon: "square.stack.3d.up",
                title: state.compositeTools.isEmpty ? "還沒有複合工具" : "選一個來編輯",
                hint: state.compositeTools.isEmpty
                    ? "把幾個常一起用的工具串成一個。Claude 呼叫一次就拿到全部結果,不必自己拆成多步。"
                    : "",
                actionTitle: state.compositeTools.isEmpty ? "新增第一個" : nil,
                action: state.compositeTools.isEmpty ? { creating = true } : nil)
        }
    }
}

// ── 編輯器 ────────────────────────────────────────────────
private struct CompositeEditor: View {
    @ObservedObject var state: AppState
    let existing: HubClient.CompositeTool?
    @Binding var banner: (String, AlertLine.Kind)?
    let onSaved: (String) -> Void
    let onDeleted: () -> Void
    let onCancel: () -> Void

    @State private var name = ""
    @State private var description = ""
    @State private var groupName = ""
    @State private var paramsJSON = "[]"
    @State private var steps: [StepDraft] = []
    @State private var testArgs = "{}"
    @State private var testResult: String?
    @State private var busy = false
    @State private var confirmingDelete = false
    @State private var showingSkill = false

    private var isNew: Bool { existing == nil }

    var body: some View {
        VStack(spacing: 0) {
            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }

            ScrollView {
                VStack(alignment: .leading, spacing: Style.Space.section) {
                    basics
                    Divider()
                    stepsSection
                    Divider()
                    paramsSection
                    Divider()
                    testSection
                }
                .padding(Style.Space.section)
            }

            ActionBar {
                if !isNew {
                    Button(role: .destructive) { confirmingDelete = true } label: {
                        Label("刪除", systemImage: "trash")
                    }
                    .confirmationDialog("確定要刪除「\(name)」?", isPresented: $confirmingDelete) {
                        Button("刪除", role: .destructive) { delete() }
                    } message: {
                        Text("連同它的 Skill 草稿一起刪除,無法復原。")
                    }
                }
            } trailing: {
                if !isNew {
                    Button {
                        showingSkill = true
                    } label: {
                        Label("Skill", systemImage: "doc.text")
                    }
                    .help("產生給 Claude 的使用說明,可匯出成 Skill 套件")
                }
                if isNew { Button("取消", action: onCancel) }
                Button(isNew ? "建立" : "儲存") { save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || name.isEmpty || steps.isEmpty)
            }
        }
        .sheet(isPresented: $showingSkill) {
            SkillSheet(state: state, toolName: name)
        }
        .onAppear(perform: load)
    }

    private func load() {
        guard let t = existing else {
            steps = [StepDraft()]
            return
        }
        name = t.name
        description = t.description
        groupName = t.groupName
        steps = t.steps.map { StepDraft($0) }
        if let data = try? JSONEncoder().encode(t.params),
           let obj = try? JSONSerialization.jsonObject(with: data),
           let out = try? JSONSerialization.data(withJSONObject: obj,
                                                 options: [.prettyPrinted, .withoutEscapingSlashes]) {
            paramsJSON = String(decoding: out, as: UTF8.self)
        }
    }

    // ── 基本 ──────────────────────────────────────────────
    private var basics: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            FormRow(label: "名稱", hint: isNew ? "英數 _ -" : "") {
                if isNew {
                    TextField("weekly_report", text: $name).textFieldStyle(.roundedBorder)
                } else {
                    Text(name).font(Style.Face.monoBody)
                    Spacer()
                }
            }
            FormRow(label: "說明", hint: "給 AI 看的") {
                TextField("彙整本週的工作摘要", text: $description)
                    .textFieldStyle(.roundedBorder)
            }
            FormRow(label: "分類", hint: "可留空") {
                TextField("", text: $groupName).textFieldStyle(.roundedBorder)
            }
        }
    }

    // ── 步驟 ──────────────────────────────────────────────
    private var stepsSection: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            HStack {
                Text("步驟").font(.headline)
                Text("依序執行").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button {
                    steps.append(StepDraft())
                } label: {
                    Label("加一步", systemImage: "plus")
                }
                .buttonStyle(.borderless)
            }

            ForEach($steps) { $step in
                StepRow(step: $step,
                        index: steps.firstIndex(where: { $0.id == step.id }) ?? 0,
                        available: state.availableStepTools,
                        canRemove: steps.count > 1,
                        onRemove: { steps.removeAll { $0.id == step.id } })
            }

            Text("步驟參數可以用 {{ input.參數名 }} 引用這個複合工具自己的參數。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    // ── 參數 ──────────────────────────────────────────────
    private var paramsSection: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            Text("參數").font(.headline)
            Text("這個複合工具自己接受的參數,步驟裡用 {{ input.名稱 }} 取用。")
                .font(.caption).foregroundStyle(.secondary)
            CodeEditor(placeholder: #"[{"name":"week","type":"string","required":false}]"#,
                       text: $paramsJSON, minHeight: 70)
        }
    }

    // ── 試跑 ──────────────────────────────────────────────
    private var testSection: some View {
        VStack(alignment: .leading, spacing: Style.Space.row) {
            HStack {
                Text("試跑").font(.headline)
                Spacer()
                Button { runTest() } label: { Label("執行", systemImage: "play.fill") }
                    .disabled(busy || isNew)
                    .help(isNew ? "先儲存才能試跑" : "會真的執行每一個步驟")
            }
            CodeEditor(placeholder: #"{"week": "2026-W38"}"#, text: $testArgs, minHeight: 55)

            if let testResult {
                ScrollView {
                    Text(testResult)
                        .font(Style.Face.mono).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(Style.Space.row)
                }
                .frame(maxHeight: 200)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: Style.radius))
            }
        }
    }

    // ── 動作 ──────────────────────────────────────────────
    private func save() {
        guard let params = parsedParams(), let stepPayload = parsedSteps() else { return }
        Task {
            busy = true
            defer { busy = false }
            var body: [String: Any] = [
                "description": description,
                "params": params,
                "steps": stepPayload,
                "output": "collect",
                "group_name": groupName,
            ]
            do {
                if isNew {
                    body["name"] = name
                    _ = try await state.client.createCompositeTool(body)
                } else {
                    _ = try await state.client.updateCompositeTool(name, body)
                }
                banner = ("已儲存", .success)
                onSaved(name)
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }

    private func parsedParams() -> [Any]? {
        let text = paramsJSON.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return [] }
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [Any] else {
            banner = ("參數必須是合法的 JSON 陣列", .problem)
            return nil
        }
        return parsed
    }

    private func parsedSteps() -> [[String: Any]]? {
        var out: [[String: Any]] = []
        for (i, step) in steps.enumerated() {
            let id = step.stepID.trimmingCharacters(in: .whitespaces)
            guard !id.isEmpty, !step.tool.isEmpty else {
                banner = ("第 \(i + 1) 步還沒填 id 或工具", .problem)
                return nil
            }
            let text = step.argsJSON.trimmingCharacters(in: .whitespacesAndNewlines)
            var args: [String: Any] = [:]
            if !text.isEmpty {
                guard let parsed = try? JSONSerialization.jsonObject(
                        with: Data(text.utf8)) as? [String: Any] else {
                    banner = ("第 \(i + 1) 步的參數不是合法的 JSON 物件", .problem)
                    return nil
                }
                args = parsed
            }
            out.append(["id": id, "tool": step.tool, "args": args])
        }
        return out
    }

    private func delete() {
        Task {
            busy = true
            defer { busy = false }
            do {
                try await state.client.deleteCompositeTool(name)
                onDeleted()
            } catch {
                banner = (error.localizedDescription, .problem)
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
                testResult = try await state.client.testCompositeTool(name, arguments: args).display
            } catch {
                testResult = "失敗:\(error.localizedDescription)"
            }
        }
    }
}

/// 一列步驟。
struct StepDraft: Identifiable {
    let id = UUID()
    var stepID = ""
    var tool = ""
    var argsJSON = "{}"

    init() {}

    init(_ step: HubClient.CompositeStep) {
        stepID = step.id
        tool = step.tool
        if let args = step.args,
           let data = try? JSONSerialization.data(
               withJSONObject: args.mapValues(\.raw),
               options: [.prettyPrinted, .withoutEscapingSlashes]) {
            argsJSON = String(decoding: data, as: UTF8.self)
        }
    }
}

private struct StepRow: View {
    @Binding var step: StepDraft
    let index: Int
    let available: [AppState.StepTool]
    let canRemove: Bool
    let onRemove: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Style.Space.tight) {
            HStack(spacing: Style.Space.row) {
                Text("\(index + 1)")
                    .font(Style.Face.mono).foregroundStyle(.secondary)
                    .frame(width: 16)

                TextField("步驟 id", text: $step.stepID)
                    .textFieldStyle(.roundedBorder).frame(width: 110)

                // 從實際可用的工具挑,不必記住完整名稱
                Picker("", selection: $step.tool) {
                    Text("選擇工具…").tag("")
                    ForEach(available) { t in
                        Text(t.name).tag(t.name)
                    }
                }
                .labelsHidden()

                if canRemove {
                    Button(action: onRemove) { Image(systemName: "minus.circle") }
                        .buttonStyle(.borderless).help("移除這一步")
                }
            }

            if let hint = available.first(where: { $0.name == step.tool })?.hint, !hint.isEmpty {
                Text(hint)
                    .font(.caption2).foregroundStyle(.secondary)
                    .padding(.leading, 26)
            }

            CodeEditor(placeholder: #"{"id": "{{ input.issue_id }}"}"#,
                       text: $step.argsJSON, minHeight: 44)
                .padding(.leading, 26)
        }
        .padding(.vertical, Style.Space.tight)
    }
}

// ── Skill ─────────────────────────────────────────────────
private struct SkillSheet: View {
    @ObservedObject var state: AppState
    let toolName: String
    @Environment(\.dismiss) private var dismiss

    @State private var markdown = ""
    @State private var purpose = ""
    @State private var problem = ""
    @State private var banner: (String, AlertLine.Kind)?
    @State private var busy = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Skill —— \(toolName)").font(.headline)
                Spacer()
                Button("關閉") { dismiss() }
            }
            .padding(Style.Space.section)

            if let banner {
                AlertLine(text: banner.0, kind: banner.1, onDismiss: { self.banner = nil })
            }
            if !problem.isEmpty {
                AlertLine(text: problem, kind: .problem)
            }

            VStack(alignment: .leading, spacing: Style.Space.row) {
                Text("告訴 Claude 什麼時候該用這個工具。匯出成 Skill 套件後可以放進專案。")
                    .font(.caption).foregroundStyle(.secondary)
                CodeEditor(placeholder: "---\nname: …\ndescription: …\n---",
                           text: $markdown, minHeight: 280)
            }
            .padding(.horizontal, Style.Space.section)

            ActionBar {
                Button {
                    export()
                } label: {
                    Label("匯出 ZIP", systemImage: "square.and.arrow.down")
                }
                .disabled(busy)
            } trailing: {
                Button("儲存草稿") { save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || markdown.isEmpty)
            }
        }
        .frame(width: 640, height: 520)
        .task { await load() }
    }

    private func load() async {
        do {
            let doc = try await state.client.skill(toolName)
            markdown = doc.markdown
            purpose = doc.purpose
            problem = doc.problem
        } catch {
            banner = (error.localizedDescription, .problem)
        }
    }

    private func save() {
        Task {
            busy = true
            defer { busy = false }
            do {
                let doc = try await state.client.saveSkill(toolName, markdown: markdown,
                                                           purpose: purpose)
                problem = doc.problem
                banner = ("草稿已儲存", .success)
            } catch {
                banner = (error.localizedDescription, .problem)
            }
        }
    }

    private func export() {
        Task {
            busy = true
            defer { busy = false }
            do {
                let data = try await state.client.downloadSkill(toolName, markdown: markdown)
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "\(toolName).zip"
                panel.allowedContentTypes = [.zip]
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
