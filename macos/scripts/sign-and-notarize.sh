#!/usr/bin/env bash
# 以 Developer ID 簽章並向 Apple 公證。
#
# 為什麼需要:
#   1. 對方下載後不會看到「無法驗證開發者」而打不開
#   2. 系統通知與開機自動啟動都要求穩定可驗證的簽章身分 ——
#      ad-hoc 簽章沒有 Team ID,macOS 一律拒絕
#
# 需要 Apple Developer 帳號(年費 US$99)。沒有憑證時這個腳本會明確說出缺什麼,
# 而不是失敗得莫名其妙。
#
# 跑法:
#   NOTARY_PROFILE=<keychain 設定檔名> scripts/sign-and-notarize.sh "build/MCP Hub.app"
#
# 第一次使用前要先建立公證憑證(只需一次):
#   xcrun notarytool store-credentials <設定檔名> \
#       --apple-id <你的 Apple ID> --team-id <TEAM_ID> --password <app 專用密碼>

set -uo pipefail
cd "$(dirname "$0")/.."

APP="${1:-build/MCP Hub.app}"
ENTITLEMENTS="Resources/MCPHub.entitlements"

say()  { printf "\033[1m→ %s\033[0m\n" "$*"; }
ok()   { printf "\033[32m✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }
note() { printf "\033[33m  %s\033[0m\n" "$*"; }

[ -d "$APP" ] || die "找不到 $APP。先跑:make app && scripts/bundle-backend.sh"

# ── 前置檢查:缺什麼要講清楚 ─────────────────────────────
say "檢查簽章憑證"
IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep "Developer ID Application" | head -1 | sed 's/.*"\(.*\)"/\1/')

if [ -z "$IDENTITY" ]; then
    printf "\033[31m✗ 找不到 Developer ID Application 憑證\033[0m\n\n" >&2
    note "這需要 Apple Developer 帳號(年費 US\$99)。取得步驟:"
    note "  1. 到 developer.apple.com 加入 Developer Program"
    note "  2. Certificates → 建立 Developer ID Application 憑證"
    note "  3. 下載並雙擊安裝到鑰匙圈"
    note ""
    note "目前可用的簽章身分:"
    security find-identity -v -p codesigning 2>/dev/null | sed 's/^/    /' >&2 || note "    (一個都沒有)"
    note ""
    note "沒有憑證時 app 仍可在本機使用(make app 會做 ad-hoc 簽章),"
    note "但系統通知與開機自動啟動無法運作,給別人時對方也要手動放行。"
    exit 1
fi
ok "使用憑證:$IDENTITY"

if [ -z "${NOTARY_PROFILE:-}" ]; then
    note "未設定 NOTARY_PROFILE —— 只簽章,不公證。"
    note "公證憑證建立方式見本腳本開頭的註解。"
fi

# ── entitlements ─────────────────────────────────────────
# 內附的 Python 會做 JIT 之類的事,hardened runtime 預設會擋。
# 這是打包內嵌直譯器最常卡住的地方。
if [ ! -f "$ENTITLEMENTS" ]; then
    say "建立 entitlements"
    mkdir -p "$(dirname "$ENTITLEMENTS")"
    cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- 內附的 Python 需要這些,否則 hardened runtime 會擋掉直譯器 -->
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <!-- 後端要連 127.0.0.1,以及下游的 MCP server -->
    <key>com.apple.security.network.client</key>
    <true/>
    <key>com.apple.security.network.server</key>
    <true/>
</dict>
</plist>
PLIST
fi

# ── 簽章 ─────────────────────────────────────────────────
# 由內而外:巢狀的二進位檔要先簽,最後才簽 bundle 本身,否則外層簽章會失效。
say "簽章內附的二進位檔(由內而外)"
COUNT=0
while IFS= read -r f; do
    codesign --force --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$f" 2>/dev/null && COUNT=$((COUNT+1))
done < <(find "$APP/Contents/Resources" \( -name "*.dylib" -o -name "*.so" -o -perm +111 -type f \) 2>/dev/null)
ok "已簽 $COUNT 個內部檔案"

say "簽章 app bundle"
codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$APP" \
    || die "簽章失敗"

say "驗證簽章"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | sed 's/^/  /' \
    || die "簽章驗證失敗"
ok "簽章有效"

# ── 公證 ─────────────────────────────────────────────────
if [ -n "${NOTARY_PROFILE:-}" ]; then
    ZIP="${APP%.app}.zip"
    say "打包送公證"
    rm -f "$ZIP"
    ditto -c -k --keepParent "$APP" "$ZIP"

    say "送交 Apple 公證(通常數分鐘)"
    xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait \
        || die "公證失敗。用這個指令看原因:xcrun notarytool log <submission-id> --keychain-profile $NOTARY_PROFILE"

    say "把公證結果釘進 app"
    # 釘上去之後,對方即使離線也能通過 Gatekeeper 驗證
    xcrun stapler staple "$APP" || die "stapler 失敗"
    rm -f "$ZIP"
    ok "公證完成"

    say "最終驗證(模擬對方第一次開啟)"
    spctl --assess --type execute --verbose "$APP" 2>&1 | sed 's/^/  /'
fi

printf "\n\033[32m✓ %s 已簽章%s\033[0m\n" \
    "$(basename "$APP")" "$([ -n "${NOTARY_PROFILE:-}" ] && echo '並公證' || echo '(未公證)')"
