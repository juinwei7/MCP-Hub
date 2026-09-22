using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using MCPHub.App.Controls;
using MCPHub.Core;

namespace MCPHub.App;

public partial class MainWindow : Window
{
    private enum Section { Servers, Actions }

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
        _ready = true;
        _state.Changed += Refresh;
        Closed += (_, _) => _state.Changed -= Refresh;
        Refresh();
    }

    /// <summary>截圖用:切到「待確認」。正常操作走側邊欄。</summary>
    internal void SelectActions() => NavActions.IsChecked = true;

    // ── 導覽 ──────────────────────────────────────────────

    private void OnNavChanged(object sender, RoutedEventArgs e)
    {
        if (!_ready) return;
        _section = ReferenceEquals(sender, NavActions) ? Section.Actions : Section.Servers;
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
        PageActions.Children.Clear();

        if (_state.Backend != BackendSupervisor.State.Ready)
        {
            PageTitle.Text = "後端";
            Rows.Children.Add(BackendNotReady());
            FooterText.Text = "";
            return;
        }

        if (_section == Section.Servers) BuildServers();
        else BuildActions();
    }

    /// <summary>側邊欄計數、總開關、兩條警示線 —— 和目前在哪一頁無關。</summary>
    private void RefreshChrome()
    {
        NavServersCount.Text = _state.Servers.Count.ToString();
        NavServersCount.Foreground = Brush("Ink3");

        // 待確認是唯一需要動手的東西 —— 有值時著色
        var pending = _state.PendingCount;
        NavActionsCount.Text = pending > 0 ? pending.ToString() : "";
        NavActionsCount.Foreground = pending > 0 ? Brush("Warn") : Brush("Ink3");
        NavActionsCount.FontWeight = pending > 0 ? FontWeights.SemiBold : FontWeights.Normal;

        NavServersLabel.Foreground = Brush("Ink");
        NavActionsLabel.Foreground = Brush("Ink");

        SupplySwitch.IsChecked = !_state.Paused;
        SupplyDot.Fill = _state.Paused ? Brush("Off") : Brush("Ok");
        SupplyLabel.Text = _state.Paused ? "已暫停" : "供應中";
        SupplyLabel.Foreground = _state.Paused ? Brush("Ink3") : Brush("Ink2");

        // 有事才浮上來。一切正常時這兩行完全不存在
        PausedAlert.Text = "已暫停 —— Claude 目前看不到任何工具";
        PausedAlert.Visibility = _state.Paused ? Visibility.Visible : Visibility.Collapsed;

        ErrorAlert.Text = _state.LastError ?? "";
        ErrorAlert.Visibility = _state.LastError is null
            ? Visibility.Collapsed : Visibility.Visible;
    }

    // ── 下游 ──────────────────────────────────────────────

    private void BuildServers()
    {
        PageTitle.Text = "下游";
        PageActions.Children.Add(Button("全部重新檢查", "Quiet",
            () => _ = _state.CheckAllAsync()));

        foreach (var s in _state.Servers)
        {
            var toggle = new ToggleButton
            {
                Style = (Style)FindResource("HubSwitch"),
                IsChecked = s.Enabled,
            };
            var slug = s.Slug;
            var wasEnabled = s.Enabled;
            toggle.Click += (_, _) => _ = _state.SetServerEnabledAsync(slug, !wasEnabled);

            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            if (s.ToolCount is int n)
            {
                trailing.Children.Add(new TextBlock
                {
                    Text = $"{n} 工具",
                    Style = (Style)FindResource("Meta"),
                    VerticalAlignment = VerticalAlignment.Center,
                    Margin = new Thickness(0, 0, 14, 0),
                });
            }
            trailing.Children.Add(toggle);

            // 異常時第二行從等寬的網址換成系統字的錯誤原因 —— 字體本身
            // 就說明了那是給人看的訊息,不是機器產生的識別碼
            var broken = s.Enabled && s.IsErrored && s.StatusDetail.Length > 0;

            Rows.Children.Add(new HubRow
            {
                Health = !s.Enabled ? Controls.Health.Off
                       : s.IsHealthy ? Controls.Health.Ok
                       : s.IsErrored ? Controls.Health.Down
                       : Controls.Health.Warn,
                Title = s.Name,
                Detail = broken ? s.StatusDetail : Target(s),
                DetailIsMachine = !broken,
                Dimmed = !s.Enabled,
                Trailing = trailing,
            });
        }

        if (_state.Servers.Count == 0)
        {
            Rows.Children.Add(Empty("還沒有下游",
                "下游是 Claude 實際要用的 MCP server。加進來之後,它們的工具會一起從 Hub 出去。"));
        }

        var enabled = _state.Servers.Count(s => s.Enabled);
        FooterText.Text = $"{_state.Servers.Count} 台,{enabled} 台啟用";
    }

    /// <summary>清單上顯示的位址。http 顯示主機,stdio 顯示指令。</summary>
    private static string Target(HubClient.Server s)
    {
        if (s.Transport == "stdio") return s.BaseUrl;
        // 協定對辨識沒有幫助,而且會把有用的路徑擠掉
        var url = s.BaseUrl;
        foreach (var prefix in new[] { "https://", "http://" })
        {
            if (url.StartsWith(prefix, StringComparison.Ordinal))
            {
                return url[prefix.Length..];
            }
        }
        return url;
    }

    // ── 待確認 ────────────────────────────────────────────

    private void BuildActions()
    {
        PageTitle.Text = "待確認";

        var waiting = _state.Actions.Where(a => a.IsWaiting).ToList();
        foreach (var a in waiting)
        {
            var id = a.Id;
            var trailing = new StackPanel { Orientation = Orientation.Horizontal };
            trailing.Children.Add(Button("拒絕", "Quiet", () => _ = _state.DecideAsync(id, false)));
            trailing.Children.Add(new Border { Width = 8 });
            trailing.Children.Add(Button("核准", "Primary", () => _ = _state.DecideAsync(id, true)));

            Rows.Children.Add(new HubRow
            {
                // 待確認一律是琥珀:它不是壞掉,是在等人
                Health = Controls.Health.Warn,
                Title = a.Tool,
                Detail = Moment(a.CreatedAt),
                DetailIsMachine = true,
                Trailing = trailing,
            });
        }

        if (waiting.Count == 0)
        {
            Rows.Children.Add(Empty("沒有待確認的操作",
                "被標記成「需人工確認」的工具,Claude 呼叫時會停在這裡等你核准。"));
        }

        FooterText.Text = $"{waiting.Count} 筆待處理,共 {_state.Actions.Count} 筆";
    }

    /// <summary>
    /// 後端回的是 ISO 8601。那個 T 是給機器看的分隔符,人讀起來只是噪音 ——
    /// 換成空格。解析不了就原樣顯示,不要為了好看把資訊吃掉。
    /// </summary>
    private static string Moment(string iso) =>
        DateTime.TryParse(iso, out var t) ? t.ToString("yyyy-MM-dd HH:mm:ss") : iso;

    // ── 零件 ──────────────────────────────────────────────

    private UIElement BackendNotReady()
    {
        var text = _state.Backend switch
        {
            BackendSupervisor.State.Starting => "後端啟動中…",
            BackendSupervisor.State.Failed => _state.Supervisor.FailureReason,
            _ => "後端未啟動",
        };
        var panel = Empty(_state.Backend == BackendSupervisor.State.Failed
                              ? "後端啟動失敗" : "正在啟動", text);
        if (_state.Backend == BackendSupervisor.State.Failed)
        {
            panel.Children.Add(Button("重試", "Primary", () => _state.Supervisor.Start()));
        }
        return panel;
    }

    private StackPanel Empty(string title, string hint)
    {
        var panel = new StackPanel
        {
            HorizontalAlignment = HorizontalAlignment.Center,
            Margin = new Thickness(0, 90, 0, 0),
            MaxWidth = 460,
        };
        panel.Children.Add(new TextBlock
        {
            Text = title,
            Style = (Style)FindResource("RowTitle"),
            HorizontalAlignment = HorizontalAlignment.Center,
            Margin = new Thickness(0, 0, 0, 6),
        });
        panel.Children.Add(new TextBlock
        {
            Text = hint,
            Style = (Style)FindResource("Body"),
            TextWrapping = TextWrapping.Wrap,
            TextAlignment = TextAlignment.Center,
            Margin = new Thickness(0, 0, 0, 16),
        });
        return panel;
    }

    private Button Button(string text, string kind, Action onClick)
    {
        var b = new Button
        {
            Content = text,
            Style = (Style)FindResource(kind == "Primary" ? "PrimaryButton" : "QuietButton"),
            Margin = new Thickness(8, 0, 0, 0),
        };
        b.Click += (_, _) => onClick();
        return b;
    }

    private Brush Brush(string key) => (Brush)FindResource(key);
}
