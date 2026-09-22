using System.IO;
using System.Windows;
using Microsoft.Win32;

namespace MCPHub.App;

/// <summary>
/// 跟著系統的淺色 / 深色走。
///
/// WPF 沒有內建的主題偵測 —— 不像 SwiftUI 給你 NSColor(name:) 那種會自己跟著
/// 外觀變的顏色,這裡得自己讀登錄檔、自己在系統設定改變時換掉字典。
/// </summary>
public static class ThemeManager
{
    private const string PersonalizeKey =
        @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize";

    private static ResourceDictionary? _current;

    public static bool IsDark { get; private set; }

    public static void Start()
    {
        Apply(ReadIsDark());
        // 使用者在「設定 → 個人化」切換時,系統會廣播 WM_SETTINGCHANGE。
        // SystemEvents 幫我們把那條訊息接起來,不必自己開一個隱藏視窗收訊息。
        SystemEvents.UserPreferenceChanged += (_, e) =>
        {
            if (e.Category is UserPreferenceCategory.General or UserPreferenceCategory.VisualStyle)
            {
                var dark = ReadIsDark();
                if (dark != IsDark) Application.Current.Dispatcher.Invoke(() => Apply(dark));
            }
        };
    }

    public static void Stop() => SystemEvents.UserPreferenceChanged -= null;

    private static bool ReadIsDark()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(PersonalizeKey);
            // 沒有這個值代表舊版 Windows 或被政策鎖住 —— 當成淺色,
            // 那是 Windows 的預設,猜錯的代價比猜深色小
            return key?.GetValue("AppsUseLightTheme") is int v && v == 0;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException
                                       or System.Security.SecurityException)
        {
            return false;
        }
    }

    private static void Apply(bool dark)
    {
        IsDark = dark;
        var next = new ResourceDictionary
        {
            Source = new Uri(dark ? "Theme/Palette.Dark.xaml" : "Theme/Palette.xaml",
                             UriKind.Relative),
        };

        var merged = Application.Current.Resources.MergedDictionaries;
        // 換掉舊的那一份而不是整包重建 —— 其他字典(Controls.xaml)裡的
        // DynamicResource 參照會自己跟著更新,畫面上的控制項不用重新建立
        if (_current is not null) merged.Remove(_current);
        merged.Insert(0, next);
        _current = next;
    }
}
