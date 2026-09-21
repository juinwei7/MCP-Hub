using System.Runtime.InteropServices;

namespace MCPHub.Core;

/// <summary>
/// Windows Job Object,設定成「父程序結束時連帶結束成員」。
///
/// 這是 Windows 比 macOS 好做的地方。macOS 沒有 PDEATHSIG,子程序無法跟著父程序死,
/// 只能靠 PID 檔在下次啟動時收拾殘局(見 macos/Sources/MCPHubCore/BackendSupervisor.swift)。
/// Windows 的 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 直接解決這件事 —— 連父程序被工作管理員
/// 強制結束也算數,因為 handle 關閉是作業系統做的。
///
/// 所以這裡不照抄 macOS 的變通做法。PID 檔仍然保留,但只當作第二道保險。
/// </summary>
public sealed class JobObject : IDisposable
{
    private IntPtr _handle;

    public bool IsValid => _handle != IntPtr.Zero;

    /// <summary>建立 Job 並設定 kill-on-close。失敗時 IsValid 為 false,呼叫端要能接受。</summary>
    public JobObject()
    {
        _handle = CreateJobObject(IntPtr.Zero, null);
        if (_handle == IntPtr.Zero) return;

        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION
            {
                LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            },
        };

        var size = Marshal.SizeOf<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>();
        var buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(info, buffer, false);
            if (!SetInformationJobObject(_handle, JobObjectExtendedLimitInformation, buffer, (uint)size))
            {
                Close();   // 設不起來的 Job 沒有意義,不如當作沒有
            }
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    /// <summary>把一個程序納入 Job。回傳是否成功。</summary>
    public bool Assign(IntPtr processHandle)
    {
        if (!IsValid || processHandle == IntPtr.Zero) return false;
        return AssignProcessToJobObject(_handle, processHandle);
    }

    public void Dispose() => Close();

    private void Close()
    {
        if (_handle == IntPtr.Zero) return;
        CloseHandle(_handle);   // 這一刻起,Job 內的程序會被系統收掉
        _handle = IntPtr.Zero;
    }

    // ── P/Invoke ──────────────────────────────────────────

    private const int JobObjectExtendedLimitInformation = 9;
    private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(
        IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);
}
