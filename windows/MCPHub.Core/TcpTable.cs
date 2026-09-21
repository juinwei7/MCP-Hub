using System.Net;
using System.Runtime.InteropServices;

namespace MCPHub.Core;

/// <summary>
/// 查出哪個程序正在監聽某個 port。
///
/// .NET 內建的 IPGlobalProperties.GetActiveTcpListeners() 拿得到 port 卻拿不到 PID,
/// 而我們需要 PID 才能告訴使用者「去關哪一個程式」——「port 被佔用」但不說是誰,
/// 等於把問題丟回給使用者。
///
/// 另一個選擇是解析 netstat -ano 的輸出,但那受系統語系影響(中文 Windows 的欄位
/// 標題不一樣),所以直接用 GetExtendedTcpTable。
/// </summary>
internal static class TcpTable
{
    public static int? FindListenerPid(int port)
    {
        var size = 0;
        // 先問需要多大的緩衝區
        GetExtendedTcpTable(IntPtr.Zero, ref size, false, AF_INET,
                            TCP_TABLE_OWNER_PID_LISTENER, 0);
        if (size <= 0) return null;

        var buffer = Marshal.AllocHGlobal(size);
        try
        {
            if (GetExtendedTcpTable(buffer, ref size, false, AF_INET,
                                    TCP_TABLE_OWNER_PID_LISTENER, 0) != 0)
            {
                return null;
            }

            var count = Marshal.ReadInt32(buffer);
            var rowSize = Marshal.SizeOf<MIB_TCPROW_OWNER_PID>();
            var cursor = buffer + sizeof(int);

            for (var i = 0; i < count; i++)
            {
                var row = Marshal.PtrToStructure<MIB_TCPROW_OWNER_PID>(cursor);
                if (LocalPort(row) == port) return (int)row.owningPid;
                cursor += rowSize;
            }
            return null;
        }
        catch (Exception)
        {
            return null;   // 查不到就當作沒人佔 —— 後續的 bind 會給出真正的答案
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    /// <summary>port 在結構裡是網路位元組序,而且塞在 4 bytes 的低兩位。</summary>
    private static int LocalPort(MIB_TCPROW_OWNER_PID row) =>
        IPAddress.NetworkToHostOrder((short)(row.localPort & 0xFFFF)) & 0xFFFF;

    private const int AF_INET = 2;
    private const int TCP_TABLE_OWNER_PID_LISTENER = 3;

    [StructLayout(LayoutKind.Sequential)]
    private struct MIB_TCPROW_OWNER_PID
    {
        public uint state;
        public uint localAddr;
        public uint localPort;
        public uint remoteAddr;
        public uint remotePort;
        public uint owningPid;
    }

    [DllImport("iphlpapi.dll", SetLastError = true)]
    private static extern uint GetExtendedTcpTable(
        IntPtr table, ref int size, bool order, int family, int tableClass, int reserved);
}
