using System.Diagnostics;
using System.IO;
using Microsoft.Win32;

namespace MCPHub.App;

/// <summary>
/// 開機時自動啟動。
///
/// 用 HKCU 的 Run 機碼,不是在「啟動」資料夾放 .lnk —— 建捷徑要透過 COM 的
/// IShellLink,為了一個布林開關拉進 COM interop 不划算,而 Run 機碼寫一行字串就好,
/// 使用者也能在「工作管理員 → 啟動」裡看到並自己關掉。
///
/// 只碰 HKCU:HKLM 需要管理員權限,而這是使用者自己的 app,
/// 沒有理由要求提權。
/// </summary>
internal static class StartupShortcut
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "MCP Hub";

    private static string? ExecutablePath
    {
        get
        {
            // .NET 單檔發佈時 Assembly.Location 是空字串,要問主程序
            var path = Process.GetCurrentProcess().MainModule?.FileName;
            return string.IsNullOrEmpty(path) || !File.Exists(path) ? null : path;
        }
    }

    public static bool IsEnabled
    {
        get
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKey);
                return key?.GetValue(ValueName) is string s && s.Length > 0;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException
                                           or System.Security.SecurityException)
            {
                return false;
            }
        }
    }

    /// <summary>回傳 null 代表成功,否則是可以顯示給使用者看的原因。</summary>
    public static string? Set(bool enabled)
    {
        var exe = ExecutablePath;
        if (enabled && exe is null)
        {
            return "找不到執行檔路徑,無法設定開機啟動。";
        }

        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKey, writable: true);
            if (key is null) return "打不開登錄檔的 Run 機碼。";
            if (enabled)
            {
                // 路徑可能有空格(Program Files),一定要加引號
                key.SetValue(ValueName, $"\"{exe}\"");
            }
            else
            {
                key.DeleteValue(ValueName, throwOnMissingValue: false);
            }
            return null;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException
                                       or System.Security.SecurityException)
        {
            return $"設定失敗:{e.Message}";
        }
    }
}
