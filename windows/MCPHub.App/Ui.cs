using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Rectangle = System.Windows.Shapes.Rectangle;
using ToggleButton = System.Windows.Controls.Primitives.ToggleButton;

namespace MCPHub.App;

/// <summary>
/// 介面零件。
///
/// 用程式建而不是 XAML + 資料繫結:開發機是 macOS,這個 app 在本機執行不了,
/// 而 WPF 的繫結失敗預設只寫到 Debug 輸出、不丟例外 —— 打錯一個屬性名會靜靜地
/// 少一塊畫面,而 CI 的截圖未必看得出是繫結壞了還是資料本來就空。
/// 直接呼叫的程式碼編譯器抓得到。
/// </summary>
internal static class Ui
{
    public static Brush Brush(FrameworkElement host, string key) =>
        host.TryFindResource(key) as Brush ?? Brushes.Gray;

    public static Style Style(FrameworkElement host, string key) =>
        (Style)host.FindResource(key);

    // ── 文字 ──────────────────────────────────────────────

    public static TextBlock Text(FrameworkElement host, string text, string style,
                                 bool wrap = false)
    {
        var t = new TextBlock { Text = text, Style = Style(host, style) };
        if (wrap) t.TextWrapping = TextWrapping.Wrap;
        return t;
    }

    public static TextBlock Heading(FrameworkElement host, string text) =>
        new()
        {
            Text = text,
            Style = Style(host, "RowTitle"),
            Margin = new Thickness(0, 0, 0, 8),
        };

    // ── 輸入 ──────────────────────────────────────────────

    public static TextBox Input(FrameworkElement host, string value, string placeholder = "")
    {
        var box = new TextBox
        {
            Text = value,
            Style = Style(host, "HubTextBox"),
            Tag = placeholder,
        };
        return box;
    }

    /// <summary>JSON 之類的多行內容。等寬,因為那是機器格式。</summary>
    public static TextBox Code(FrameworkElement host, string value, double height = 90)
    {
        var box = Input(host, value);
        box.FontFamily = (FontFamily)host.FindResource("MonoFont");
        box.FontSize = 12;
        box.AcceptsReturn = true;
        box.TextWrapping = TextWrapping.NoWrap;
        box.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
        box.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto;
        box.Height = height;
        box.VerticalContentAlignment = VerticalAlignment.Top;
        return box;
    }

    public static ComboBox Choice(FrameworkElement host, IEnumerable<string> options,
                                  string selected)
    {
        var box = new ComboBox { Style = Style(host, "HubCombo") };
        foreach (var o in options) box.Items.Add(o);
        box.SelectedItem = selected;
        if (box.SelectedItem is null && box.Items.Count > 0) box.SelectedIndex = 0;
        return box;
    }

    public static ToggleButton Switch(FrameworkElement host, bool on, Action<bool> changed)
    {
        var t = new ToggleButton
        {
            Style = Style(host, "HubSwitch"),
            IsChecked = on,
        };
        t.Click += (_, _) => changed(t.IsChecked == true);
        return t;
    }

    public static Button Button(FrameworkElement host, string text, string kind,
                                Action onClick)
    {
        var b = new Button
        {
            Content = text,
            Style = Style(host, kind switch
            {
                "Primary" => "PrimaryButton",
                "Link" => "LinkButton",
                _ => "QuietButton",
            }),
            Margin = new Thickness(8, 0, 0, 0),
        };
        if (kind == "Link") b.Foreground = Brush(host, "Accent");
        b.Click += (_, _) => onClick();
        return b;
    }

    // ── 版面 ──────────────────────────────────────────────

    public static StackPanel Row(params UIElement[] children)
    {
        var p = new StackPanel { Orientation = Orientation.Horizontal };
        foreach (var c in children) p.Children.Add(c);
        return p;
    }

    /// <summary>表單的一列:標籤在上、控制項在下。左右並排在窄視窗裡會擠。</summary>
    public static StackPanel Field(FrameworkElement host, string label, UIElement control,
                                   string hint = "")
    {
        var p = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };
        p.Children.Add(new TextBlock
        {
            Text = label,
            Style = Style(host, "Meta"),
            Margin = new Thickness(0, 0, 0, 4),
        });
        p.Children.Add(control);
        if (hint.Length > 0)
        {
            p.Children.Add(new TextBlock
            {
                Text = hint,
                Style = Style(host, "Meta"),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 4, 0, 0),
            });
        }
        return p;
    }

    public static Rectangle Divider(FrameworkElement host) => new()
    {
        Height = 1,
        Fill = Brush(host, "LineSoft"),
        Margin = new Thickness(0, 6, 0, 14),
    };

    /// <summary>沒東西可列時的說明。標題 + 一句為什麼,不放假的空清單。</summary>
    public static StackPanel Empty(FrameworkElement host, string title, string hint)
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
            Style = Style(host, "RowTitle"),
            HorizontalAlignment = HorizontalAlignment.Center,
            Margin = new Thickness(0, 0, 0, 6),
        });
        panel.Children.Add(new TextBlock
        {
            Text = hint,
            Style = Style(host, "Body"),
            TextWrapping = TextWrapping.Wrap,
            TextAlignment = TextAlignment.Center,
            Margin = new Thickness(0, 0, 0, 16),
        });
        return panel;
    }

    /// <summary>小標籤。用在 transport、狀態這類短字串上。</summary>
    public static Border Pill(FrameworkElement host, string text, string tone)
    {
        var tint = Brush(host, tone switch
        {
            "accent" => "Accent",
            "warn" => "Warn",
            "down" => "Down",
            _ => "Ink3",
        });
        return new Border
        {
            CornerRadius = new CornerRadius(4),
            Background = Brush(host, tone == "neutral" ? "Sunken" : $"{tone}Wash"),
            Padding = new Thickness(6, 1, 6, 2),
            Margin = new Thickness(0, 0, 8, 0),
            VerticalAlignment = VerticalAlignment.Center,
            Child = new TextBlock
            {
                Text = text,
                FontSize = 10.5,
                Foreground = tint,
                FontFamily = (FontFamily)host.FindResource("UiFont"),
            },
        };
    }
}
