import AppKit
import SwiftUI

/// 主視窗 —— 日常管理不必再開瀏覽器。
///
/// 三個設計原則:顯示需要注意的事而非所有資訊;每個動作都有明確結果;
/// 把「下游連不上」當常態處理,而不是例外。

struct MainWindow: View {
    @ObservedObject var state: AppState

    var body: some View {
        Group {
            switch state.backend {
            case .ready:
                TabView {
                    ServersTab(state: state).tabItem { Label("下游", systemImage: "server.rack") }
                    ToolsTab(state: state).tabItem { Label("工具", systemImage: "wrench.and.screwdriver") }
                    LogsTab(state: state).tabItem { Label("記錄", systemImage: "list.bullet.rectangle") }
                    ActionsTab(state: state).tabItem { Label("待確認", systemImage: "checkmark.shield") }
                    CustomToolsTab(state: state).tabItem {
                        Label("自訂工具", systemImage: "wrench.and.screwdriver")
                    }
                    CompositeToolsTab(state: state).tabItem {
                        Label("複合工具", systemImage: "square.stack.3d.up")
                    }
                    SettingsTab(state: state).tabItem { Label("設定", systemImage: "gearshape") }
                }
                .padding(.top, 8)
            default:
                BackendNotReady(state: state)
            }
        }
        .frame(minWidth: 820, minHeight: 520)
    }
}

/// 後端沒就緒時要說清楚原因,不是空白或永遠轉圈圈。
private struct BackendNotReady: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(spacing: 14) {
            switch state.backend {
            case .starting:
                ProgressView()
                Text("後端啟動中…").foregroundStyle(.secondary)
            case .failed(let reason):
                Image(systemName: "exclamationmark.triangle")
                    .font(.largeTitle).foregroundStyle(.orange)
                Text("後端啟動失敗").font(.headline)
                Text(reason)
                    .font(.callout).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .textSelection(.enabled)
                    .frame(maxWidth: 440)
                Button("重試") { state.supervisor.start() }
            case .stopped:
                Text("後端未啟動").foregroundStyle(.secondary)
                Button("啟動") { state.supervisor.start() }
            case .ready:
                EmptyView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}

// ── 下游 ──────────────────────────────────────────────────
private struct ServersTab: View {
    @ObservedObject var state: AppState
    @State private var busy: String?
    @State private var editing: HubClient.Server?
    @State private var creating = false
    @State private var deleting: HubClient.Server?

    var body: some View {
        VStack(spacing: 0) {
            if let err = state.lastError {
                Banner(text: err, kind: .error, onDismiss: { state.lastError = nil })
            }
            List {
                ForEach(state.servers) { s in
                    HStack(spacing: 10) {
                        StatusDot(kind: dotKind(s), help: s.statusDetail.isEmpty ? s.status : s.statusDetail)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(s.name).fontWeight(.medium)
                            Text(detailLine(s))
                                .font(.caption).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle)
                        }
                        Spacer()
                        if busy == s.slug {
                            ProgressView().controlSize(.small)
                        } else {
                            Menu {
                                Button("編輯…") { editing = s }
                                Button("重新檢查") { check(s) }
                                Button("重抓工具") { refresh(s) }
                                if s.authType == "oauth" {
                                    Button("OAuth 授權…") { startOAuth(s) }
                                }
                                Divider()
                                Button("刪除…", role: .destructive) { deleting = s }
                            } label: {
                                Image(systemName: "ellipsis.circle")
                            }
                            .menuStyle(.borderlessButton)
                            .fixedSize()
                        }
                        Toggle("", isOn: Binding(
                            get: { s.enabled },
                            set: { setEnabled(s, $0) }
                        )).labelsHidden()
                    }
                    .padding(.vertical, 3)
                }
            }
            Divider()
            HStack {
                Text(summary()).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("全部檢查") { Task { await state.checkAll() } }
                Button {
                    creating = true
                } label: {
                    Label("新增下游", systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(8)
        }
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

    private func refresh(_ s: HubClient.Server) {
        Task {
            busy = s.slug
            defer { busy = nil }
            do { _ = try await state.client.refreshServer(s.slug) }
            catch { state.lastError = error.localizedDescription }
            await state.refresh()
        }
    }

    private func startOAuth(_ s: HubClient.Server) {
        Task {
            busy = s.slug
            defer { busy = nil }
            do {
                let start = try await state.client.startOAuth(s.slug)
                // 授權要在瀏覽器完成 —— callback 由後端在 8765 接收
                if let url = URL(string: start.authorizationURL) {
                    NSWorkspace.shared.open(url)
                }
            } catch {
                state.lastError = error.localizedDescription
            }
        }
    }

    private func performDelete() {
        guard let s = deleting else { return }
        deleting = nil
        Task {
            busy = s.slug
            defer { busy = nil }
            do { try await state.client.deleteServer(s.slug) }
            catch { state.lastError = error.localizedDescription }
            await state.refresh()
        }
    }

    private func dotKind(_ s: HubClient.Server) -> StatusDot.Kind {
        guard s.enabled else { return .off }
        if s.isHealthy { return .ok }
        if s.isErrored { return .bad }
        return .warn
    }

    /// 異常時顯示錯誤原因而不是網址 —— 那才是當下需要看到的東西。
    private func detailLine(_ s: HubClient.Server) -> String {
        if s.isErrored && !s.statusDetail.isEmpty { return s.statusDetail }
        let count = s.toolCount ?? 0
        return "\(s.baseURL.isEmpty ? s.transport : s.baseURL) · \(count) 個工具"
    }

    private func summary() -> String {
        let on = state.servers.filter(\.enabled).count
        return "\(state.servers.count) 台下游,\(on) 台啟用,\(state.healthy) 台健康,\(state.errored) 台異常"
    }

    private func setEnabled(_ s: HubClient.Server, _ on: Bool) {
        Task {
            busy = s.slug
            defer { busy = nil }
            do { _ = try await state.client.setEnabled(s.slug, on) }
            catch { state.lastError = error.localizedDescription }
            await state.refresh()
        }
    }

    private func check(_ s: HubClient.Server) {
        Task {
            busy = s.slug
            defer { busy = nil }
            do { _ = try await state.client.check(s.slug) }
            catch { state.lastError = error.localizedDescription }
            await state.refresh()
        }
    }
}

// ── 工具 ──────────────────────────────────────────────────
private struct ToolsTab: View {
    @ObservedObject var state: AppState
    @State private var slug: String = ""
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
            HStack {
                Picker("下游", selection: $slug) {
                    ForEach(state.servers) { Text($0.name).tag($0.slug) }
                }
                .frame(maxWidth: 220)
                TextField("搜尋工具", text: $query).textFieldStyle(.roundedBorder)
            }
            .padding(8)

            if let error { Banner(text: error, kind: .error) }

            if loading {
                ProgressView().frame(maxHeight: .infinity)
            } else if tools.isEmpty {
                Text(slug.isEmpty ? "請先選擇下游" : "這台下游沒有快取到工具")
                    .foregroundStyle(.secondary).frame(maxHeight: .infinity)
            } else {
                List(shown) { t in
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(t.name).font(.system(.body, design: .monospaced))
                            if !t.shownDescription.isEmpty {
                                Text(t.shownDescription)
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                        Spacer()
                        Toggle("需確認", isOn: Binding(
                            get: { t.needsConfirm },
                            set: { v in update(t) { try await state.client.setTool(slug, t.name, needsConfirm: v) } }
                        )).toggleStyle(.checkbox)
                        Toggle("", isOn: Binding(
                            get: { t.enabled },
                            set: { v in update(t) { try await state.client.setTool(slug, t.name, enabled: v) } }
                        )).labelsHidden()
                    }
                    .padding(.vertical, 2)
                }
            }

            Divider()
            HStack {
                Text("\(shown.count) / \(tools.count) 個工具")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("全部啟用") { bulk(true) }.disabled(slug.isEmpty)
                Button("全部停用") { bulk(false) }.disabled(slug.isEmpty)
                Button("重抓") { reloadFromDownstream() }.disabled(slug.isEmpty)
            }
            .padding(8)
        }
        .task { await pickInitialServer() }
        .onChange(of: slug) { _, _ in Task { await load() } }
    }

    private func pickInitialServer() async {
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

    private func update(_ t: HubClient.Tool, _ op: @escaping () async throws -> HubClient.Tool) {
        Task {
            do { _ = try await op() } catch { self.error = error.localizedDescription }
            await load()
        }
    }

    private func bulk(_ enabled: Bool) {
        Task {
            do { tools = try await state.client.setAllTools(slug, enabled: enabled) }
            catch { self.error = error.localizedDescription }
        }
    }

    private func reloadFromDownstream() {
        Task {
            loading = true
            do { _ = try await state.client.refresh(slug); error = nil }
            catch { self.error = error.localizedDescription }
            loading = false
            await load()
            await state.refresh()
        }
    }
}

// ── 記錄 ──────────────────────────────────────────────────
private struct LogsTab: View {
    @ObservedObject var state: AppState
    @State private var page: HubClient.LogPage?
    @State private var current = 1
    @State private var errorsOnly = false
    @State private var error: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Toggle("只看錯誤", isOn: $errorsOnly)
                Spacer()
                if let p = page {
                    Text("共 \(p.total) 筆,其中 \(p.errors) 筆錯誤")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(8)

            if let error { Banner(text: error, kind: .error) }

            List(page?.rows ?? []) { r in
                HStack(spacing: 10) {
                    Circle().fill(r.isError ? Color.red : Color.green.opacity(0.7))
                        .frame(width: 7, height: 7)
                    Text(r.time.replacingOccurrences(of: "T", with: " "))
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Text(r.qualifiedName).font(.system(.body, design: .monospaced)).lineLimit(1)
                    Spacer()
                    if !r.error.isEmpty {
                        Text(r.error).font(.caption).foregroundStyle(.red)
                            .lineLimit(1).truncationMode(.middle).frame(maxWidth: 200)
                    }
                    if let ms = r.durationMs {
                        Text("\(ms)ms").font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 1)
            }

            Divider()
            HStack {
                // 558 筆且持續成長 —— 一定要分頁
                Button("上一頁") { current -= 1; reload() }.disabled(current <= 1)
                Text("第 \(page?.page ?? current) / \(page?.pages ?? 1) 頁").font(.caption)
                Button("下一頁") { current += 1; reload() }
                    .disabled(current >= (page?.pages ?? 1))
                Spacer()
                Button("重新整理") { reload() }
            }
            .padding(8)
        }
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
private struct ActionsTab: View {
    @ObservedObject var state: AppState
    @State private var error: String?

    // 用共用狀態,否則選單列的數字與這裡會對不起來
    private var waiting: [HubClient.Action] { state.actions.filter(\.isWaiting) }

    var body: some View {
        VStack(spacing: 0) {
            if let error { Banner(text: error, kind: .error) }

            if waiting.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "checkmark.circle").font(.largeTitle).foregroundStyle(.green)
                    Text("沒有待處理的確認項目").foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(waiting) { a in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(a.tool).font(.system(.body, design: .monospaced))
                            Text(a.createdAt.replacingOccurrences(of: "T", with: " "))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("拒絕") { decide(a, false) }
                        Button("核准") { decide(a, true) }.keyboardShortcut(.defaultAction)
                    }
                    .padding(.vertical, 2)
                }
            }

            Divider()
            HStack {
                Text("\(waiting.count) 筆待處理,共 \(state.actions.count) 筆")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("重新整理") { reload() }
            }
            .padding(8)
        }
        .task { reload() }
    }

    /// 核准只改狀態,實際執行發生在 hub_server 程序。
    private func decide(_ a: HubClient.Action, _ approve: Bool) {
        Task { await state.decide(a.id, approve: approve) }
    }

    private func reload() {
        Task { await state.refresh() }
    }
}
