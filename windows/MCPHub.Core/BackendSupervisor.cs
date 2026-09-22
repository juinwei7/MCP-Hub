using System.Diagnostics;
using System.Security.Cryptography;

namespace MCPHub.Core;

/// <summary>
/// 管理 Python 後端子程序的生命週期 —— macOS 版 BackendSupervisor.swift 的對應實作。
///
/// 結構相同,但收拾子程序的機制不同:macOS 沒有 PDEATHSIG,只能靠 PID 檔在下次
/// 啟動時接管;Windows 有 Job Object,父程序不論怎麼結束都會連帶收掉後端。
/// PID 檔仍然保留,但降級成第二道保險(見 ReapStaleBackend 的說明)。
/// </summary>
public sealed class BackendSupervisor : IDisposable
{
    public enum State { Stopped, Starting, Ready, Failed }

    /// <summary>
    /// 後端必須固定這個 port —— Python 端把 {BASE_URL}/oauth/callback 註冊成 OAuth
    /// redirect_uri,授權伺服器把 client_id 綁在上面。換 port 等於所有 OAuth 下游的
    /// 動態註冊失效,使用者每次開 app 都要重新授權。
    /// </summary>
    public const int Port = 8765;

    private const int MaxRestarts = 3;
    private static readonly TimeSpan ReadyTimeout = TimeSpan.FromSeconds(20);

    private readonly JobObject _job = new();
    private readonly object _lock = new();
    private Process? _process;
    private int _restartAttempts;

    public State Current { get; private set; } = State.Stopped;
    public string FailureReason { get; private set; } = "";

    /// <summary>每次啟動隨機產生,只經環境變數傳給子程序,不落地存檔。</summary>
    public string Token { get; } = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));

    public event Action<State>? StateChanged;

    // ── 路徑 ──────────────────────────────────────────────

    /// <summary>
    /// 資料一律放使用者目錄。安裝目錄(Program Files)一般使用者寫不進去,
    /// 而且把資料放在可被覆寫安裝的位置本來就不對。
    /// 可用 MCPHUB_DATA_DIR 覆寫 —— 驗收腳本靠它跑在臨時目錄。
    /// </summary>
    public static string DataDirectory
    {
        get
        {
            var overridden = Environment.GetEnvironmentVariable("MCPHUB_DATA_DIR");
            if (!string.IsNullOrWhiteSpace(overridden)) return overridden;
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(local, "MCP Hub");
        }
    }

    private static string PidFile => Path.Combine(DataDirectory, "backend.pid");

    /// <summary>開發時用專案的 .venv;打包後用安裝目錄內附的 runtime。</summary>
    public static (string Python, string Repo)? ResolvePython()
    {
        var python = Environment.GetEnvironmentVariable("MCPHUB_PYTHON");
        var repo = Environment.GetEnvironmentVariable("MCPHUB_REPO");
        if (!string.IsNullOrWhiteSpace(python) && !string.IsNullOrWhiteSpace(repo))
        {
            return (python, repo);
        }

        var bundled = Path.Combine(AppContext.BaseDirectory, "backend");
        var exe = Path.Combine(bundled, "python.exe");
        return File.Exists(exe) ? (exe, bundled) : null;
    }

    // ── 啟動 ──────────────────────────────────────────────

    public void Start()
    {
        lock (_lock)
        {
            try
            {
                Directory.CreateDirectory(DataDirectory);
            }
            catch (Exception e)
            {
                Fail($"無法建立資料目錄:{e.Message}");
                return;
            }

            ReapStaleBackend();

            var resolved = ResolvePython();
            if (resolved is null)
            {
                Fail("""
                     找不到 Python 後端。開發時請設定環境變數:
                       MCPHUB_PYTHON=<專案>\.venv\Scripts\python.exe
                       MCPHUB_REPO=<專案路徑>
                     """);
                return;
            }

            var holder = PortHolder();
            if (holder is not null && holder != Environment.ProcessId)
            {
                Fail($"""
                      port {Port} 已被 PID {holder} 佔用。
                      可能有另一個 MCP Hub 或 gateway.web 在執行,請先關閉它。
                      這裡不會改用其他 port —— 那會讓所有 OAuth 下游的授權失效。
                      """);
                return;
            }

            SetState(State.Starting);
            Spawn(resolved.Value.Python, resolved.Value.Repo);
        }
    }

    private void Spawn(string python, string repo)
    {
        var info = new ProcessStartInfo
        {
            FileName = python,
            UseShellExecute = false,
            CreateNoWindow = true,          // 不要在使用者面前閃一個主控台視窗
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = repo,
        };
        info.ArgumentList.Add("-m");
        info.ArgumentList.Add("gateway.web");

        info.Environment["PYTHONPATH"] = repo;
        info.Environment["PYTHONUNBUFFERED"] = "1";
        // Windows 主控台預設編碼編不出後端訊息裡的中文,後端自己也會 reconfigure,
        // 這裡再保險一次 —— 編碼錯誤會讓有用的錯誤訊息變成無關的例外。
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.Environment["MCP_HUB_PORT"] = Port.ToString();
        info.Environment["MCP_HUB_DB"] = Path.Combine(DataDirectory, "actions.db");
        info.Environment["MCP_HUB_KEY"] = Path.Combine(DataDirectory, ".secret_key");
        info.Environment["MCP_HUB_API_TOKEN"] = Token;

        Process proc;
        try
        {
            proc = Process.Start(info) ?? throw new InvalidOperationException("Process.Start 回了 null");
        }
        catch (Exception e)
        {
            Fail($"無法啟動後端:{e.Message}");
            return;
        }

        // 立刻納入 Job:父程序此後不論怎麼結束,系統都會連帶收掉它
        _job.Assign(proc.Handle);

        proc.EnableRaisingEvents = true;
        proc.Exited += (_, _) => HandleExit();

        _process = proc;
        WritePid(proc.Id);
        WaitUntilReady();
    }

    /// <summary>
    /// 輪詢 /health 直到後端能回應。啟動中的程序還沒綁好 port 是正常的,
    /// 所以連不上不算失敗,逾時才算。
    /// </summary>
    private void WaitUntilReady()
    {
        var deadline = DateTime.UtcNow + ReadyTimeout;
        while (DateTime.UtcNow < deadline)
        {
            if (_process is null || _process.HasExited) return;   // 交給 Exited 處理
            if (ProbeHealth())
            {
                _restartAttempts = 0;
                SetState(State.Ready);
                return;
            }
            Thread.Sleep(250);
        }
        Fail("後端啟動逾時(20 秒內沒有回應 /health)。");
        Stop();
    }

    private static bool ProbeHealth()
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
            var resp = http.GetAsync($"http://127.0.0.1:{Port}/api/v1/health")
                           .GetAwaiter().GetResult();
            return resp.IsSuccessStatusCode;
        }
        catch
        {
            return false;   // 還沒起來,不是失敗
        }
    }

    // ── 結束與重啟 ────────────────────────────────────────

    private void HandleExit()
    {
        lock (_lock)
        {
            ClearPid();
            _process = null;
            if (Current == State.Stopped) return;   // 是我們自己要求停的

            _restartAttempts++;
            if (_restartAttempts > MaxRestarts)
            {
                Fail($"後端連續 {MaxRestarts} 次異常結束,已停止重試。");
                return;
            }
            // 退避,避免壞掉的後端被無限快速重啟
            Thread.Sleep(TimeSpan.FromSeconds(_restartAttempts));

            var resolved = ResolvePython();
            if (resolved is null) return;
            SetState(State.Starting);
            Spawn(resolved.Value.Python, resolved.Value.Repo);
        }
    }

    /// <summary>結束子程序。先禮貌地要求,逾時才強制。</summary>
    public void Stop()
    {
        lock (_lock)
        {
            SetState(State.Stopped);
            var proc = _process;
            _process = null;
            if (proc is null) { ClearPid(); return; }

            try
            {
                if (!proc.HasExited)
                {
                    proc.Kill(entireProcessTree: true);
                    proc.WaitForExit(5000);
                }
            }
            catch (Exception)
            {
                // 已經結束、或 handle 失效 —— 都不是需要處理的情況
            }
            finally
            {
                proc.Dispose();
                ClearPid();
            }
        }
    }

    public void Dispose()
    {
        Stop();
        _job.Dispose();
    }

    // ── 殘留處理 ──────────────────────────────────────────

    /// <summary>
    /// 收掉上次留下的後端。
    ///
    /// 有了 Job Object,理論上不該有殘留。但這條路徑仍然保留,因為:
    ///   - 使用者可能自己手動跑過 python -m gateway.web
    ///   - 舊版 Windows 或受限環境下 Job Object 可能建不起來(JobObject.IsValid 為 false)
    /// 成本很低,而少了它的話上述情況會變成「port 被佔用」的神秘錯誤。
    /// </summary>
    private static void ReapStaleBackend()
    {
        var pid = ReadPid();
        if (pid is null) return;

        try
        {
            using var proc = Process.GetProcessById(pid.Value);
            proc.Kill(entireProcessTree: true);
            proc.WaitForExit(3000);
        }
        catch (ArgumentException)
        {
            // 程序已經不在了 —— 這是最常見的情況
        }
        catch (Exception)
        {
            // 殺不掉就算了,後面的 port 檢查會把問題講清楚
        }
        finally
        {
            ClearPid();
        }
    }

    /// <summary>
    /// 誰佔著這個 port。回傳 null 代表沒人佔。
    ///
    /// .NET 的 IPGlobalProperties.GetActiveTcpListeners() 拿得到 port 但拿不到 PID,
    /// 而我們需要 PID 才能告訴使用者「去關哪一個」。netstat 的輸出受語系影響,
    /// 所以用 GetExtendedTcpTable。
    /// </summary>
    public static int? PortHolder(int port = Port) => TcpTable.FindListenerPid(port);

    /// <summary>
    /// 接進 Claude 的指令。
    ///
    /// 環境變數不能省:hub_server 是 Claude 獨立啟動的,不經過這個 app。
    /// 少了它們,聚合器會讀到專案目錄那份空的 actions.db,使用者在 app 裡的
    /// 設定一個都不會生效 —— 而且不會有任何錯誤訊息,只是「工具怎麼都沒出現」。
    ///
    /// 用反引號換行而不是 \ ：Windows 的使用者多半在 PowerShell 裡貼,
    /// 而 PowerShell 的續行符號是反引號,貼 \ 進去會被當成路徑分隔字元。
    /// 純函式,方便測試。
    /// </summary>
    public static string ClaudeAddCommand(string dataDir, string python, string repo) =>
        $"""
        claude mcp add my-hub `
          -e PYTHONPATH="{repo}" `
          -e MCP_HUB_DB="{Path.Combine(dataDir, "actions.db")}" `
          -e MCP_HUB_KEY="{Path.Combine(dataDir, ".secret_key")}" `
          -- "{python}" -m gateway.hub_server
        """;

    private static void WritePid(int pid)
    {
        try { File.WriteAllText(PidFile, pid.ToString()); }
        catch (Exception) { /* PID 檔只是保險,寫不成不影響主流程 */ }
    }

    private static int? ReadPid()
    {
        try
        {
            var text = File.ReadAllText(PidFile).Trim();
            return int.TryParse(text, out var pid) ? pid : null;
        }
        catch (Exception) { return null; }
    }

    private static void ClearPid()
    {
        try { File.Delete(PidFile); }
        catch (Exception) { /* 同上 */ }
    }

    // ── 狀態 ──────────────────────────────────────────────

    private void Fail(string reason)
    {
        FailureReason = reason;
        SetState(State.Failed);
    }

    private void SetState(State next)
    {
        if (Current == next) return;
        Current = next;
        StateChanged?.Invoke(next);
    }
}
