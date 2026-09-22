using MCPHub.Core;
using Xunit;

namespace MCPHub.Core.Tests;

/// <summary>
/// 接入指令是使用者會直接複製去執行的東西 —— 打錯一個環境變數,
/// 聚合器會讀到空的 actions.db,而且不會有任何錯誤訊息,只是「工具怎麼都沒出現」。
/// </summary>
public class ClaudeCommandTests
{
    private const string DataDir = @"C:\Users\me\AppData\Roaming\MCP Hub";
    private const string Python = @"C:\proj\.venv\Scripts\python.exe";

    private static string Build() =>
        BackendSupervisor.ClaudeAddCommand(DataDir, Python, @"C:\proj");

    /// <summary>
    /// 期望值也走 Path.Combine。寫死反斜線的話,在 macOS 上開發時會因為
    /// 分隔字元不同而紅掉 —— 那是環境差異,不是程式壞了。
    /// </summary>
    private static string Under(string name) => Path.Combine(DataDir, name);

    [Fact]
    public void CarriesTheThreeEnvironmentVariables()
    {
        var cmd = Build();
        // 這三個少任何一個,Hub 的設定都不會生效
        Assert.Contains("PYTHONPATH=", cmd);
        Assert.Contains("MCP_HUB_DB=", cmd);
        Assert.Contains("MCP_HUB_KEY=", cmd);
    }

    [Fact]
    public void PointsAtTheDataDirectoryNotTheProject()
    {
        // 資料在使用者的 AppData,不在專案目錄 —— 這正是最容易搞錯的地方
        var cmd = Build();
        Assert.Contains(Under("actions.db"), cmd);
        Assert.Contains(Under(".secret_key"), cmd);
    }

    [Fact]
    public void UsesBacktickContinuation()
    {
        // Windows 使用者多半在 PowerShell 裡貼。反斜線在那裡是路徑分隔字元,
        // 用它續行會把指令切成好幾段各自失敗
        var cmd = Build();
        Assert.Contains("`", cmd);
        Assert.DoesNotContain("\\\n", cmd);
    }

    [Fact]
    public void QuotesPathsSoSpacesSurvive()
    {
        // 「MCP Hub」中間有空格,沒引號的話 PowerShell 會把它當成兩個參數
        var cmd = Build();
        Assert.Contains($"\"{Under("actions.db")}\"", cmd);
        Assert.Contains($"\"{Python}\"", cmd);
    }

    [Fact]
    public void LaunchesTheAggregatorNotTheWebBackend()
    {
        // gateway.web 是 app 自己起的 HTTP 後端;Claude 要的是 stdio 的聚合器
        var cmd = Build();
        Assert.Contains("-m gateway.hub_server", cmd);
        Assert.DoesNotContain("gateway.web", cmd);
    }
}
