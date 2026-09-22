using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using MCPHub.App.Controls;
using MCPHub.Core;

namespace MCPHub.App;

public partial class MainWindow : Window
{
    internal enum Section { Servers, Tools, Logs, Actions, Custom, Composite, Settings }

    private readonly AppState _state;
    private Section _section = Section.Servers;

    /// <summary>
    /// XAML 建好之前不要重畫。RadioButton 的 IsChecked="True" 會在
    /// InitializeComponent() 進行中就觸發 Checked,那時具名元素都還是 null。
    /// </summary>
    private bool _ready;

    public MainWindow(AppState state)
    {
        _state = state;
        InitializeComponent();
        BuildNav();
        _ready = true;
        _state.Changed += Refresh;
        Closed += (_, _) => _state.Changed -= Refresh;
        Refresh();
    }

    /// <summary>截圖用:直接切到某個區域。正常操作走側邊欄。</summary>
    internal void Select(Section section) => Nav(section).IsChecked = true;

    // ── 側邊欄 ────────────────────────────────────────────
    //
    // 不放圖示。macOS 版有 SF Symbols,Windows 對應的是 Segoe Fluent Icons,
    // 但碼位猜錯就會渲染成一個空方框 —— 而我沒有 Windows 可以先看一眼。
    // 錯的圖示比沒有圖示糟,文字加計數本來就讀得懂。

    private readonly Dictionary<Section, TextBlock> _navCounts = [];

    private RadioButton Nav(Section s) => s switch
    {
        Section.Servers => NavServers,
        Section.Tools => NavTools,
        Section.Logs => NavLogs,
        Section.Actions => NavActions,
        Section.Custom => NavCustom,
        Section.Composite => NavComposite,
        _ => NavSettings,
    };

    private static string Label(Section s) => s switch
    {
        Section.Servers => "下游",
        Section.Tools => "工具",
        Section.Logs => "記錄",
        Section.Actions => "待確認",
        Section.Custom => "自訂工具",
        Section.Composite => "複合工具",
        _ => "設定",
    };

    private void BuildNav()
    {
        foreach (var s in Enum.GetValues<Section>())
        {
            var grid = new Grid();
            grid.ColumnDefinitions.Add(
                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var label = new TextBlock
            {
                Text = Label(s),
                FontFamily = (FontFamily)FindResource("UiFont"),
                FontSize = 13,
                Foreground = Ui.Brush(this, "Ink"),
                VerticalAlignment = VerticalAlignment.Center,
            };
            var count = new TextBlock
            {
                Style = Ui.Style(this, "MonoNumber"),
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(count, 1);
            grid.Children.Add(label);
            grid.Children.Add(count);

            Nav(s).Content = grid;
            _navCounts[s] = count;
        }
    }

    private void OnNavChanged(object sender, RoutedEventArgs e)
    {
        if (!_ready) return;
        foreach (var s in Enum.GetValues<Section>())
        {
            if (ReferenceEquals(sender, Nav(s))) { _section = s; break; }
        }
        ResetSectionState();
        Refresh();
    }

    private void OnSupplyToggled(object sender, RoutedEventArgs e) =>
        _ = _state.SetPausedAsync(SupplySwitch.IsChecked != true);

    private void OnResume(object sender, RoutedEventArgs e) => _ = _state.SetPausedAsync(false);

    private void OnDismissError(object sender, RoutedEventArgs e) => _state.ClearError();

    // ── 重畫 ──────────────────────────────────────────────

    private void Refresh()
    {
        if (!_ready) return;
        RefreshChrome();
        Rows.Children.Clear();
        Detail.Children.Clear();
        PageActions.Children.Clear();
        ShowDetail(false);
        PageTitle.Text = Label(_section);
        FooterText.Text = "";

        if (_state.Backend != BackendSupervisor.State.Ready)
        {
            PageTitle.Text = "後端";
            Rows.Children.Add(BackendNotReady());
            return;
        }

        switch (_section)
        {
            case Section.Servers: BuildServers(); break;
            case Section.Tools: BuildTools(); break;
            case Section.Logs: BuildLogs(); break;
            case Section.Actions: BuildActions(); break;
            case Section.Custom: BuildCustom(); break;
            case Section.Composite: BuildComposite(); break;
            default: BuildSettings(); break;
        }
    }

    /// <summary>
    /// 右邊的編輯區。沒有東西要編時整欄收成 0,清單就佔滿整個寬度 ——
    /// 空的編輯區和空的清單欄是同一件事講兩次。
    /// </summary>
    private void ShowDetail(bool on, double width = 470)
    {
        DetailCol.Width = on ? new GridLength(width) : new GridLength(0);
        SplitCol.Width = on ? new GridLength(1) : new GridLength(0);
    }

    /// <summary>側邊欄計數、總開關、兩條警示線 —— 和目前在哪一頁無關。</summary>
    private void RefreshChrome()
    {
        Count(Section.Servers, _state.Servers.Count);
        Count(Section.Tools, _state.TotalTools);
        Count(Section.Logs, null);
        // 待確認是唯一需要動手的東西 —— 有值時著色,沒值時連 0 都不放
        Count(Section.Actions, _state.PendingCount, attention: true);
        Count(Section.Custom, _state.CustomTools.Count);
        Count(Section.Composite, _state.CompositeTools.Count);
        Count(Section.Settings, null);

        SupplySwitch.IsChecked = !_state.Paused;
        SupplyDot.Fill = _state.Paused ? Ui.Brush(this, "Off") : Ui.Brush(this, "Ok");
        SupplyLabel.Text = _state.Paused ? "已暫停" : "供應中";
        SupplyLabel.Foreground = _state.Paused
            ? Ui.Brush(this, "Ink3") : Ui.Brush(this, "Ink2");

        // 有事才浮上來。一切正常時這兩行完全不存在
        PausedAlert.Text = "已暫停 —— Claude 目前看不到任何工具";
        PausedAlert.Visibility = _state.Paused ? Visibility.Visible : Visibility.Collapsed;

        ErrorAlert.Text = _state.LastError ?? "";
        ErrorAlert.Visibility = _state.LastError is null
            ? Visibility.Collapsed : Visibility.Visible;
    }

    private void Count(Section s, int? value, bool attention = false)
    {
        var box = _navCounts[s];
        var hot = attention && value > 0;
        box.Text = value is null || (attention && value == 0) ? "" : value.Value.ToString();
        box.Foreground = hot ? Ui.Brush(this, "Warn") : Ui.Brush(this, "Ink3");
        box.FontWeight = hot ? FontWeights.SemiBold : FontWeights.Normal;
    }

    // ── 待確認 ────────────────────────────────────────────

    private void BuildActions()
    {
        var waiting = _state.Actions.Where(a => a.IsWaiting).ToList();
        foreach (var a in waiting)
        {
            var id = a.Id;
            Rows.Children.Add(new HubRow
            {
                // 待確認一律是琥珀:它不是壞掉,是在等人
                Health = Controls.Health.Warn,
                Title = a.Tool,
                Detail = Moment(a.CreatedAt),
                DetailIsMachine = true,
                Trailing = Ui.Row(
                    Ui.Button(this, "拒絕", "Quiet", () => _ = _state.DecideAsync(id, false)),
                    Ui.Button(this, "核准", "Primary", () => _ = _state.DecideAsync(id, true))),
            });
        }

        if (waiting.Count == 0)
        {
            Rows.Children.Add(Ui.Empty(this, "沒有待確認的操作",
                "被標記成「需人工確認」的工具,Claude 呼叫時會停在這裡等你核准。"));
        }

        FooterText.Text = $"{waiting.Count} 筆待處理,共 {_state.Actions.Count} 筆";
    }

    // ── 零件 ──────────────────────────────────────────────

    /// <summary>
    /// 後端回的是 ISO 8601。那個 T 是給機器看的分隔符,人讀起來只是噪音 ——
    /// 換成空格。解析不了就原樣顯示,不要為了好看把資訊吃掉。
    /// </summary>
    private static string Moment(string iso) =>
        DateTime.TryParse(iso, out var t) ? t.ToString("yyyy-MM-dd HH:mm:ss") : iso;

    private UIElement BackendNotReady()
    {
        var text = _state.Backend switch
        {
            BackendSupervisor.State.Starting => "後端啟動中…",
            BackendSupervisor.State.Failed => _state.Supervisor.FailureReason,
            _ => "後端未啟動",
        };
        var panel = Ui.Empty(this,
            _state.Backend == BackendSupervisor.State.Failed ? "後端啟動失敗" : "正在啟動",
            text);
        if (_state.Backend == BackendSupervisor.State.Failed)
        {
            panel.Children.Add(Ui.Button(this, "重試", "Primary",
                () => _state.Supervisor.Start()));
        }
        return panel;
    }
}
