using System.Diagnostics;
using MCPHub.Core;
using Xunit;

namespace MCPHub.Core.Tests;

/// <summary>
/// Job Object 是否真的會在父程序結束時收掉子程序。
///
/// 這是整個 Windows client 技術風險最高的一點,也是它相對 macOS 的優勢 ——
/// 值得用真的程序驗證,而不是只確認 P/Invoke 的簽章編得過。
///
/// 測試方式刻意對照 macOS 的 acceptance.sh:起真的程序、真的關掉、真的數殘留。
/// </summary>
public class JobObjectTests
{
    /// <summary>起一個會活一段時間的子程序,用來觀察它有沒有被收掉。</summary>
    private static Process StartSleeper(int seconds = 60)
    {
        var info = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        info.ArgumentList.Add("/c");
        // timeout 需要主控台,ping 到 loopback 是常見的等待手法
        info.ArgumentList.Add($"ping -n {seconds + 1} 127.0.0.1 > nul");
        return Process.Start(info)!;
    }

    [Fact]
    public void JobIsCreatedSuccessfully()
    {
        using var job = new JobObject();
        Assert.True(job.IsValid, "Job Object 建不起來的話,整個孤兒程序防護就沒了");
    }

    [Fact]
    public void AssignedProcessIsKilledWhenJobHandleCloses()
    {
        // 這就是 macOS 做不到的事:父程序一結束,系統連帶收掉 Job 內的程序
        var child = StartSleeper();
        try
        {
            var job = new JobObject();
            Assert.True(job.IsValid);
            Assert.True(job.Assign(child.Handle), "把程序納入 Job 應該要成功");
            Assert.False(child.HasExited, "納入 Job 不該立刻殺掉它");

            job.Dispose();   // 等同於父程序結束時 handle 被系統關閉

            Assert.True(child.WaitForExit(10_000),
                        "Job handle 關閉後,子程序應該被系統收掉");
        }
        finally
        {
            if (!child.HasExited) child.Kill(entireProcessTree: true);
            child.Dispose();
        }
    }

    [Fact]
    public void ProcessOutsideJobSurvives()
    {
        // 反面:沒被納入 Job 的程序不該受影響 —— 否則等於誤殺
        var outsider = StartSleeper();
        try
        {
            using (var job = new JobObject())
            {
                Assert.True(job.IsValid);
            }   // Job 關閉

            Thread.Sleep(500);
            Assert.False(outsider.HasExited, "不在 Job 裡的程序不該被收掉");
        }
        finally
        {
            if (!outsider.HasExited) outsider.Kill(entireProcessTree: true);
            outsider.Dispose();
        }
    }

    [Fact]
    public void AssignRejectsInvalidHandle()
    {
        using var job = new JobObject();
        Assert.False(job.Assign(IntPtr.Zero));
    }

    [Fact]
    public void DisposeIsIdempotent()
    {
        var job = new JobObject();
        job.Dispose();
        job.Dispose();   // 不該拋例外
        Assert.False(job.IsValid);
    }
}
