using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace MCPHub.App.Controls;

public enum AlertKind
{
    /// <summary>出事了,要人處理。</summary>
    Problem,
    /// <summary>只是告知現在的狀態(例如已暫停)。</summary>
    Info,
    /// <summary>剛做完的事成功了。</summary>
    Success,
}

/// <summary>有事才浮上來的那一行,右邊帶修復動作。</summary>
public partial class AlertLine : UserControl
{
    public AlertLine() => InitializeComponent();

    public event RoutedEventHandler? ActionClicked;

    public static readonly DependencyProperty TextProperty =
        DependencyProperty.Register(nameof(Text), typeof(string), typeof(AlertLine),
            new PropertyMetadata("", OnChanged));

    public static readonly DependencyProperty KindProperty =
        DependencyProperty.Register(nameof(Kind), typeof(AlertKind), typeof(AlertLine),
            new PropertyMetadata(AlertKind.Problem, OnChanged));

    public static readonly DependencyProperty ActionTitleProperty =
        DependencyProperty.Register(nameof(ActionTitle), typeof(string), typeof(AlertLine),
            new PropertyMetadata("", OnChanged));

    public string Text
    {
        get => (string)GetValue(TextProperty);
        set => SetValue(TextProperty, value);
    }

    public AlertKind Kind
    {
        get => (AlertKind)GetValue(KindProperty);
        set => SetValue(KindProperty, value);
    }

    public string ActionTitle
    {
        get => (string)GetValue(ActionTitleProperty);
        set => SetValue(ActionTitleProperty, value);
    }

    private static void OnChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is AlertLine line) line.Refresh();
    }

    private void OnAction(object sender, RoutedEventArgs e) => ActionClicked?.Invoke(this, e);

    private void Refresh()
    {
        Message.Text = Text;

        var (tint, wash, glyph) = Kind switch
        {
            // Segoe Fluent Icons 的碼位。用字型而不是圖檔,DPI 縮放才不會糊
            AlertKind.Problem => (Brush("Down"), Brush("DownWash"), ""),   // 警告三角
            AlertKind.Success => (Brush("Ok"), Brush("AccentWash"), ""),   // 打勾
            _ => (Brush("Accent"), Brush("AccentWash"), ""),               // 資訊
        };

        Glyph.Text = glyph;
        Glyph.Foreground = tint;
        Message.Foreground = tint;
        Bg.Background = wash;

        Action.Foreground = tint;
        Action.Content = ActionTitle;
        Action.Visibility = string.IsNullOrEmpty(ActionTitle)
            ? Visibility.Collapsed : Visibility.Visible;
    }

    private Brush Brush(string key) => TryFindResource(key) as Brush ?? Brushes.Gray;
}
