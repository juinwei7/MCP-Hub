using System.Windows;

namespace MCPHub.App;

public partial class App : Application
{
    private AppState? _state;
    private TrayIcon? _tray;
    private MainWindow? _window;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        ThemeManager.Start();

        _state = new AppState();
        _tray = new TrayIcon(_state, ShowWindow);
        _state.Boot();

        // 第一次啟動先把視窗打開,不然只有系統匣圖示會讓人以為沒反應。
        // 之後關掉視窗只是隱藏,程序還在跑。
        ShowWindow();
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
