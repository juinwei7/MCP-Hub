import Foundation

/// 管理 Python 後端子程序的生命週期。
///
/// 這是整個 app 技術風險最高的部分,不是介面。孤兒程序是實際會發生的問題 ——
/// 開發這個功能時,機器上同時有 7 隻 hub_server 殘留著。
///
/// macOS 沒有 Linux 的 PDEATHSIG,子程序無法自動跟著父程序死。所以防線有兩道:
///   1. 能攔截的結束路徑(正常退出、SIGTERM/SIGINT)一律主動回收
///   2. 攔不住的路徑(SIGKILL、當機)靠 PID 檔,下次啟動時接管並收掉殘留
final class BackendSupervisor {

    enum State: Equatable {
        case stopped
        case starting
        case ready
        case failed(String)
    }

    /// 後端必須固定這個 port —— auth.py 把 {BASE_URL}/oauth/callback 註冊成
    /// OAuth redirect_uri,授權伺服器把 client_id 綁在上面。換 port 等於所有
    /// OAuth 下游的動態註冊失效,使用者每次開 app 都要重新授權。
    static let port = 8765

    private(set) var state: State = .stopped {
        didSet { if state != oldValue { onStateChange?(state) } }
    }
    var onStateChange: ((State) -> Void)?

    let token = BackendSupervisor.randomToken()

    private var process: Process?
    private var restartAttempts = 0
    private let maxRestarts = 3
    private let queue = DispatchQueue(label: "backend.supervisor")

    // MARK: - 路徑

    /// 資料一律放使用者目錄。發佈版的 .app bundle 是唯讀且已簽章的,寫不進去。
    /// 可用 MCPHUB_DATA_DIR 覆寫 —— 驗收腳本靠它跑在臨時目錄,不碰正式資料。
    static var dataDirectory: URL {
        if let override = ProcessInfo.processInfo.environment["MCPHUB_DATA_DIR"] {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("MCP Hub", isDirectory: true)
    }

    private var pidFile: URL { Self.dataDirectory.appendingPathComponent("backend.pid") }

    /// 開發時用專案的 .venv;打包後用 bundle 內附的 runtime。
    private func resolvePython() -> (executable: URL, repo: URL)? {
        let env = ProcessInfo.processInfo.environment
        if let py = env["MCPHUB_PYTHON"], let repo = env["MCPHUB_REPO"] {
            return (URL(fileURLWithPath: py), URL(fileURLWithPath: repo))
        }
        let bundled = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/backend", isDirectory: true)
        let py = bundled.appendingPathComponent("bin/python3")
        if FileManager.default.isExecutableFile(atPath: py.path) {
            return (py, bundled)
        }
        return nil
    }

    // MARK: - 啟動

    func start() {
        queue.async { [self] in
            do {
                try FileManager.default.createDirectory(
                    at: Self.dataDirectory, withIntermediateDirectories: true)
            } catch {
                state = .failed("無法建立資料目錄:\(error.localizedDescription)")
                return
            }

            reapStaleBackend()

            guard let (python, repo) = resolvePython() else {
                state = .failed("""
                    找不到 Python 後端。開發時請設定環境變數:
                      MCPHUB_PYTHON=<專案>/.venv/bin/python
                      MCPHUB_REPO=<專案路徑>
                    """)
                return
            }

            if let holder = portHolder(), holder != getpid() {
                state = .failed("""
                    port \(Self.port) 已被 PID \(holder) 佔用。
                    可能有另一個 MCP Hub 或 gateway.web 在執行,請先關閉它。
                    這裡不會改用其他 port —— 那會讓所有 OAuth 下游的授權失效。
                    """)
                return
            }

            state = .starting
            spawn(python: python, repo: repo)
        }
    }

    private func spawn(python: URL, repo: URL) {
        let p = Process()
        p.executableURL = python
        p.arguments = ["-m", "gateway.web"]

        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = repo.path
        env["PYTHONUNBUFFERED"] = "1"
        env["MCP_HUB_PORT"] = String(Self.port)
        env["MCP_HUB_DB"] = Self.dataDirectory.appendingPathComponent("actions.db").path
        env["MCP_HUB_KEY"] = Self.dataDirectory.appendingPathComponent(".secret_key").path
        env["MCP_HUB_API_TOKEN"] = token   // 只在環境變數裡,不落地
        p.environment = env

        p.terminationHandler = { [weak self] proc in
            self?.handleExit(status: proc.terminationStatus)
        }

        do {
            try p.run()
        } catch {
            state = .failed("無法啟動後端:\(error.localizedDescription)")
            return
        }
        process = p
        writePid(p.processIdentifier)
        waitUntilReady()
    }

    /// 輪詢 /health 直到後端能回應。啟動中的程序還沒綁好 port 是正常的,
    /// 所以連不上不算失敗,逾時才算。
    private func waitUntilReady() {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if process?.isRunning != true {
                return   // 程序已死,交給 terminationHandler 處理
            }
            if probeHealth() {
                restartAttempts = 0
                state = .ready
                return
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
        state = .failed("後端啟動逾時(20 秒內沒有回應 /health)。")
        stop()
    }

    private func probeHealth() -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(Self.port)/api/v1/health") else { return false }
        var req = URLRequest(url: url)
        req.timeoutInterval = 1
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { data, response, _ in
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let data, (try? JSONSerialization.jsonObject(with: data)) != nil {
                ok = true
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 2)
        return ok
    }

    // MARK: - 結束與重啟

    private func handleExit(status: Int32) {
        queue.async { [self] in
            clearPid()
            process = nil
            guard state != .stopped else { return }   // 是我們自己要求停的

            restartAttempts += 1
            guard restartAttempts <= maxRestarts else {
                state = .failed("後端連續 \(maxRestarts) 次異常結束,已停止重試。")
                return
            }
            // 退避,避免壞掉的後端被無限快速重啟
            Thread.sleep(forTimeInterval: Double(restartAttempts))
            if let (python, repo) = resolvePython() {
                state = .starting
                spawn(python: python, repo: repo)
            }
        }
    }

    /// 結束子程序。先 SIGTERM 給它收尾的機會,逾時才 SIGKILL。
    func stop() {
        state = .stopped
        guard let p = process, p.isRunning else {
            clearPid()
            return
        }
        p.terminationHandler = nil   // 這是我們要求的,不要觸發重啟
        p.terminate()

        let deadline = Date().addingTimeInterval(5)
        while p.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if p.isRunning {
            kill(p.processIdentifier, SIGKILL)
        }
        process = nil
        clearPid()
    }

    // MARK: - 殘留處理

    /// 收掉上次沒能正常結束時留下的後端(app 被 SIGKILL 或當機時會發生)。
    private func reapStaleBackend() {
        guard let pid = readPid() else { return }
        if kill(pid, 0) == 0 {
            kill(pid, SIGTERM)
            let deadline = Date().addingTimeInterval(3)
            while kill(pid, 0) == 0 && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.1)
            }
            if kill(pid, 0) == 0 { kill(pid, SIGKILL) }
        }
        clearPid()
    }

    /// 誰佔著這個 port。回傳 nil 代表沒人佔。
    private func portHolder() -> pid_t? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        task.arguments = ["-nP", "-iTCP:\(Self.port)", "-sTCP:LISTEN", "-t"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        do { try task.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        let text = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return text.split(separator: "\n").first.flatMap { pid_t($0) }
    }

    private func writePid(_ pid: pid_t) {
        try? String(pid).write(to: pidFile, atomically: true, encoding: .utf8)
    }

    private func readPid() -> pid_t? {
        guard let s = try? String(contentsOf: pidFile, encoding: .utf8) else { return nil }
        return pid_t(s.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func clearPid() {
        try? FileManager.default.removeItem(at: pidFile)
    }

    /// 產生接入 Claude 的指令。
    ///
    /// 抽成純函式是為了能測試 —— 這串指令若少了環境變數,使用者會遇到最難查的
    /// 那種問題:聚合器讀到專案目錄那份空的 actions.db,app 裡的設定一個都不生效,
    /// 而且沒有任何錯誤訊息,只是「工具怎麼都沒出現」。
    static func claudeAddCommand(dataDir: String, python: String, repo: String) -> String {
        """
        claude mcp add my-hub \\
          -e PYTHONPATH="\(repo)" \\
          -e MCP_HUB_DB="\(dataDir)/actions.db" \\
          -e MCP_HUB_KEY="\(dataDir)/.secret_key" \\
          -- "\(python)" -m gateway.hub_server
        """
    }

    private static func randomToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return bytes.map { String(format: "%02x", $0) }.joined()
    }
}
