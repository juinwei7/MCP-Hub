using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace MCPHub.App.Controls;

/// <summary>下游健康狀態。顏色只表達狀態,不當裝飾。</summary>
public enum Health
{
    /// <summary>連得上。</summary>
    Ok,
    /// <summary>連得上但有疑慮(例如授權快過期)。</summary>
    Warn,
    /// <summary>連不上。</summary>
    Down,
    /// <summary>停用。不是壞掉,所以不給顏色。</summary>
    Off,
}

/// <summary>
/// 清單列。狀態是列首的色條,右邊掛動作或計數。
///
/// 視覺狀態全部在 code-behind 算,不用 converter：這個 app 沒有 Windows 機器
/// 可以跑,converter 綁錯型別是執行期才炸的那種錯,而這裡編譯器抓得到。
/// </summary>
public partial class HubRow : UserControl
{
    public HubRow() => InitializeComponent();

    public static readonly DependencyProperty HealthProperty =
        DependencyProperty.Register(nameof(Health), typeof(Health), typeof(HubRow),
            new PropertyMetadata(Health.Off, OnVisualChanged));

    public static readonly DependencyProperty TitleProperty =
        DependencyProperty.Register(nameof(Title), typeof(string), typeof(HubRow),
            new PropertyMetadata("", OnVisualChanged));

    public static readonly DependencyProperty DetailProperty =
        DependencyProperty.Register(nameof(Detail), typeof(string), typeof(HubRow),
            new PropertyMetadata("", OnVisualChanged));

    /// <summary>true = 機器產生的值(網址、slug、耗時),用等寬字。</summary>
    public static readonly DependencyProperty DetailIsMachineProperty =
        DependencyProperty.Register(nameof(DetailIsMachine), typeof(bool), typeof(HubRow),
            new PropertyMetadata(true, OnVisualChanged));

    public static readonly DependencyProperty DimmedProperty =
        DependencyProperty.Register(nameof(Dimmed), typeof(bool), typeof(HubRow),
            new PropertyMetadata(false, OnVisualChanged));

    public static readonly DependencyProperty TrailingProperty =
        DependencyProperty.Register(nameof(Trailing), typeof(object), typeof(HubRow),
            new PropertyMetadata(null, OnTrailingChanged));

    public Health Health
    {
        get => (Health)GetValue(HealthProperty);
        set => SetValue(HealthProperty, value);
    }

    public string Title
    {
        get => (string)GetValue(TitleProperty);
        set => SetValue(TitleProperty, value);
    }

    public string Detail
    {
        get => (string)GetValue(DetailProperty);
        set => SetValue(DetailProperty, value);
    }

    public bool DetailIsMachine
    {
        get => (bool)GetValue(DetailIsMachineProperty);
        set => SetValue(DetailIsMachineProperty, value);
    }

    public bool Dimmed
    {
        get => (bool)GetValue(DimmedProperty);
        set => SetValue(DimmedProperty, value);
    }

    public object? Trailing
    {
        get => GetValue(TrailingProperty);
        set => SetValue(TrailingProperty, value);
    }

    private static void OnTrailingChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is HubRow row) row.TrailingHost.Content = e.NewValue;
    }

    private static void OnVisualChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is HubRow row) row.Refresh();
    }

    private void Refresh()
    {
        TitleText.Text = Title;
        TitleText.Opacity = Dimmed ? 0.55 : 1.0;

        // 停用不給顏色 —— 它不是壞掉,不該和異常同一個視覺層級
        Rail.Fill = Health == Health.Off ? Brushes.Transparent : Look(Health);

        if (string.IsNullOrEmpty(Detail))
        {
            DetailText.Visibility = Visibility.Collapsed;
            return;
        }

        DetailText.Visibility = Visibility.Visible;
        DetailText.Text = Detail;

        // 這是整個設計裡唯一的排版主張:等寬 = 機器產生的值。
        // 下游異常時第二行從等寬的網址換成系統字的錯誤原因,
        // 字體本身就說明了那是給人看的訊息。
        if (DetailIsMachine)
        {
            DetailText.FontFamily = Font("MonoFont", "Consolas");
            DetailText.FontSize = 11.5;
            DetailText.Foreground = Brush("Ink3");
        }
        else
        {
            DetailText.FontFamily = Font("UiFont", "Segoe UI");
            DetailText.FontSize = 12;
            DetailText.Foreground = Health == Health.Down ? Brush("Down") : Brush("Ink2");
        }

        DetailText.Opacity = Dimmed ? 0.55 : 1.0;
    }

    private Brush Look(Health h) => h switch
    {
        Health.Ok => Brush("Ok"),
        Health.Warn => Brush("Warn"),
        Health.Down => Brush("Down"),
        _ => Brush("Off"),
    };

    /// <summary>
    /// 主題字典換過去之後 FindResource 才拿得到色票;設計階段(XAML 設計工具)
    /// 或字典還沒掛上時會拿不到,所以給一個看得見的退路而不是讓它炸。
    /// </summary>
    private Brush Brush(string key) =>
        TryFindResource(key) as Brush ?? Brushes.Gray;

    private FontFamily Font(string key, string fallback) =>
        TryFindResource(key) as FontFamily ?? new FontFamily(fallback);
}
