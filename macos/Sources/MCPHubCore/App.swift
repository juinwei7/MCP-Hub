import AppKit
import SwiftUI

/// MCP Hub 的 macOS 外殼 —— app 狀態、視窗、生命週期。
///
/// 職責:啟動並監督 Python 後端、在選單列顯示狀態、提供一個最小視窗。
/// 主視窗的實作在 MainWindow.swift。

@MainActor
final class AppState: ObservableObject {
    @Published var backend: BackendSupervisor.State = .stopped
    @Published var servers: [HubClient.Server] = []
    @Published var actions: [HubClient.Action] = []
    @Published var customTools: [HubClient.CustomTool] = []
    @Published var compositeTools: [HubClient.CompositeTool] = []
    @Published var categories: HubClient.CategoryOverview?
    @Published var lastError: String?
    /// Hub 層級的暫停。和「把每台下游停用」不同 —— 它不動下游的啟用狀態。
    @Published var paused = false
    /// 預設開啟 —— 這個快捷鍵存在的理由就是選單列圖示可能看不到,
    /// 要人先找到設定才能打開它,等於沒解決問題。
    @Published var hotKeyEnabled =
        UserDefaults.standard.object(forKey: AppState.hotKeyDefaultsKey) as? Bool ?? true

    fileprivate static let hotKeyDefaultsKey = "globalHotKeyEnabled"

    let supervisor = BackendSupervisor()
    let notifier = Notifier()
    private var refreshTask: Task<Void, Never>?

    var client: HubClient { HubClient(token: supervisor.token) }

    var healthy: Int { servers.filter { $0.enabled && $0.isHealthy }.count }
    var errored: Int { servers.filter { $0.enabled && $0.isErrored }.count }

    var pendingCount: Int { actions.filter(\.isWaiting).count }
    var totalTools: Int { servers.compactMap(\.toolCount).reduce(0, +) }

    func boot() {
        notifier.onDecision = { [weak self] id, approve in
            Task { await self?.decide(id, approve: approve) }
        }
        notifier.start()
        supervisor.onStateChange = { [weak self] state in
            Task { @MainActor in
                self?.backend = state
                if state == .ready { self?.startRefreshing() }
            }
        }
        applyHotKeySetting()
        supervisor.start()
    }

    /// 快捷鍵只在後端就緒時才有意義,但註冊本身跟後端無關 —— 先註冊,
    /// 按下去時如果還沒就緒就什麼也不做,比「有時候有、有時候沒有」好預測。
    func applyHotKeySetting() {
        UserDefaults.standard.set(hotKeyEnabled, forKey: Self.hotKeyDefaultsKey)
        guard hotKeyEnabled else {
            GlobalHotKey.shared.unregister()
            return
        }
        if let problem = GlobalHotKey.shared.register({ [weak self] in
            guard let self, case .ready = self.backend else { return }
            Task { await self.setPaused(!self.paused) }
        }) {
            lastError = problem
            hotKeyEnabled = false   // 註冊失敗就別讓設定顯示成「開著」
            UserDefaults.standard.set(false, forKey: Self.hotKeyDefaultsKey)
        }
    }

    private func startRefreshing() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    func refresh() async {
        do {
            // 工具數也一起抓:側邊欄一直顯示著這兩個計數,只在點進分頁時才載入的話,
            // 在那之前它們會是 0 —— 錯的數字比沒有數字更糟。
            let (srv, act, custom, comp, isPaused) = try await (
                client.servers(), client.actions(),
                client.customTools(), client.compositeTools(), client.paused())
            servers = srv
            actions = act
            customTools = custom
            compositeTools = comp
            paused = isPaused
            lastError = nil

            notifier.sync(actions: act, servers: srv)
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// 先改本地狀態再送出:選單列按下去要立刻反映,不能等一輪 round trip。
    /// 失敗就回捲,並把原因留在 lastError。
    func setPaused(_ want: Bool) async {
        let before = paused
        paused = want
        do {
            paused = try await client.setPaused(want)
        } catch {
            paused = before
            lastError = error.localizedDescription
        }
    }

    func loadCustomTools() async {
        do { customTools = try await client.customTools() }
        catch { lastError = error.localizedDescription }
    }

    /// 複合工具的步驟可以挑的工具清單。
    struct StepTool: Identifiable {
        let name: String
        let hint: String
        var id: String { name }
    }

    @Published var availableStepTools: [StepTool] = []

    func loadAvailableStepTools() async {
        do {
            availableStepTools = try await client.stepTools().map {
                let params = $0.params.map(\.name).joined(separator: ", ")
                let bits = [$0.description, params.isEmpty ? "" : "參數:\(params)"]
                return StepTool(name: $0.name,
                                hint: bits.filter { !$0.isEmpty }.joined(separator: " · "))
            }
        } catch { lastError = error.localizedDescription }
    }

    func loadCompositeTools() async {
        do { compositeTools = try await client.compositeTools() }
        catch { lastError = error.localizedDescription }
    }

    func loadCategories() async {
        do { categories = try await client.categories() }
        catch { lastError = error.localizedDescription }
    }

    func decide(_ id: String, approve: Bool) async {
        do { _ = try await client.decide(id, approve: approve) }
        catch { lastError = error.localizedDescription }
        await refresh()
    }

    func checkAll() async {
        _ = try? await client.checkAll()
        await refresh()
    }

    func openAdmin() {
        NSWorkspace.shared.open(URL(string: "http://localhost:\(BackendSupervisor.port)/")!)
    }

    func shutdown() {
        refreshTask?.cancel()
        supervisor.stop()
    }
}

/// 主視窗的持有者。選單列 app 沒有 Dock 圖示,視窗要自己管。
@MainActor
final class WindowController {
    private var window: NSWindow?
    private let state: AppState

    init(state: AppState) { self.state = state }

    func show() {
        if window == nil {
            let w = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 1060, height: 680),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered, defer: false)
            w.title = "MCP Hub"
            w.contentView = NSHostingView(rootView: MainWindow(state: state))
            w.isReleasedWhenClosed = false   // 關掉只是隱藏,選單列還要能再開
            // 側邊欄 196 + 分頁內欄 272 + 編輯區,再窄下去右邊的表單就開始換行
            w.minSize = NSSize(width: 940, height: 580)
            w.center()
            // 記住使用者調過的大小,下次開回同一個位置
            w.setFrameAutosaveName("MCPHubMainWindow")
            window = w
        }
        // accessory 政策下要主動搶焦點,否則視窗會開在背景
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let state = AppState()
    private var menuBar: MenuBarController?
    private var windows: WindowController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let windows = WindowController(state: state)
        self.windows = windows
        menuBar = MenuBarController(state: state, windows: windows)
        state.boot()
        windows.show()   // 第一次啟動先把視窗打開,不然只有選單列圖示會讓人以為沒反應
        installSignalHandlers()
    }

    /// app 結束時一定要把子程序收掉 —— 這是整個外殼最重要的職責。
    func applicationWillTerminate(_ notification: Notification) {
        state.shutdown()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false   // 關掉視窗不等於結束 —— 選單列還在
    }

    /// 在 Finder 再開一次 app 就把視窗叫回來。
    ///
    /// 沒有這個的話會有一個走不出來的死角:LSUIElement 沒有 Dock 圖示,關掉視窗
    /// 又不會結束程序,而選單列圖示在擁擠的選單列上(或被 Ice / Bartender 這類
    /// 工具收起來時)可能根本看不到 —— app 跑著,但使用者沒有任何入口。
    /// 預設的 reopen 行為救不回來:視窗物件還在(isReleasedWhenClosed = false),
    /// 但 AppKit 不會自己把它 order front。
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { windows?.show() }
        return true
    }

    /// 從終端機跑時會收到 SIGINT/SIGTERM,預設行為不會經過 applicationWillTerminate。
    /// SIGKILL 攔不住,那條路徑靠 PID 檔在下次啟動時接管。
    private func installSignalHandlers() {
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in
                // queue 就是 main,所以這裡確實在 main actor 上
                MainActor.assumeIsolated {
                    self?.state.shutdown()
                    exit(0)
                }
            }
            source.resume()
            signalSources.append(source)
        }
    }

    private var signalSources: [DispatchSourceSignal] = []
}

/// 啟動整個 app。放在 library target 裡,讓可測試的邏輯不必跟頂層程式碼綁在一起。
public func runApp() {
    MainActor.assumeIsolated {
        let delegate = AppDelegate()
        let app = NSApplication.shared
        app.delegate = delegate
        app.setActivationPolicy(.accessory)   // 選單列 app,不佔 Dock
        objc_setAssociatedObject(app, "mcphub.delegate", delegate, .OBJC_ASSOCIATION_RETAIN)
        app.run()
    }
}
