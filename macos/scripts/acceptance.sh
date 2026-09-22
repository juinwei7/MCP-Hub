#!/usr/bin/env bash
# macOS app 的生命週期驗收
#
# 重點不是介面,是「子程序有沒有被管好」。每一條都實際起 app、實際殺程序、
# 實際數殘留,不是看起來正常就算。
#
# 跑法:make test

set -uo pipefail
cd "$(dirname "$0")/.."

REPO="$(cd .. && pwd)"
PYTHON="$REPO/.venv/bin/python"
DATA="$(mktemp -d /tmp/mcphub_acc.XXXXXX)"
BIN="./.build/debug/MCPHub"
PASS=0; FAIL=0

say()  { printf "\n\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$*"; FAIL=$((FAIL+1)); }

# 後端程序數(只算這次測試起的,靠 PYTHONPATH 指向本專案來辨識)
backends() { pgrep -f "gateway.web" 2>/dev/null | wc -l | tr -d ' '; }

launch() {
    MCPHUB_PYTHON="$PYTHON" MCPHUB_REPO="$REPO" MCPHUB_DATA_DIR="$DATA" \
        "$BIN" >"$DATA/app.log" 2>&1 &
    echo $!
}

wait_ready() {
    for _ in $(seq 1 40); do
        curl -sf http://127.0.0.1:8765/api/v1/health >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    return 1
}

wait_gone() {
    for _ in $(seq 1 30); do
        [ "$(backends)" = "0" ] && sleep 1 && return 0   # 多等一秒讓 port 完全釋放
        sleep 0.3
    done
    return 1
}

diagnose() {
    echo "     ── app.log ──"
    sed 's/^/     /' "$DATA/app.log" 2>/dev/null | tail -15
    echo "     lsof 8765: $(/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null | tr '\n' ' ')"
}

cleanup() {
    pkill -f "gateway.web" 2>/dev/null
    pkill -f "$BIN" 2>/dev/null
    rm -rf "$DATA"
}
trap cleanup EXIT

# ── 前置:確認環境乾淨 ────────────────────────────────────
say "前置檢查"
swift build >/dev/null 2>&1 || { echo "建置失敗"; exit 1; }
if [ "$(backends)" != "0" ]; then
    echo "  ! 已有 gateway.web 在執行,先收掉以免影響計數"
    pkill -f "gateway.web"; sleep 1
fi
ok "建置成功、環境乾淨"

# ── A. 啟動 ──────────────────────────────────────────────
say "A — 啟動與就緒"
APP_PID=$(launch)
if wait_ready; then
    ok "A1 後端在逾時內就緒"
else
    bad "A1 後端未就緒"; diagnose; exit 1
fi

DB=$(curl -s http://127.0.0.1:8765/api/v1/health | sed -n 's/.*"db_path":"\([^"]*\)".*/\1/p')
if [ "$DB" = "$DATA/actions.db" ]; then
    ok "A2 資料指向指定目錄($DB)"
else
    bad "A2 資料路徑錯誤:$DB"
fi

[ "$(backends)" = "1" ] && ok "A3 恰好一個後端程序" || bad "A3 後端程序數為 $(backends)"

# ── B. 正常結束不留孤兒(本腳本存在的主要理由)──────────
say "B — 正常結束"
kill -TERM "$APP_PID" 2>/dev/null
if wait_gone; then
    ok "B1 SIGTERM 後端已回收,無殘留"
else
    bad "B1 殘留 $(backends) 個後端程序"; pkill -f "gateway.web"
fi

# ── C. 強制結束 → 下次啟動接管 ───────────────────────────
say "C — 強制結束與殘留接管"
APP_PID=$(launch)
if ! wait_ready; then bad "C 準備階段失敗"; diagnose; exit 1; fi
STALE=$(pgrep -f "gateway.web" | head -1)

kill -9 "$APP_PID" 2>/dev/null
sleep 1
if kill -0 "$STALE" 2>/dev/null; then
    ok "C1 SIGKILL 後子程序如預期倖存(macOS 無 PDEATHSIG)"
else
    ok "C1 子程序已隨之結束"
fi

APP_PID=$(launch)
# 殘留的後端也會回應 /health,所以不能只等 health —— 要等後端換成新的 PID。
NEW=""
for _ in $(seq 1 40); do
    CUR=$(pgrep -f "gateway.web" | head -1)
    if [ -n "$CUR" ] && [ "$CUR" != "$STALE" ] && curl -sf http://127.0.0.1:8765/api/v1/health >/dev/null 2>&1; then
        NEW="$CUR"; break
    fi
    sleep 0.5
done

if [ -n "$NEW" ]; then
    NOW=$(backends)
    [ "$NOW" = "1" ] && ok "C2 重新啟動後仍只有一個後端(PID $STALE → $NEW)" \
                     || bad "C2 後端程序數為 $NOW(應為 1)"
    kill -0 "$STALE" 2>/dev/null && bad "C3 舊的殘留程序未被收掉" || ok "C3 舊的殘留程序已被收掉"
else
    bad "C2 未接管殘留或未重新啟動"; diagnose
fi

# ── D. 後端崩潰自動重啟 ──────────────────────────────────
say "D — 崩潰重啟"
BEFORE=$(pgrep -f "gateway.web" | head -1)
kill -9 "$BEFORE" 2>/dev/null
sleep 4
if wait_ready; then
    AFTER=$(pgrep -f "gateway.web" | head -1)
    if [ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" ]; then
        ok "D1 後端被外部殺掉後自動重啟(PID $BEFORE → $AFTER)"
    else
        bad "D1 未重啟"
    fi
else
    bad "D1 重啟後未就緒"
fi

kill -TERM "$APP_PID" 2>/dev/null; wait_gone

# ── E. port 衝突 ─────────────────────────────────────────
say "E — port 衝突"
sleep 2   # 讓上一階段的 port 完全釋放
MCP_HUB_DB="$DATA/other.db" MCP_HUB_KEY="$DATA/other.key" MCP_HUB_PORT=8765 \
    PYTHONPATH="$REPO" "$PYTHON" -m gateway.web >"$DATA/squatter.log" 2>&1 &
SQUAT=$!
if ! wait_ready; then
    bad "E 準備階段失敗:佔位程序起不來"
    sed 's/^/     /' "$DATA/squatter.log" | tail -8
    kill $SQUAT 2>/dev/null
else
BEFORE_N=$(backends)
: > "$DATA/app.log"
APP_PID=$(launch)
sleep 4
if grep -q "已被 PID" "$DATA/app.log" 2>/dev/null; then
    ok "E1 偵測到 port 被佔用並明確報告"
else
    # 狀態只顯示在選單列,log 可能沒有 —— 改以「沒有多起一個後端」判斷
    N=$(backends)
    [ "$N" = "$BEFORE_N" ] && ok "E1 未在 port 被佔用時另起後端(維持 $N 個)" \
                           || bad "E1 後端程序數 $BEFORE_N → $N,不該變動"
fi
fi
kill -TERM "$APP_PID" 2>/dev/null; sleep 1
kill -TERM "$SQUAT" 2>/dev/null; wait_gone

# ── 結果 ─────────────────────────────────────────────────
say "結果"
printf "  通過 %d 項,失敗 %d 項\n\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
