<#
.SYNOPSIS
    起 MCP Hub、等它就緒、把主視窗截下來,然後收乾淨。

.DESCRIPTION
    開發機是 macOS,這個 app 在本機執行不了 —— 編譯器能抓型別錯誤,但抓不到
    「版面跑掉」「顏色在深色模式下看不見」「控制項疊在一起」這類問題。
    CI 的 Windows runner 是唯一能真的看到畫面的地方,所以把截圖變成建置的一部分。

    用 PrintWindow 而不是抓整個螢幕:PrintWindow 直接問視窗要它的內容,
    不管它有沒有被別的東西蓋住、有沒有在前景。CI 上沒有人在操作滑鼠,
    但也沒有保證視窗一定在最上層。

.PARAMETER Exe
    MCPHub.exe 的路徑。

.PARAMETER Out
    輸出的 PNG 路徑。
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win {
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

function Fail($message) {
    Write-Host "::error::$message"
    exit 1
}

Write-Host "啟動 $Exe"
$proc = Start-Process -FilePath $Exe -PassThru

try {
    # ── 等後端就緒 ────────────────────────────────────────
    # 視窗會先出現在「啟動中」的狀態;要等後端起來才截得到真正的畫面
    $ready = $false
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            Fail "app 在就緒前就結束了(exit code $($proc.ExitCode))"
        }
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/v1/health' `
                                   -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            # 還沒起來,繼續等
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        # 後端沒起來不代表不能截 —— 「後端啟動失敗」那個畫面本身也要看得對,
        # 所以繼續截,只是把這件事講出來
        Write-Host "::warning::後端在 ${TimeoutSeconds}s 內沒有就緒,截到的會是啟動失敗的畫面"
    }

    # 讓輪詢跑完一輪,畫面上才有資料而不是空的骨架
    Start-Sleep -Seconds 3

    # ── 找視窗 ────────────────────────────────────────────
    $hwnd = [IntPtr]::Zero
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { break }
        $hwnd = [Win]::FindWindow($null, 'MCP Hub')
        if ($hwnd -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 300
    }

    if ($hwnd -eq [IntPtr]::Zero) {
        # 「找不到視窗」本身沒有任何線索。把能拿到的都倒出來,
        # 不然下一輪 CI 只是重複同一個問號。
        Write-Host "--- 診斷 ---"
        if ($proc.HasExited) {
            Write-Host "app 已結束,exit code $($proc.ExitCode)"
        } else {
            Write-Host "app 還在跑(pid $($proc.Id)),但沒有標題為「MCP Hub」的視窗"
        }

        $log = Join-Path $env:MCPHUB_DATA_DIR 'crash.log'
        if (Test-Path $log) {
            Write-Host "--- crash.log ---"
            Get-Content $log | Select-Object -Last 60 | ForEach-Object { Write-Host $_ }
        } else {
            Write-Host "(沒有 crash.log:$log)"
        }

        Write-Host "--- 目前有標題的最上層視窗 ---"
        Get-Process | Where-Object { $_.MainWindowTitle } |
            Select-Object ProcessName, Id, MainWindowTitle |
            Format-Table -AutoSize | Out-String | ForEach-Object { Write-Host $_ }

        Fail "找不到標題為「MCP Hub」的視窗"
    }

    [void][Win]::SetForegroundWindow($hwnd)
    Start-Sleep -Milliseconds 500

    $rect = New-Object Win+RECT
    if (-not [Win]::GetWindowRect($hwnd, [ref]$rect)) { Fail "GetWindowRect 失敗" }
    $w = $rect.Right - $rect.Left
    $h = $rect.Bottom - $rect.Top
    if ($w -le 0 -or $h -le 0) { Fail "視窗尺寸不合理:${w}x${h}" }
    Write-Host "視窗 ${w}x${h}"

    # ── 截圖 ──────────────────────────────────────────────
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $gfx.GetHdc()
    try {
        # 0x2 = PW_RENDERFULLCONTENT。沒有這個旗標,DWM 合成的視窗
        # (WPF 就是)會截出一片空白
        if (-not [Win]::PrintWindow($hwnd, $hdc, 2)) { Fail "PrintWindow 失敗" }
    } finally {
        $gfx.ReleaseHdc($hdc)
    }
    $gfx.Dispose()

    $dir = Split-Path -Parent $Out
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Host "已存 $Out"
}
finally {
    # app 是系統匣程式,關掉視窗不會結束 —— 一定要真的殺掉,
    # 否則 runner 上會留下它和它起的後端
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $proc.WaitForExit(5000) | Out-Null
    }
    Get-Process -Name python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path -like '*venv*' } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
