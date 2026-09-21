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
PY_VERSION="3.11"

say() { printf "\033[1m→ %s\033[0m\n" "$*"; }
die() { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

[ -d "$APP" ] || die "找不到 $APP,請先跑 make app"

# ── 架構檢查────────────────────────────────────
# 簽章會綁定架構。裝錯架構的 runtime 會在執行時才炸,而且訊息很難懂。
ARCH="$(uname -m)"
case "$ARCH" in
    arm64)  RUNTIME_ARCH="aarch64-apple-darwin" ;;
    x86_64) RUNTIME_ARCH="x86_64-apple-darwin" ;;
    *)      die "不支援的架構 $ARCH。目前只支援 arm64 與 x86_64。" ;;
esac

# ── 取得 runtime ─────────────────────────────────────────
mkdir -p "$CACHE"
TARBALL="$CACHE/cpython-$PY_VERSION-$RUNTIME_ARCH.tar.gz"

if [ ! -f "$TARBALL" ]; then
    say "查詢 python-build-standalone 最新版本"
    URL=$(curl -sSf --max-time 30 \
        "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest" \
        | python3 -c "
import sys, json
assets = json.load(sys.stdin).get('assets', [])
want = ('cpython-$PY_VERSION', '$RUNTIME_ARCH', 'install_only_stripped.tar.gz')
for a in assets:
    n = a['name']
    if all(w in n for w in want):
        print(a['browser_download_url']); break
") || die "查不到可用的 runtime,請確認網路"
    [ -n "$URL" ] || die "找不到 $PY_VERSION / $RUNTIME_ARCH 的 install_only 版本"

    say "下載 runtime($(basename "$URL"))"
    curl -sSfL --max-time 300 "$URL" -o "$TARBALL.tmp" || die "下載失敗"
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
"$PY" -m pip install --quiet --no-compile -r "$REPO/requirements.txt" \
    || die "依賴安裝失敗"

# ── 複製後端原始碼 ───────────────────────────────────────
say "複製 gateway 原始碼"
rsync -a --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$REPO/gateway/" "$BACKEND/gateway/"

# ── 精簡 ─────────────────────────────────────────────────
# 測試、pip 快取、.pyc 都不需要跟著發佈,體積差很多
say "精簡不需要的檔案"
find "$BACKEND" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$BACKEND" -type d -name 'test' -path '*/lib/python*' -prune -exec rm -rf {} + 2>/dev/null || true
find "$BACKEND" -type d -name 'tests' -path '*/lib/python*' -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$BACKEND/lib/python$PY_VERSION/idlelib" \
       "$BACKEND/lib/python$PY_VERSION/tkinter" \
       "$BACKEND/lib/python$PY_VERSION/turtledemo" \
       "$BACKEND/share" 2>/dev/null || true

# ── 驗證 ─────────────────────────────────────────────────
say "驗證內附的後端可用"
PYTHONPATH="$BACKEND" "$PY" -c "
import gateway.web, gateway.api, gateway.store
print('  gateway 匯入成功')
" || die "內附的後端匯入失敗 —— 可能缺依賴"

SIZE=$(du -sh "$APP" | cut -f1)
printf "\n\033[32m✓ 已內附後端,%s 現在是自足的(%s)\033[0m\n" "$(basename "$APP")" "$SIZE"
