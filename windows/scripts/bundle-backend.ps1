<#
.SYNOPSIS
    把 Python runtime 與 gateway 原始碼內附進發佈目錄,讓 app 自足。

.DESCRIPTION
    和 macOS 的 bundle-backend.sh 同一件事:使用者不該為了跑這個 app 先裝 Python。
    BackendSupervisor.ResolvePython() 會先看 MCPHUB_PYTHON,沒有就找
    <執行檔目錄>\backend\python.exe —— 這個腳本就是把那個 python.exe 放好。

    runtime 取自 python-build-standalone,那是整包可搬移的 CPython。
    Windows 的 install_only 版解開後 python.exe 直接在根目錄,和 macOS 的
    bin/python3 不同,ResolvePython 已經照這個形狀寫。

.PARAMETER Dest
    發佈目錄(dotnet publish 的輸出)。
#>
param(
    [Parameter(Mandatory = $true)][string]$Dest
)

$ErrorActionPreference = 'Stop'

$PyVersion = '3.11.16'
# 和 macOS 的 bundle-backend.sh 用同一個 release。兩邊分開維護版號的話,
# 遲早會變成 macOS 內附 3.11.16、Windows 內附 3.11.11 這種難查的差異。
$PbsRelease = '20260901'
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Say($m) { Write-Host "-> $m" }
function Die($m) { Write-Host "::error::$m"; exit 1 }

if (-not (Test-Path $Dest)) { Die "找不到發佈目錄 $Dest" }

# ── 取得 runtime ─────────────────────────────────────────
$cache = Join-Path $env:TEMP 'mcphub-runtime'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$archive = Join-Path $cache "cpython-$PyVersion-$PbsRelease-win64.tar.gz"

if (-not (Test-Path $archive)) {
    $asset = "cpython-$PyVersion%2B$PbsRelease-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsRelease/$asset"
    Say "下載 runtime(cpython $PyVersion / win64 / release $PbsRelease)"
    try {
        Invoke-WebRequest -Uri $url -OutFile "$archive.tmp" -UseBasicParsing
    } catch {
        Die "下載失敗:$url`n   確認 PbsRelease=$PbsRelease 與 PyVersion=$PyVersion 這組合存在。"
    }
    Move-Item "$archive.tmp" $archive
} else {
    Say "使用已快取的 runtime"
}

# ── 解開 ─────────────────────────────────────────────────
$backend = Join-Path $Dest 'backend'
if (Test-Path $backend) { Remove-Item -Recurse -Force $backend }
New-Item -ItemType Directory -Force -Path $backend | Out-Null

Say "解開 runtime"
# Windows 10 1803 之後內建 bsdtar,能直接吃 .tar.gz。
# --strip-components 1 拆掉壓縮檔裡那層 python\ 目錄
tar -xzf $archive -C $backend --strip-components 1
if ($LASTEXITCODE -ne 0) { Die "解壓縮失敗" }

$py = Join-Path $backend 'python.exe'
if (-not (Test-Path $py)) { Die "解開後找不到 $py" }

# ── 裝依賴 ───────────────────────────────────────────────
Say "安裝依賴到 bundle"
& $py -m pip install --quiet --upgrade pip 2>&1 | Out-Null
& $py -m pip install --quiet --no-compile -r (Join-Path $Repo 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Die "依賴安裝失敗" }

# ── 複製後端原始碼 ───────────────────────────────────────
Say "複製 gateway 原始碼"
$gateway = Join-Path $backend 'gateway'
Copy-Item -Recurse -Force (Join-Path $Repo 'gateway') $gateway
Get-ChildItem -Recurse -Force -Directory -Filter '__pycache__' $gateway |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ── 精簡 ─────────────────────────────────────────────────
# 使用者要下載這包,每一 MB 都是別人的時間。和 macOS 那邊砍同一批東西:
#   pip / setuptools / ensurepip   依賴打包時就裝好,app 不會再安裝東西
#   tcl / tk / tkinter             後端沒有 GUI
#   include                        C 標頭檔,執行期用不到
#   distutils / lib2to3 / pydoc_data   都沒有被匯入
# 砍完會跑一次匯入驗證,少砍到東西當場就會失敗。
Say "精簡不需要的檔案"

foreach ($junk in 'Lib\idlelib', 'Lib\tkinter', 'Lib\turtledemo', 'Lib\test',
                  'Lib\ensurepip', 'Lib\distutils', 'Lib\lib2to3', 'Lib\pydoc_data',
                  'Lib\site-packages\pip', 'Lib\site-packages\setuptools',
                  'Lib\site-packages\pkg_resources',
                  'tcl', 'include', 'share') {
    $path = Join-Path $backend $junk
    if (Test-Path $path) { Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue }
}
# pip / setuptools 的 metadata 與啟動器
Get-ChildItem -Path (Join-Path $backend 'Lib\site-packages') -Filter '*.dist-info' `
              -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(pip|setuptools)-' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path (Join-Path $backend 'Scripts') -Filter 'pip*' `
              -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
# 第三方套件自己帶的測試
Get-ChildItem -Recurse -Force -Directory $backend -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in 'test','tests' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Force -Directory -Filter '__pycache__' $backend |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ── 驗證 ─────────────────────────────────────────────────
# 驗的是「內附的這一份」能不能用,不是 runner 上剛好裝了什麼
Say "驗證內附的後端可用"
$env:PYTHONPATH = $backend
# Windows 主控台預設 cp1252,編不了中文。這裡跑的是 python -c,
# 不會經過 gateway.console.use_utf8,所以要自己把 UTF-8 模式打開 ——
# 否則印出來的字本身就會丟 UnicodeEncodeError,看起來像匯入失敗。
$env:PYTHONUTF8 = '1'
& $py -c "import gateway.web, gateway.api, gateway.store, gateway.hub_server; print('  gateway import ok')"
$ok = $LASTEXITCODE -eq 0
Remove-Item Env:\PYTHONPATH
Remove-Item Env:\PYTHONUTF8
if (-not $ok) { Die "內附的後端匯入失敗 —— 可能缺依賴" }

$size = (Get-ChildItem -Recurse -Force $Dest | Measure-Object -Property Length -Sum).Sum
Write-Host ("OK 已內附後端,發佈目錄現在是自足的({0:N0} MB)" -f ($size / 1MB))
