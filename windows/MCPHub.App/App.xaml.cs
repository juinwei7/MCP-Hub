using System.IO;
using System.Windows;
using System.Windows.Threading;
using MCPHub.Core;

namespace MCPHub.App;

public partial class App : Application
{
    private AppState? _state;
    private TrayIcon? _tray;
    private MainWindow? _window;

    /// <summary>渲染截圖時要拿到同一份狀態。正常執行路徑不會用到。</summary>
    internal static AppState? SharedState { get; private set; }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        InstallCrashLog();

        ThemeManager.Start();

        _state = new AppState();
        SharedState = _state;
        _state.Boot();

        // --shot <目錄>:把畫面渲染成 PNG 然後結束。這是 CI 唯一能看到畫面的
        // 方式 —— 詳見 Shot.cs。系統匣在這條路徑上不需要。
        var shotDir = ShotDirectory(e.Args);
        if (shotDir is not null)
        {
            _ = Shot.RunAsync(shotDir, _state).ContinueWith(
                t => Dispatcher.Invoke(() => Shutdown(t.IsFaulted ? 1 : 0)),
                TaskScheduler.Default);
            return;
        }

        _tray = new TrayIcon(_state, ShowWindow);

        // 第一次啟動先把視窗打開,不然只有系統匣圖示會讓人以為沒反應。
        // 之後關掉視窗只是隱藏,程序還在跑。
        ShowWindow();
    }

    private static string? ShotDirectory(string[] args)
    {
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "--shot") return args[i + 1];
        }
        return null;
    }

    /// <summary>
    /// 叫出主視窗。
    ///
    /// 系統匣圖示可能被 Windows 收進「顯示隱藏的圖示」那個小箭頭裡,
    /// 所以再執行一次 MCPHub.exe 也要能把視窗叫回來 —— 否則會有一個
    /// 「程序在跑但沒有任何入口」的死角。那條路徑走的是 SingleInstance。
    /// </summary>
    internal void ShowWindow()
    {
        if (_state is null) return;
        if (_window is null)
        {
            _window = new MainWindow(_state);
            _window.Closed += (_, _) => _window = null;
        }
        _window.Show();
        if (_window.WindowState == WindowState.Minimized)
        {
            _window.WindowState = WindowState.Normal;
        }
        _window.Activate();
    }

    /// <summary>
    /// 崩潰時留下記錄。
    ///
    /// WinExe 沒有 console,例外沒有地方可去 —— 使用者只會看到 app 無聲無息地
    /// 消失,而在 CI 上就是「找不到視窗」這種沒有線索的失敗。
    /// 寫在資料目錄裡,和 actions.db 放一起,回報問題時一併帶走。
    /// </summary>
    private void InstallCrashLog()
    {
        DispatcherUnhandledException += (_, e) =>
        {
            Write("Dispatcher", e.Exception);
            // 不設 Handled:狀態已經不明,硬撐下去只會讓後面的錯誤更難解讀
        };
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            Write("AppDomain", e.ExceptionObject as Exception);
        TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            Write("Task", e.Exception);
            e.SetObserved();
        };

        static void Write(string source, Exception? ex)
        {
            try
            {
                var dir = BackendSupervisor.DataDirectory;
                Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "crash.log"),
                    $"""

                    ── {DateTime.Now:yyyy-MM-dd HH:mm:ss} [{source}] ──
                    {ex}

                    """);
            }
            catch (Exception) { /* 連記錄都寫不了就只能放棄,不能讓它再丟一次 */ }
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        // app 結束時一定要把後端子程序收掉 —— 這是整個外殼最重要的職責。
        // Job Object 是最後一道保險,但正常結束時應該走到這裡。
        _tray?.Dispose();
        _state?.Dispose();
        ThemeManager.Stop();
        base.OnExit(e);
    }
}
