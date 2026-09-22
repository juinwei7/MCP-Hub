using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MCPHub.Core;

namespace MCPHub.App;

/// <summary>
/// 把畫面渲染成 PNG,不需要桌面。
///
/// 為什麼不截螢幕:開發機是 macOS,這個 app 在本機執行不了,CI 的 Windows
/// runner 是唯一看得到畫面的地方 —— 但那上面沒有可靠的互動式 desktop,
/// 實測連一個有標題的最上層視窗都列不出來,截螢幕那條路走不通。
///
/// WPF 的版面計算不需要視窗真的顯示:Measure / Arrange 跑完,視覺樹就完整了,
/// RenderTargetBitmap 直接把它畫下來。順帶還有兩個好處 —— 尺寸是我們指定的,
/// 不受 runner 螢幕解析度影響;而且不必等視窗管理員,沒有時序問題。
/// </summary>
internal static class Shot
{
    /// <summary>渲染的尺寸。比 MinWidth 寬一點,才看得出版面在正常寬度下的樣子。</summary>
    private const int Width = 1160;
    private const int Height = 720;

    public static async Task<int> RunAsync(string outDir, AppState state)
    {
        Directory.CreateDirectory(outDir);
        var log = new StringWriter();

        // 後端起來之前畫面上只有「啟動中」。等它,才截得到真正的內容。
        var deadline = DateTime.UtcNow.AddSeconds(90);
        while (DateTime.UtcNow < deadline
               && state.Backend != BackendSupervisor.State.Ready
               && state.Backend != BackendSupervisor.State.Failed)
        {
            await Task.Delay(500).ConfigureAwait(true);
        }
        log.WriteLine($"後端狀態:{state.Backend}");
        if (state.Backend == BackendSupervisor.State.Failed)
        {
            log.WriteLine($"原因:{state.Supervisor.FailureReason}");
        }

        // 讓第一輪輪詢把資料填進來,順便把各區域各自載入的東西也抓齊
        await state.RefreshAsync().ConfigureAwait(true);
        await state.LoadLogsAsync().ConfigureAwait(true);
        await state.LoadCategoriesAsync().ConfigureAwait(true);
        await state.LoadStepToolsAsync().ConfigureAwait(true);
        await state.LoadClaudePreviewAsync().ConfigureAwait(true);
        foreach (var s in state.Servers.Where(x => x.Enabled))
        {
            await state.LoadToolsAsync(s.Slug).ConfigureAwait(true);
        }

        log.WriteLine($"下游 {state.Servers.Count} 台、待確認 {state.PendingCount} 筆");

        // 兩個主題 × 兩個畫面。深色不是把淺色反轉是另一組色值,而設計的主張
        // 全在「列」上 —— 空狀態什麼都驗不到。
        // 每個區域、兩個主題都渲染。深色不是把淺色反轉是另一組色值,
        // 而每個畫面的版面都不一樣 —— 只看一張什麼都保證不了。
        foreach (var dark in new[] { false, true })
        {
            ThemeManager.Force(dark);
            var theme = dark ? "dark" : "light";
            foreach (var section in Enum.GetValues<MainWindow.Section>())
            {
                var key = section.ToString().ToLowerInvariant();
                var path = Path.Combine(outDir, $"{key}-{theme}.png");
                Render(path, section, editor: false);
                log.WriteLine($"已渲染 {path}");

                // 有編輯器的三個區域多渲染一張打開的狀態
                if (section is MainWindow.Section.Servers or MainWindow.Section.Custom
                            or MainWindow.Section.Composite)
                {
                    var edit = Path.Combine(outDir, $"{key}-edit-{theme}.png");
                    Render(edit, section, editor: true);
                    log.WriteLine($"已渲染 {edit}");
                }
            }
        }

        File.WriteAllText(Path.Combine(outDir, "shot.log"), log.ToString());
        return 0;
    }

    private static void Render(string path, MainWindow.Section section, bool editor)
    {
        // 每次都重建:主題換過之後,已經建好的控制項雖然會跟著 DynamicResource
        // 更新,但 code-behind 用 FindResource 取到的筆刷是當下那一份 ——
        // 重建才保證兩張圖各自是完整的那個主題。
        var window = new MainWindow(App.SharedState!)
        {
            Width = Width,
            Height = Height,
        };

        window.Select(section);
        if (editor) window.OpenNewEditor();

        var root = (UIElement)window.Content;
        window.Content = null;   // 先脫離視窗,才能單獨排版與渲染

        root.Measure(new Size(Width, Height));
        root.Arrange(new Rect(0, 0, Width, Height));
        root.UpdateLayout();

        // 96 dpi = WPF 的裝置無關單位,截出來就是設計上的尺寸
        var bmp = new RenderTargetBitmap(Width, Height, 96, 96, PixelFormats.Pbgra32);
        bmp.Render(root);

        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bmp));
        using var stream = File.Create(path);
        encoder.Save(stream);

        window.Close();
    }
}
