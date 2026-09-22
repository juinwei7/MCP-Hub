import AppKit
import Combine
import SwiftUI

/// 選單列項目 —— 原生相對於瀏覽器分頁最直接的價值:不用開網頁就看得到 Hub 活著沒。
@MainActor
final class MenuBarController {
    private let item: NSStatusItem
    private let state: AppState
    private var cancellables = Set<AnyCancellable>()
    private let windows: WindowController

    init(state: AppState, windows: WindowController) {
        self.state = state
        self.windows = windows
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "puzzlepiece.extension",
                                     accessibilityDescription: "MCP Hub")
        item.button?.imagePosition = .imageLeading
        rebuildMenu()

        // 狀態一變就重畫,不輪詢 UI
        state.$servers.sink { [weak self] _ in self?.rebuildMenu() }.store(in: &cancellables)
        state.$backend.sink { [weak self] _ in self?.rebuildMenu() }.store(in: &cancellables)
        state.$paused.sink { [weak self] _ in self?.rebuildMenu() }.store(in: &cancellables)
    }

    private func rebuildMenu() {
        item.button?.title = summaryTitle()
        item.button?.image = NSImage(systemSymbolName: iconName(),
                                     accessibilityDescription: "MCP Hub")

        let menu = NSMenu()
        menu.addItem(header(backendLine()))

        if case .ready = state.backend {
            // 總開關擺在最上面,和下游清單之間隔一條線 —— 它管的是整個 Hub,
            // 不是其中某一台。
            menu.addItem(.separator())
            let pause = action(state.paused ? "繼續" : "暫停", #selector(togglePaused))
            // 快捷鍵標在這裡是唯一會被看到的地方 —— 沒標的話沒人知道它存在
            if GlobalHotKey.shared.isRegistered {
                pause.keyEquivalent = "p"
                pause.keyEquivalentModifierMask = [.control, .option, .command]
            }
            menu.addItem(pause)

            menu.addItem(.separator())
            if state.servers.isEmpty {
                menu.addItem(header("尚未加入任何下游"))
            } else {
                for s in state.servers {
                    menu.addItem(serverItem(s))
                }
            }
            menu.addItem(.separator())
            if state.pendingCount > 0 {
                menu.addItem(header("\(state.pendingCount) 筆待確認"))
                if !state.notifier.canNotify {
                    menu.addItem(header("(系統通知未啟用,只能從這裡看到)"))
                }
            }
            menu.addItem(.separator())
            menu.addItem(action("開啟主視窗", #selector(openWindow)))
            menu.addItem(action("全部重新檢查", #selector(checkAll)))
            menu.addItem(action("複製接入 Claude 的指令", #selector(copyClaudeCommand)))
        } else if case .failed(let reason) = state.backend {
            menu.addItem(.separator())
            for line in reason.split(separator: "\n") {
                menu.addItem(header(String(line)))
            }
            menu.addItem(.separator())
            menu.addItem(action("重試", #selector(retry)))
        }

        menu.addItem(.separator())
        if LoginItem.available {
            let li = action("開機時自動啟動", #selector(toggleLoginItem))
            li.state = LoginItem.isEnabled ? .on : .off
            menu.addItem(li)
        }
        menu.addItem(action("結束 MCP Hub", #selector(quit)))
        item.menu = menu
    }

    /// 圖示只表達一件事:現在該不該理它。
    ///
    /// 暫停排在待辦之前 —— 暫停時工具根本出不去,那幾筆待確認也動不了,
    /// 先讓人看到「是我自己關掉的」比較有用。
    private func iconName() -> String {
        if case .failed = state.backend { return "exclamationmark.triangle.fill" }
        if state.paused { return "pause.circle" }
        // 通知送不出去時,選單列就是唯一的提示管道,有待辦就換成實心圖示
        if state.pendingCount > 0 && !state.notifier.canNotify {
            return "exclamationmark.triangle.fill"
        }
        return "puzzlepiece.extension"
    }

    /// 選單列只顯示「需要注意的事」:一切正常時不放數字,有異常才跳出來。
    private func summaryTitle() -> String {
        switch state.backend {
        case .ready:
            if state.paused { return "" }   // 圖示已經說了,不必再掛數字
            // 待確認優先於異常 —— 那是需要你動手的,異常只是需要你知道
            if state.pendingCount > 0 { return " \(state.pendingCount)" }
            return state.errored > 0 ? " \(state.errored)" : ""
        case .starting:
            return " …"
        case .failed:
            return " !"
        case .stopped:
            return ""
        }
    }

    private func backendLine() -> String {
        switch state.backend {
        case .stopped: return "後端未啟動"
        case .starting: return "後端啟動中…"
        case .ready:
            if state.paused { return "已暫停 · Claude 目前看不到任何工具" }
            let total = state.servers.filter { $0.enabled }.count
            return "後端正常 · \(state.healthy)/\(total) 台下游健康"
        case .failed: return "後端啟動失敗"
        }
    }

    private func serverItem(_ s: HubClient.Server) -> NSMenuItem {
        let dot = !s.enabled ? "○" : (s.isHealthy ? "●" : (s.isErrored ? "▲" : "○"))
        let mi = NSMenuItem(title: "\(dot)  \(s.name)", action: #selector(toggleServer(_:)),
                            keyEquivalent: "")
        mi.target = self
        mi.representedObject = s.slug
        mi.state = s.enabled ? .on : .off
        if s.isErrored && !s.statusDetail.isEmpty {
            mi.toolTip = s.statusDetail
        }
        return mi
    }

    private func header(_ text: String) -> NSMenuItem {
        let mi = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        mi.isEnabled = false
        return mi
    }

    private func action(_ title: String, _ sel: Selector) -> NSMenuItem {
        let mi = NSMenuItem(title: title, action: sel, keyEquivalent: "")
        mi.target = self
        return mi
    }

    // MARK: - 動作

    @objc private func toggleServer(_ sender: NSMenuItem) {
        guard let slug = sender.representedObject as? String,
              let srv = state.servers.first(where: { $0.slug == slug }) else { return }
        Task {
            _ = try? await state.client.setEnabled(slug, !srv.enabled)
            await state.refresh()
        }
    }

    @objc private func togglePaused() {
        Task { await state.setPaused(!state.paused) }
    }

    @objc private func toggleLoginItem() {
        if let problem = LoginItem.set(!LoginItem.isEnabled) {
            state.lastError = problem
        }
        rebuildMenu()
    }

    @objc private func openWindow() {
        windows.show()
    }

    @objc private func checkAll() {
        Task { await state.checkAll() }
    }

    /// 把 claude mcp add 的指令放進剪貼簿。
    ///
    /// 環境變數不能省:hub_server 是 Claude 獨立啟動的,不經過這個 app。
    /// 少了它們,聚合器會讀到專案目錄那份空的 actions.db,使用者在 app 裡的
    /// 設定一個都不會生效 —— 而且不會有任何錯誤訊息,只是「工具怎麼都沒出現」。
    @objc private func copyClaudeCommand() {
        let env = ProcessInfo.processInfo.environment
        let command = BackendSupervisor.claudeAddCommand(
            dataDir: BackendSupervisor.dataDirectory.path,
            python: env["MCPHUB_PYTHON"] ?? "<專案>/.venv/bin/python",
            repo: env["MCPHUB_REPO"] ?? "<專案路徑>")

        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(command, forType: .string)
        state.lastError = "已複製接入指令到剪貼簿,貼到終端機執行即可。"
    }

    @objc private func retry() {
        state.supervisor.start()
    }

    @objc private func quit() {
        NSApplication.shared.terminate(nil)
    }
}
