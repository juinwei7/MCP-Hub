using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows;
using WinForms = System.Windows.Forms;
using MCPHub.Core;

// System.Drawing 和 System.Windows.Media 有一批同名型別。這個檔案畫的是 GDI+
// 點陣圖(系統匣圖示只能吃 HICON),所以這裡一律指 System.Drawing 那一邊。
using Color = System.Drawing.Color;
using Pen = System.Drawing.Pen;
using Brush = System.Drawing.Brush;
using FontFamily = System.Drawing.FontFamily;

namespace MCPHub.App;

/// <summary>
/// 系統匣圖示。原生相對於瀏覽器分頁最直接的價值:不用開任何東西就看得到
/// Hub 活著沒。
///
/// 選單結構和 macOS 版一致 —— 狀態列、總開關、下游清單(點一下切換)、
/// 待確認、動作、結束。
/// </summary>
public sealed class TrayIcon : IDisposable
{
    private readonly AppState _state;
    private readonly Action _showWindow;
    private readonly WinForms.NotifyIcon _icon;
    private Icon? _currentIcon;

    public TrayIcon(AppState state, Action showWindow)
    {
        _state = state;
        _showWindow = showWindow;

        _icon = new WinForms.NotifyIcon
        {
            Visible = true,
            ContextMenuStrip = new WinForms.ContextMenuStrip(),
            Text = "MCP Hub",
        };
        _icon.DoubleClick += (_, _) => _showWindow();
        // 每次打開才重建:下游狀態隨時在變,預先建好的選單會顯示過期的資訊
        _icon.ContextMenuStrip.Opening += (_, _) => Rebuild();

        _state.Changed += OnStateChanged;
        UpdateIcon();
    }

    private void OnStateChanged() => UpdateIcon();

    // ── 圖示 ──────────────────────────────────────────────

    /// <summary>
    /// 圖示只表達一件事:現在該不該理它。
    ///
    /// 自己畫而不是附 .ico 檔:狀態有四種,附四個檔案還要處理 DPI 的多尺寸,
    /// 不如直接依目前的 DPI 畫一張。順帶也讓顏色跟著語意色走。
    /// </summary>
    private void UpdateIcon()
    {
        var (color, dot) = Look();
        var next = Draw(color, dot);

        var previous = _currentIcon;
        _icon.Icon = next;
        _currentIcon = next;
        // NotifyIcon 只是引用,換掉之後舊的要自己收 —— 每十秒換一次的話
        // 不收會穩定漏 GDI handle
        previous?.Dispose();

        _icon.Text = Summary();
    }

    private (Color Tint, bool Dot) Look()
    {
        if (_state.Backend == BackendSupervisor.State.Failed) return (Color.FromArgb(0xE0, 0x70, 0x5E), true);
        // 暫停排在待辦之前 —— 暫停時工具根本出不去,那幾筆待確認也動不了,
        // 先讓人看到「是我自己關掉的」比較有用
        if (_state.Paused) return (Color.FromArgb(0x8A, 0x97, 0x9B), false);
        if (_state.PendingCount > 0) return (Color.FromArgb(0xD0, 0x95, 0x2F), true);
        if (_state.Errored > 0) return (Color.FromArgb(0xE0, 0x70, 0x5E), true);
        return (Color.FromArgb(0x47, 0xB6, 0xC4), false);
    }

    /// <summary>
    /// 一個圓角方框代表 Hub,右上角一點代表「有事」。
    /// 系統匣只有 16x16,任何細節都會糊掉,所以形狀必須簡單到剩輪廓。
    /// </summary>
    private static Icon Draw(Color tint, bool dot)
    {
        using var bmp = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);

            using var pen = new Pen(tint, 3f);
            g.DrawRoundedBox(pen, 5, 7, 22, 18, 5);

            // 中間兩條橫線 —— 暗示「多條線匯進來」
            using var thin = new Pen(tint, 2.5f);
            g.DrawLine(thin, 10, 14, 22, 14);
            g.DrawLine(thin, 10, 19, 18, 19);

            if (dot)
            {
                using var brush = new SolidBrush(tint);
                g.FillEllipse(brush, 22, 2, 9, 9);
            }
        }

        var handle = bmp.GetHicon();
        try
        {
            // FromHandle 不接管 handle 的生命週期,所以複製一份再把原 handle 銷毀
            using var shared = Icon.FromHandle(handle);
            return (Icon)shared.Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyIcon(IntPtr handle);

    /// <summary>滑過圖示時的提示。一切正常時不放數字 —— 有事才講。</summary>
    private string Summary() => _state.Backend switch
    {
        BackendSupervisor.State.Stopped => "MCP Hub —— 後端未啟動",
        BackendSupervisor.State.Starting => "MCP Hub —— 後端啟動中",
        BackendSupervisor.State.Failed => "MCP Hub —— 後端啟動失敗",
        _ when _state.Paused => "MCP Hub —— 已暫停",
        _ when _state.PendingCount > 0 => $"MCP Hub —— {_state.PendingCount} 筆待確認",
        _ when _state.Errored > 0 => $"MCP Hub —— {_state.Errored} 台下游連不上",
        _ => "MCP Hub",
    };

    // ── 選單 ──────────────────────────────────────────────

    private void Rebuild()
    {
        var menu = _icon.ContextMenuStrip!;
        menu.Items.Clear();
        menu.Items.Add(Header(BackendLine()));

        if (_state.Backend == BackendSupervisor.State.Ready)
        {
            // 總開關擺在最上面,和下游清單之間隔一條線 —— 它管的是整個 Hub,
            // 不是其中某一台
            menu.Items.Add(new WinForms.ToolStripSeparator());
            menu.Items.Add(Item(_state.Paused ? "繼續" : "暫停",
                                () => _ = _state.SetPausedAsync(!_state.Paused)));

            menu.Items.Add(new WinForms.ToolStripSeparator());
            if (_state.Servers.Count == 0)
            {
                menu.Items.Add(Header("尚未加入任何下游"));
            }
            else
            {
                foreach (var s in _state.Servers) menu.Items.Add(ServerItem(s));
            }

            if (_state.PendingCount > 0)
            {
                menu.Items.Add(new WinForms.ToolStripSeparator());
                menu.Items.Add(Header($"{_state.PendingCount} 筆待確認"));
            }

            menu.Items.Add(new WinForms.ToolStripSeparator());
            menu.Items.Add(Item("開啟主視窗", _showWindow));
            menu.Items.Add(Item("全部重新檢查", () => _ = _state.CheckAllAsync()));
            menu.Items.Add(Item("複製接入 Claude 的指令", CopyClaudeCommand));
        }
        else if (_state.Backend == BackendSupervisor.State.Failed)
        {
            menu.Items.Add(new WinForms.ToolStripSeparator());
            foreach (var line in _state.Supervisor.FailureReason.Split('\n'))
            {
                if (line.Length > 0) menu.Items.Add(Header(line));
            }
            menu.Items.Add(new WinForms.ToolStripSeparator());
            menu.Items.Add(Item("重試", () => _state.Supervisor.Start()));
        }

        menu.Items.Add(new WinForms.ToolStripSeparator());
        menu.Items.Add(Item("結束 MCP Hub", () => Application.Current.Shutdown()));
    }

    private string BackendLine() => _state.Backend switch
    {
        BackendSupervisor.State.Stopped => "後端未啟動",
        BackendSupervisor.State.Starting => "後端啟動中…",
        BackendSupervisor.State.Failed => "後端啟動失敗",
        _ when _state.Paused => "已暫停 · Claude 目前看不到任何工具",
        _ => $"後端正常 · {_state.Healthy}/{_state.Servers.Count(s => s.Enabled)} 台下游健康",
    };

    private WinForms.ToolStripMenuItem ServerItem(HubClient.Server s)
    {
        var mark = !s.Enabled ? "○" : s.IsHealthy ? "●" : s.IsErrored ? "▲" : "○";
        var item = new WinForms.ToolStripMenuItem($"{mark}  {s.Name}")
        {
            Checked = s.Enabled,
            CheckOnClick = false,
            ToolTipText = s.IsErrored && s.StatusDetail.Length > 0 ? s.StatusDetail : null,
        };
        item.Click += (_, _) => _ = _state.SetServerEnabledAsync(s.Slug, !s.Enabled);
        return item;
    }

    private static WinForms.ToolStripMenuItem Header(string text) =>
        new(text) { Enabled = false };

    private static WinForms.ToolStripMenuItem Item(string text, Action action)
    {
        var item = new WinForms.ToolStripMenuItem(text);
        item.Click += (_, _) => action();
        return item;
    }

    private void CopyClaudeCommand()
    {
        var resolved = BackendSupervisor.ResolvePython();
        var command = BackendSupervisor.ClaudeAddCommand(
            BackendSupervisor.DataDirectory,
            resolved?.Python ?? @"<專案>\.venv\Scripts\python.exe",
            resolved?.Repo ?? "<專案路徑>");
        try
        {
            Clipboard.SetText(command);
        }
        catch (System.Runtime.InteropServices.ExternalException)
        {
            // 剪貼簿被別的程序鎖住時 SetText 會丟例外。這不值得讓 app 掛掉,
            // 但也不能假裝成功 —— 使用者會去貼上一段舊內容
            WinForms.MessageBox.Show("剪貼簿正被其他程式佔用,請稍後再試一次。",
                                     "MCP Hub");
        }
    }

    public void Dispose()
    {
        _state.Changed -= OnStateChanged;
        _icon.Visible = false;
        _icon.Dispose();
        _currentIcon?.Dispose();
    }
}

internal static class GraphicsExtensions
{
    /// <summary>圓角矩形。GDI+ 沒有內建,每次都要自己拼路徑。</summary>
    public static void DrawRoundedBox(this Graphics g, Pen pen,
                                      float x, float y, float w, float h, float r)
    {
        using var path = new GraphicsPath();
        path.AddArc(x, y, r * 2, r * 2, 180, 90);
        path.AddArc(x + w - r * 2, y, r * 2, r * 2, 270, 90);
        path.AddArc(x + w - r * 2, y + h - r * 2, r * 2, r * 2, 0, 90);
        path.AddArc(x, y + h - r * 2, r * 2, r * 2, 90, 90);
        path.CloseFigure();
        g.DrawPath(pen, path);
    }
}
