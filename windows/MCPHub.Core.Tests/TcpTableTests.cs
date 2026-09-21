using System.Net;
using System.Net.Sockets;
using MCPHub.Core;
using Xunit;

namespace MCPHub.Core.Tests;

/// <summary>
/// port 佔用偵測。
///
/// 重點不只是「有沒有人在聽」,而是「是誰」—— 只說 port 被佔用卻不說是哪個程式,
/// 等於把問題丟回給使用者。macOS 版用 lsof 拿 PID,Windows 用 GetExtendedTcpTable。
/// </summary>
public class TcpTableTests
{
    [Fact]
    public void FindsOwnPidWhenListening()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var pid = BackendSupervisor.PortHolder(port);

            Assert.NotNull(pid);
            Assert.Equal(Environment.ProcessId, pid);
        }
        finally
        {
            listener.Stop();
        }
    }

    [Fact]
    public void ReturnsNullForFreePort()
    {
        // 先佔一個再放掉,拿到一個幾乎確定沒人用的 port 號
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();

        Assert.Null(BackendSupervisor.PortHolder(port));
    }

    [Fact]
    public void ByteOrderIsHandled()
    {
        // port 在 TCP 表裡是網路位元組序。沒處理的話 8765 會被讀成別的數字,
        // 於是「port 被佔用」永遠偵測不到 —— 這種 bug 不會拋例外,只會靜默失效。
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            Assert.InRange(port, 1, 65535);
            Assert.NotNull(BackendSupervisor.PortHolder(port));

            // 位元組序處理錯的話,交換高低位之後反而會找得到
            var swapped = ((port & 0xFF) << 8) | ((port >> 8) & 0xFF);
            if (swapped != port && swapped is > 0 and <= 65535)
            {
                Assert.Null(BackendSupervisor.PortHolder(swapped));
            }
        }
        finally
        {
            listener.Stop();
        }
    }
}
