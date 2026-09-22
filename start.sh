#!/usr/bin/env bash
# 從原始碼啟動後端:建 venv → 裝依賴 → 跑 gateway.web,並印出接入 Claude 的指令。
#
# 這條路沒有介面 —— 網頁管理台已經由原生 app 取代(見 README)。
# 適合的情境是:在沒有桌面的機器上跑、或是開發後端時要看即時的 log。
# 想要介面的話去下載 Release,或 cd macos && make dist。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
PORT="${MCP_HUB_PORT:-8765}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "找不到 $PY,請先安裝 Python 3.11+(或設 PYTHON=你的python路徑)" >&2
  exit 1
fi

if [ ! -d "$VENV" ]; then
  echo "→ 建立虛擬環境 $VENV"
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"

echo "→ 安裝 / 更新依賴"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r requirements.txt

PROJ="$(pwd)"
cat <<EOF

──────────────────────────────────────────────────────────────
MCP Hub 後端準備就緒

  JSON API：http://localhost:${PORT}/api/v1/health
  (沒有網頁介面。要圖形介面請用原生 app,見 README)

  接入 Claude Code(在任意目錄執行皆可):
    claude mcp add my-hub -e PYTHONPATH="${PROJ}" -- "${VPY}" -m gateway.hub_server

  注意:這個指令讓聚合器使用「專案目錄」的 actions.db。
  如果你也在用原生 app,它的資料在別的地方 —— 請改用 app 裡
  「複製接入 Claude 的指令」那顆按鈕,否則兩邊會各看各的資料庫。

  移除:claude mcp remove my-hub
──────────────────────────────────────────────────────────────

啟動後端中…(Ctrl-C 結束)
EOF

exec "$VPY" -m gateway.web
