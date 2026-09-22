import AppKit
import SwiftUI

/// 主視窗。
///
/// 導覽用側邊欄而不是上方分頁 —— macOS 這個資訊量(七個區域)用分頁列會擠,
/// 而且分頁沒有地方放計數。側邊欄的計數讓「有幾個待確認」在任何畫面都看得到。
struct MainWindow: View {
    @ObservedObject var state: AppState
    @State private var section: Section = .servers

    enum Section: String, Hashable, CaseIterable {
        case servers, tools, logs, actions, customTools, composites, settings

        var title: String {
            switch self {
            case .servers: return "下游"
            case .tools: return "工具"
            case .logs: return "記錄"
            case .actions: return "待確認"
            case .customTools: return "自訂工具"
            case .composites: return "複合工具"
            case .settings: return "設定"
            }
        }

        var icon: String {
            switch self {
            case .servers: return "server.rack"
            case .tools: return "wrench.adjustable"
            case .logs: return "list.bullet.rectangle"
            case .actions: return "checkmark.shield"
            case .customTools: return "hammer"
            case .composites: return "square.stack.3d.up"
            case .settings: return "gearshape"
            }
        }
    }

    var body: some View {
        Group {
            switch state.backend {
            case .ready:
                // 自己排版而不是用 NavigationSplitView:這個視窗是手刻的 NSWindow +
                // NSHostingView,SwiftUI 在那種宿主裡裝不上 AppKit 的 sidebar,
                // 結果是側邊欄被畫成浮空的圓角清單,還會自動長出一顆會漂移的收合鈕。
                // 七個區域本來就該一直看得到,能收合沒有價值,索性不要那顆按鈕。
                HStack(spacing: 0) {
                    sidebar
                        .frame(width: 196)
                        .background(Palette.sunken)
                    Divider()
                    VStack(spacing: 0) {
                        // 暫停影響的是整個 Hub,所以橫跨所有畫面 —— 只在下游那頁講的話,
                        // 人在別頁按下工具開關時會以為那些改動正在生效。
                        if state.paused {
                            AlertLine(text: "已暫停 —— Claude 目前看不到任何工具",
                                      kind: .info,
                                      actionTitle: "繼續",
                                      action: { Task { await state.setPaused(false) } })
                        }
                        content
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            default:
                BackendNotReady(state: state)
            }
        }
        .background(Palette.ground)
        // 一次把整棵樹的強調色換掉。borderedProminent 按鈕、Toggle、List 選取
        // 預設都吃系統藍 —— 在這個冷調面板裡那是唯一不屬於這裡的顏色。
        .tint(Palette.accent)
    }

    // ── 側邊欄 ────────────────────────────────────────────
    private var sidebar: some View {
        VStack(alignment: .leading, spacing: Style.Space.section) {
            navGroup("監控") {
                NavItem(section: .servers, count: state.servers.count, selection: $section)
                NavItem(section: .tools, count: state.totalTools, selection: $section)
                NavItem(section: .logs, count: nil, selection: $section)
                // 待確認是唯一需要動手的東西 —— 有值時著色
                NavItem(section: .actions, count: state.pendingCount,
                        attention: state.pendingCount > 0, selection: $section)
            }
            navGroup("工具") {
                NavItem(section: .customTools, count: state.customTools.count,
                        selection: $section)
                NavItem(section: .composites, count: state.compositeTools.count,
                        selection: $section)
            }
            navGroup("其他") {
                NavItem(section: .settings, count: nil, selection: $section)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Style.Space.tight)
        .padding(.vertical, Style.Space.section)
    }

    private func navGroup<C: View>(_ title: String,
                                   @ViewBuilder content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title)
                .font(Style.Face.meta)
                .foregroundStyle(Palette.ink3)
                .padding(.horizontal, Style.Space.row)
                .padding(.bottom, Style.Space.tight)
            content()
        }
    }

    // ── 內容 ──────────────────────────────────────────────
    @ViewBuilder
    private var content: some View {
        switch section {
        case .servers: ServersView(state: state)
        case .tools: ToolsView(state: state)
        case .logs: LogsView(state: state)
        case .actions: ActionsView(state: state)
        case .customTools: CustomToolsTab(state: state)
        case .composites: CompositeToolsTab(state: state)
        case .settings: SettingsTab(state: state)
        }
    }
}

/// 側邊欄的一列。
///
/// 自己畫而不是靠 List 的 selection:系統的選取態是系統藍,而這份設計裡
/// 藍色是唯一不屬於這個冷調面板的顏色 —— 選取是可互動的狀態,該用強調色。
private struct NavItem: View {
    let section: MainWindow.Section
    let count: Int?
    var attention = false
    @Binding var selection: MainWindow.Section

    @State private var hovering = false

    private var isOn: Bool { selection == section }

    var body: some View {
        Button { selection = section } label: {
            HStack(spacing: Style.Space.row) {
                Image(systemName: section.icon)
                    .font(.system(size: 12))
                    .frame(width: 17)
                    .foregroundStyle(isOn ? Palette.accent : Palette.ink2)
                Text(section.title)
                    .font(.system(size: 13, weight: isOn ? .semibold : .regular))
                    .foregroundStyle(isOn ? Palette.ink : Palette.ink2)
                Spacer(minLength: Style.Space.tight)
                if let count {
                    Text("\(count)")
                        .font(Style.Face.number)
                        .monospacedDigit()
                        .foregroundStyle(attention ? Palette.warn : Palette.ink3)
                        .fontWeight(attention ? .semibold : .regular)
                }
            }
            .padding(.horizontal, Style.Space.row)
            .frame(height: 30)
            .background(
                RoundedRectangle(cornerRadius: Style.radius, style: .continuous)
                    .fill(isOn ? Palette.accent.opacity(0.16)
                               : (hovering ? Palette.line.opacity(0.45) : .clear)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .accessibilityLabel(section.title)
    }
}

/// 後端沒就緒時要說清楚原因,不是空白或永遠轉圈圈。
private struct BackendNotReady: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(spacing: Style.Space.section) {
            switch state.backend {
            case .starting:
                ProgressView().controlSize(.small)
                Text("後端啟動中…").font(Style.Face.body).foregroundStyle(Palette.ink2)
            case .failed(let reason):
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 26, weight: .light))
                    .foregroundStyle(Palette.down)
                Text("後端啟動失敗").font(Style.Face.sectionTitle)
                Text(reason)
                    .font(Style.Face.body).foregroundStyle(Palette.ink2)
                    .multilineTextAlignment(.center).textSelection(.enabled)
                    .frame(maxWidth: 420).lineSpacing(2)
                Button("重試") { state.supervisor.start() }
                    .buttonStyle(.borderedProminent)
            case .stopped:
                Text("後端未啟動").foregroundStyle(Palette.ink2)
                Button("啟動") { state.supervisor.start() }
            case .ready:
                EmptyView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(Style.Space.block)
        .background(Palette.ground)
    }
}

// ── 下游 ──────────────────────────────────────────────────
private struct ServersView: View {
    @ObservedObject var state: AppState
    @State private var busy: String?
    @State private var editing: HubClient.Server?
    @State private var creating = false
    @State private var deleting: HubClient.Server?
    @State private var lastChecked: String?

    /// 有問題的下游。一切正常時這是空的,畫面上就什麼都不多顯示。
    private var troubled: [HubClient.Server] {
        state.servers.filter { $0.enabled && $0.isErrored }
    }

    var body: some View {
        VStack(spacing: 0) {
            SectionBar(title: "下游") {
                Button("全部檢查") { checkAll() }
                Button {
                    creating = true
                } label: {
                    Label("新增下游", systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
            }

            if let err = state.lastError {
                AlertLine(text: err, kind: .problem, onDismiss: { state.lastError = nil })
            }

            // 有事才浮上來,而且直接帶修復動作
            ForEach(troubled) { s in
                AlertLine(text: "\(s.name) 連不上:\(s.statusDetail)",
                          actionTitle: "重新檢查", action: { check(s) })
            }

            if state.servers.isEmpty {
                EmptyState(icon: "server.rack", title: "還沒有下游",
                           hint: "加一台 MCP server,它的工具就會透過 Hub 一起曝露給 Claude。也可以從設定匯入現有的 Claude 設定。",
                           actionTitle: "新增第一台", action: { creating = true })
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(state.servers) { s in
                            row(s)
                        }
                    }
                }
            }

            StatusBar {
                Text(summary())
                Spacer()
                if let lastChecked { Text("最後檢查 \(lastChecked)") }
            }
        }
        .background(Palette.ground)
        .task { await state.refresh() }
        .sheet(isPresented: $creating) {
            ServerEditor(state: state, existing: nil) { _ in creating = false }
        }
        .sheet(item: $editing) { server in
            ServerEditor(state: state, existing: server) { _ in editing = nil }
        }
        .confirmationDialog("確定要刪除「\(deleting?.name ?? "")」?",
                            isPresented: .constant(deleting != nil)) {
            Button("刪除", role: .destructive) { performDelete() }
            Button("取消", role: .cancel) { deleting = nil }
        } message: {
            Text("連同它的工具快取與 OAuth 授權一起刪除。Claude 會立刻看不到這些工具。")
        }
    }

    private func row(_ s: HubClient.Server) -> some View {
        HubRow(health: health(s),
               title: s.name,
               detail: detailLine(s),
               detailIsMachine: !(s.isErrored && !s.statusDetail.isEmpty),
               dimmed: !s.enabled,
               count: s.toolCount.map { (String($0), "工具") }) {
            if busy == s.slug {
                ProgressView().controlSize(.small)
            } else {
                RowActions {
                    RowMenu {
                        Button("編輯…") { editing = s }
                        Button("重新檢查") { check(s) }
                        Button("重抓工具") { refresh(s) }
                        if s.authType == "oauth" {
                            Button("OAuth 授權…") { startOAuth(s) }
                        }
                        Divider()
                        Button("刪除…", role: .destructive) { deleting = s }
                    }
                }
            }
            HubSwitch(isOn: Binding(get: { s.enabled }, set: { setEnabled(s, $0) }))
        }
    }

    private func health(_ s: HubClient.Server) -> Health {
        guard s.enabled else { return .off }
        if s.isHealthy { return .ok }
        if s.isErrored { return .down }
        return .warn
    }

    /// 異常時顯示錯誤原因而不是網址 —— 那才是當下需要看到的東西。
    private func detailLine(_ s: HubClient.Server) -> String {
        if s.isErrored && !s.statusDetail.isEmpty { return s.statusDetail }
        if s.transport == "stdio" { return s.command.isEmpty ? "stdio" : s.command }
        return s.baseURL.replacingOccurrences(of: "https://", with: "")
                        .replacingOccurrences(of: "http://", with: "")
    }

    private func summary() -> String {
        let on = state.servers.filter(\.enabled).count
        return "\(state.servers.count) 台,\(on) 台啟用"
    }

    private func stamp() {
        lastChecked = Date().formatted(date: .omitted, time: .shortened)
    }

    // ── 動作 ──────────────────────────────────────────────
    private func setEnabled(_ s: HubClient.Server, _ on: Bool) {
        run(s) { _ = try await state.client.setEnabled(s.slug, on) }
    }

    private func check(_ s: HubClient.Server) {
        run(s) { _ = try await state.client.check(s.slug) }
        stamp()
    }

    private func refresh(_ s: HubClient.Server) {
        run(s) { _ = try await state.client.refreshServer(s.slug) }
    }

    private func checkAll() {
        Task { await state.checkAll(); stamp() }
    }

    private func startOAuth(_ s: HubClient.Server) {
        run(s) {
            let start = try await state.client.startOAuth(s.slug)
            // 授權要在瀏覽器完成 —— callback 由後端在 8765 接收
            if let url = URL(string: start.authorizationURL) {
                NSWorkspace.shared.open(url)
            }
        }
    }

    private func performDelete() {
        guard let s = deleting else { return }
        deleting = nil
        run(s) { try await state.client.deleteServer(s.slug) }
    }

    private func run(_ s: HubClient.Server, _ op: @escaping () async throws -> Void) {
        Task {
            busy = s.slug
            defer { busy = nil }
            do { try await op() }
            catch { state.lastError = error.localizedDescription }
            await state.refresh()
        }
    }
}

// ── 下游工具 ──────────────────────────────────────────────
private struct ToolsView: View {
    @ObservedObject var state: AppState
    @State private var slug = ""
    @State private var tools: [HubClient.Tool] = []
    @State private var query = ""
    @State private var loading = false
    @State private var error: String?

    /// 61 個工具全列出來只會讓人找不到重點,所以預設就給搜尋。
    private var shown: [HubClient.Tool] {
        guard !query.isEmpty else { return tools }
        let q = query.lowercased()
        return tools.filter {
            $0.name.lowercased().contains(q) || $0.shownDescription.lowercased().contains(q)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            SectionBar(title: "工具") {
                Picker("", selection: $slug) {
                    ForEach(state.servers) { Text($0.name).tag($0.slug) }
                }
                .labelsHidden().frame(width: 150)
                TextField("搜尋", text: $query)
                    .textFieldStyle(.roundedBorder).frame(width: 160)
            }

            if let error { AlertLine(text: error, onDismiss: { self.error = nil }) }

            if loading {
                ProgressView().controlSize(.small).frame(maxHeight: .infinity)
            } else if tools.isEmpty {
                EmptyState(icon: "wrench.adjustable",
                           title: slug.isEmpty ? "先選一台下游" : "這台下游還沒抓到工具",
                           hint: slug.isEmpty ? "" : "連線正常後會自動快取工具清單,也可以在下游那邊手動重抓。")
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(shown) { t in
                            HubRow(health: t.enabled ? .ok : .off,
                                   title: t.name,
                                   detail: t.shownDescription.isEmpty ? "沒有說明" : t.shownDescription,
                                   detailIsMachine: false,
                                   dimmed: !t.enabled) {
                                RowActions {
                                    Toggle("需確認", isOn: Binding(
                                        get: { t.needsConfirm },
                                        set: { v in update(t.name, needsConfirm: v) }))
                                        .toggleStyle(.checkbox)
                                        .font(Style.Face.meta)
                                }
                                HubSwitch(isOn: Binding(get: { t.enabled },
                                        set: { v in update(t.name, enabled: v) }))
                            }
                        }
                    }
                }
            }

            StatusBar {
                Text(query.isEmpty ? "\(tools.count) 個工具"
                                   : "\(shown.count) / \(tools.count) 個工具")
                Spacer()
                Button("全部啟用") { bulk(true) }.buttonStyle(.link).disabled(slug.isEmpty)
                Button("全部停用") { bulk(false) }.buttonStyle(.link).disabled(slug.isEmpty)
            }
        }
        .background(Palette.ground)
        .task { await pick() }
        .onChange(of: slug) { _, _ in Task { await load() } }
    }

    private func pick() async {
        if state.servers.isEmpty { await state.refresh() }
        if slug.isEmpty, let first = state.servers.first { slug = first.slug }
        await load()
    }

    private func load() async {
        guard !slug.isEmpty else { return }
        loading = true
        defer { loading = false }
        do { tools = try await state.client.tools(slug); error = nil }
        catch { self.error = error.localizedDescription }
    }

    private func update(_ name: String, enabled: Bool? = nil, needsConfirm: Bool? = nil) {
        Task {
            do { _ = try await state.client.setTool(slug, name, enabled: enabled,
                                                    needsConfirm: needsConfirm) }
            catch { self.error = error.localizedDescription }
            await load()
        }
    }

    private func bulk(_ enabled: Bool) {
        Task {
            do { tools = try await state.client.setAllTools(slug, enabled: enabled) }
            catch { self.error = error.localizedDescription }
        }
    }
}

// ── 呼叫記錄 ──────────────────────────────────────────────
private struct LogsView: View {
    @ObservedObject var state: AppState
    @State private var page: HubClient.LogPage?
    @State private var current = 1
    @State private var errorsOnly = false
    @State private var error: String?

    var body: some View {
        VStack(spacing: 0) {
            SectionBar(title: "記錄") {
                Toggle("只看錯誤", isOn: $errorsOnly)
                    .toggleStyle(.checkbox).font(Style.Face.body)
            }

            if let error { AlertLine(text: error, onDismiss: { self.error = nil }) }

            if (page?.rows ?? []).isEmpty {
                EmptyState(icon: "list.bullet.rectangle",
                           title: errorsOnly ? "沒有錯誤記錄" : "還沒有呼叫記錄",
                           hint: errorsOnly ? "" : "Claude 每次透過 Hub 呼叫工具都會記在這裡。")
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(page?.rows ?? []) { r in
                            HubRow(health: r.isError ? .down : .ok,
                                   title: r.qualifiedName,
                                   detail: r.isError && !r.error.isEmpty
                                       ? r.error
                                       : r.time.replacingOccurrences(of: "T", with: " "),
                                   detailIsMachine: !r.isError,
                                   count: r.durationMs.map { (String($0), "ms") }) {
                                EmptyView()
                            }
                        }
                    }
                }
            }

            StatusBar {
                if let p = page {
                    Text("共 \(p.total) 筆")
                    if p.errors > 0 {
                        Text("\(p.errors) 筆錯誤").foregroundStyle(Palette.down)
                    }
                }
                Spacer()
                Button("上一頁") { current -= 1; reload() }
                    .buttonStyle(.link).disabled(current <= 1)
                Text("\(page?.page ?? current) / \(page?.pages ?? 1)").monospacedDigit()
                Button("下一頁") { current += 1; reload() }
                    .buttonStyle(.link).disabled(current >= (page?.pages ?? 1))
            }
        }
        .background(Palette.ground)
        .task { reload() }
        .onChange(of: errorsOnly) { _, _ in current = 1; reload() }
    }

    private func reload() {
        Task {
            do {
                page = try await state.client.logs(page: current, errorsOnly: errorsOnly)
                current = page?.page ?? current
                error = nil
            } catch { self.error = error.localizedDescription }
        }
    }
}

// ── 待確認 ────────────────────────────────────────────────
private struct ActionsView: View {
    @ObservedObject var state: AppState
    @State private var error: String?

    private var waiting: [HubClient.Action] { state.actions.filter(\.isWaiting) }

    var body: some View {
        VStack(spacing: 0) {
            SectionBar(title: "待確認") {
                Button("重新整理") { Task { await state.refresh() } }
            }

            if let error { AlertLine(text: error, onDismiss: { self.error = nil }) }

            if waiting.isEmpty {
                EmptyState(icon: "checkmark.circle", title: "沒有待處理的項目",
                           hint: "標記為「需確認」的工具被呼叫時,會先停在這裡等你決定。")
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(waiting) { a in
                            HubRow(health: .warn,
                                   title: a.tool,
                                   detail: a.createdAt.replacingOccurrences(of: "T", with: " ")) {
                                Button("拒絕") { decide(a, false) }
                                Button("核准") { decide(a, true) }
                                    .buttonStyle(.borderedProminent)
                            }
                        }
                    }
                }
            }

            StatusBar {
                Text("\(waiting.count) 筆待處理,共 \(state.actions.count) 筆")
            }
        }
        .background(Palette.ground)
        .task { await state.refresh() }
    }

    /// 核准只改狀態,實際執行發生在 hub_server 程序。
    private func decide(_ a: HubClient.Action, _ approve: Bool) {
        Task { await state.decide(a.id, approve: approve) }
    }
}
