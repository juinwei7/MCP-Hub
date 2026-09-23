#!/usr/bin/env bash
# 把 Python runtime、依賴、以及 gateway 原始碼包進 .app。
#
# 目標是「對方下載後雙擊就能用」—— 不需要先裝 Python,不需要碰終端機。
# 用 python-build-standalone 的 install_only 版本:它是可重新定位的完整 runtime,
# 不像系統 Python 會把絕對路徑寫死在裡面。
#
# 產出:<app>/Contents/Resources/backend/
#   bin/python3      內附的直譯器
#   lib/             標準庫 + 依賴
#   gateway/         後端原始碼(含 templates)
#
# 跑法:scripts/bundle-backend.sh "build/MCP Hub.app"

set -euo pipefail
cd "$(dirname "$0")/.."

APP="${1:-build/MCP Hub.app}"
REPO="$(cd .. && pwd)"
# 放使用者快取目錄,不放 .build —— 會被 make clean 砍掉的東西不算快取
CACHE="${XDG_CACHE_HOME:-$HOME/Library/Caches}/mcphub-runtime"
PY_VERSION="3.11.16"

# 固定 runtime 版本,不查 GitHub API。兩個理由:
#   1. 未驗證的 API 呼叫在 CI runner 上會撞速率限制回 403(實際發生過)
#   2. 「最新版」代表每次建出來的東西都可能不一樣,打包腳本不該這樣
# 要升級就改這裡,順便會被 commit 記錄下來。
PBS_RELEASE="${PBS_RELEASE:-20260901}"

say() { printf "\033[1m→ %s\033[0m\n" "$*"; }
die() { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

[ -d "$APP" ] || die "找不到 $APP,請先跑 make app"

# ── 架構檢查────────────────────────────────────
# 簽章會綁定架構。裝錯架構的 runtime 會在執行時才炸,而且訊息很難懂。
#
# 可以用 MCPHUB_ARCH 指定,讓 arm64 的機器也能包出 x86_64 的版本 ——
# GitHub 已經不提供 Intel 的 macOS runner,交叉打包是唯一還能出 Intel 版的路。
ARCH="${MCPHUB_ARCH:-$(uname -m)}"
case "$ARCH" in
    arm64)  RUNTIME_ARCH="aarch64-apple-darwin" ;;
    x86_64) RUNTIME_ARCH="x86_64-apple-darwin" ;;
    *)      die "不支援的架構 $ARCH。目前只支援 arm64 與 x86_64。" ;;
esac

# ── 取得 runtime ─────────────────────────────────────────
mkdir -p "$CACHE"
TARBALL="$CACHE/cpython-$PY_VERSION-$PBS_RELEASE-$RUNTIME_ARCH.tar.gz"

if [ ! -f "$TARBALL" ]; then
    ASSET="cpython-${PY_VERSION}%2B${PBS_RELEASE}-${RUNTIME_ARCH}-install_only_stripped.tar.gz"
    URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${ASSET}"

    say "下載 runtime(cpython $PY_VERSION / $RUNTIME_ARCH / release $PBS_RELEASE)"
    curl -sSfL --max-time 300 "$URL" -o "$TARBALL.tmp" \
        || die "下載失敗:$URL
   確認 PBS_RELEASE=$PBS_RELEASE 與 PY_VERSION=$PY_VERSION 這組合存在,
   或設 PBS_RELEASE 環境變數指定其他版本。"
    mv "$TARBALL.tmp" "$TARBALL"
else
    say "使用已快取的 runtime"
fi

# ── 解開到 bundle ────────────────────────────────────────
BACKEND="$APP/Contents/Resources/backend"
rm -rf "$BACKEND"
mkdir -p "$BACKEND"

say "解開 runtime 到 bundle"
# tarball 內層是 python/ 目錄,--strip-components 1 把它拆掉
tar -xzf "$TARBALL" -C "$BACKEND" --strip-components 1

PY="$BACKEND/bin/python3"
[ -x "$PY" ] || die "解開後找不到 $PY"

# ── 裝依賴 ───────────────────────────────────────────────
say "安裝依賴到 bundle"
"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
# 刻意不加 --no-compile。少了 .pyc,Python 會在「第一次執行時」自己產生 ——
# 而那是寫進已經簽章的 app bundle 裡,當場破壞封印(codesign --verify 會說
# a sealed resource is missing or invalid)。打包時就編好,執行時就沒有
# 任何東西需要寫。壓縮後的體積差不到 1MB,換一個不會自我破壞的 bundle。
"$PY" -m pip install --quiet -r "$REPO/requirements.txt" \
    || die "依賴安裝失敗"

# ── 複製後端原始碼 ───────────────────────────────────────
say "複製 gateway 原始碼"
rsync -a --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$REPO/gateway/" "$BACKEND/gateway/"

# ── 精簡 ─────────────────────────────────────────────────
# 使用者要下載這包,所以每一 MB 都是別人的時間。全部都實際量過才砍:
#
#   libpython3.11.dylib  16M  bin/python3.11 是靜態連結的(otool -L 沒有這一行),
#                             而且沒有任何 .so 連到它 —— 純多餘
#   pip/setuptools/ensurepip 13M  依賴在打包時就裝好了,送出去的 app 不會再安裝東西
#   tcl / tk             6M   只有 _tkinter 用得到,而後端沒有 GUI
#   include/             1M   C 標頭檔,執行期用不到
#   distutils/lib2to3/pydoc_data  2M  都沒有被匯入
#
# 砍完會跑一次匯入驗證,少砍到東西當場就會失敗。
say "精簡不需要的檔案"

LIBDIR="$BACKEND/lib/python${PY_VERSION%.*}"

# 1. 多餘的 libpython。靜態連結的直譯器不需要它。
rm -f "$BACKEND/lib/libpython"*.dylib "$BACKEND/lib/libpython"*.a

# 2. 打包工具鏈
rm -rf "$LIBDIR/ensurepip" "$LIBDIR/site-packages/pip" \
       "$LIBDIR/site-packages/setuptools" "$LIBDIR/site-packages/pkg_resources" \
       "$LIBDIR/site-packages"/pip-*.dist-info \
       "$LIBDIR/site-packages"/setuptools-*.dist-info
rm -f "$BACKEND/bin/pip" "$BACKEND/bin/pip3" "$BACKEND/bin"/pip3.*

# 3. Tk 整組 —— 函式庫、模組、綁定
rm -rf "$BACKEND/lib/tcl"* "$BACKEND/lib/tk"* "$LIBDIR/tkinter" "$LIBDIR/turtledemo"
rm -f "$BACKEND/lib/libtcl"*.dylib "$BACKEND/lib/libtk"*.dylib \
      "$LIBDIR/lib-dynload/_tkinter"*.so "$LIBDIR/turtle.py"

# 4. 開發用的東西
rm -rf "$BACKEND/include" "$BACKEND/share" "$LIBDIR/config-"* \
       "$LIBDIR/idlelib" "$LIBDIR/distutils" "$LIBDIR/lib2to3" "$LIBDIR/pydoc_data"

# 5. 測試套件(stdlib 與第三方的都算)
find "$BACKEND" -type d \( -name 'test' -o -name 'tests' \) -prune -exec rm -rf {} + 2>/dev/null || true

# 6. 剝掉原生模組的符號表。cryptography 的 _rust.abi3.so 一個就 11M,
#    剝完 9.1M —— 而 debug 符號對使用者沒有任何用處。
#    最後會重新簽章,所以改動二進位是安全的。
find "$BACKEND" -name "*.so" -exec strip -S -x {} + 2>/dev/null || true

# 7. 把整包都先編成 .pyc。
#
#    少一個 .pyc,Python 就會在第一次匯入時自己補上 —— 而那是寫進已經簽章的
#    app bundle 裡,當場破壞封印(codesign --verify 會說 a sealed resource is
#    missing or invalid)。
#
#    這個問題只差一個檔案:實測比對啟動前後,整個 bundle 只多出
#    concurrent/futures/__pycache__/thread.cpython-311.pyc —— runtime 本身
#    漏編的一個標準庫模組。所以不能只編 gateway,要整包編過。
#
#    -q 兩次是連「無法編譯」的檔案也不要吵(stdlib 裡有幾個刻意壞掉的測試樣本)。
"$PY" -m compileall -qq "$BACKEND" >/dev/null 2>&1 || true

# ── 驗證 ─────────────────────────────────────────────────
say "驗證內附的後端可用"
PYTHONPATH="$BACKEND" "$PY" -c "
import gateway.web, gateway.api, gateway.store
print('  gateway 匯入成功')
" || die "內附的後端匯入失敗 —— 可能缺依賴"

SIZE=$(du -sh "$APP" | cut -f1)
printf "\n\033[32m✓ 已內附後端,%s 現在是自足的(%s)\033[0m\n" "$(basename "$APP")" "$SIZE"
