#!/usr/bin/env bash
# 一鍵啟動 MCP Hub:建 venv → 裝依賴 → 開管理台,並印出接入 Claude 的指令。
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
✅ MCP Hub 準備就緒

  管理台：http://localhost:${PORT}

  接入 Claude Code(在任意目錄執行皆可):
    claude mcp add my-hub -e PYTHONPATH="${PROJ}" -- "${VPY}" -m gateway.hub_server

  移除:claude mcp remove my-hub
──────────────────────────────────────────────────────────────

啟動管理台中…(Ctrl-C 結束)
EOF

exec "$VPY" -m gateway.web
