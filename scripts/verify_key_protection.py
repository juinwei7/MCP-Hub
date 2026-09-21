#!/usr/bin/env python3
"""
實機驗證金鑰檔的存取權限。

單元測試只能驗證「選對分支、參數沒拼錯」,擋不掉「icacls 的語意不如預期」
這類問題。這個腳本實際建一把金鑰,然後**問作業系統**它到底開放給誰。

跑法:python scripts/verify_key_protection.py
離開碼 0 代表金鑰檔確實只有本人能讀。

刻意不放進 unittest:它依賴真實檔案系統與外部指令,在容器或受限的檔案系統上
可能偽陽性。獨立成腳本由 CI 明確呼叫,失敗訊息也更清楚。
"""
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# 這些主體出現在 ACL 裡就代表保護失敗 —— 金鑰檔不該開放給群組或所有人。
# 一律小寫比對,且只比對「主體名稱」而非整段輸出(見 acl_principals 的說明)。
BROAD_PRINCIPALS = {
    "everyone", "builtin\\users", "users",
    "nt authority\\authenticated users", "authenticated users",
    "nt authority\\interactive", "interactive",
    "builtin\\guests", "guests",
}

# 這兩個在 Windows 上移不掉也不該移:SYSTEM 本來就能存取一切,而管理員隨時可以
# 取得任何檔案的擁有權。把它們排除只會製造鎖死自己的風險,換不到實際的保護。
EXPECTED_PRINCIPALS = {"nt authority\\system", "builtin\\administrators"}


def acl_principals(output, path):
    r"""
    從 icacls 的輸出裡取出主體名稱。

    不能直接在整段輸出裡找關鍵字:第一行含有檔案路徑,而路徑通常是
    C:\Users\... —— 用 "Users" 比對會配到路徑,把正常的 ACL 誤判成外洩。

    也不能用空白切開取最後一段:主體名稱本身可能含空白
    (NT AUTHORITY\SYSTEM、NT AUTHORITY\Authenticated Users)。

    所以把已知的檔案路徑剝掉,剩下的才是主體 —— 不用猜。
    格式是 `<主體>:(<權限>)`,每個 ACE 一行,只有第一行前面多一段路徑。
    """
    prefix = str(path)
    principals = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or ":(" not in line:
            continue
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
        principals.append(line.rsplit(":(", 1)[0].strip())
    return principals


def fail(msg):
    print(f"\033[31m✗ {msg}\033[0m", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"\033[32m✓ {msg}\033[0m")


def check_posix(path):
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode != 0o600:
        fail(f"權限應為 0600,實際是 {oct(mode)} —— 其他使用者可能讀得到金鑰")
    ok(f"權限是 {oct(mode)},只有本人可讀寫")


def check_windows(path):
    r = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        fail(f"icacls 查詢失敗:{(r.stderr or r.stdout).strip()}")

    print("  icacls 回報:")
    for line in r.stdout.splitlines():
        if line.strip():
            print(f"    {line}")

    principals = acl_principals(r.stdout, path)
    print(f"  解析出的主體:{principals}")

    leaked = [p for p in principals if p.lower() in BROAD_PRINCIPALS]
    if leaked:
        fail(f"ACL 開放給廣泛主體:{', '.join(leaked)} —— 其他使用者讀得到金鑰")

    user = os.environ.get("USERNAME", "").lower()
    if user and not any(user in p.lower() for p in principals):
        fail(f"ACL 裡找不到目前使用者 {user} —— 可能連自己都讀不到了")

    unexpected = [p for p in principals
                  if p.lower() not in EXPECTED_PRINCIPALS and user not in p.lower()]
    if unexpected:
        print(f"  註:另有 {unexpected} —— 非廣泛主體,但值得留意")

    ok("ACL 只授權給本人(以及 SYSTEM / Administrators),未開放給廣泛主體")


def main():
    # 用臨時目錄,不碰任何正式資料
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "actions.db"
        key = Path(tmp) / ".secret_key"
        os.environ["MCP_HUB_DB"] = str(db)
        os.environ["MCP_HUB_KEY"] = str(key)

        # 一定要在設好環境變數之後才 import —— config 是在 import 時讀取的
        from gateway import crypto

        token = crypto.enc("觸發金鑰建立")
        if not crypto.is_encrypted(token):
            fail("加密沒有生效,金鑰可能沒被建立")
        if not key.exists():
            fail(f"金鑰檔沒有被建立:{key}")

        print(f"平台:{sys.platform}")
        print(f"金鑰檔:{key}")

        if sys.platform.startswith("win"):
            check_windows(key)
        else:
            check_posix(key)

        # 確認金鑰真的能用來解密(保護太嚴格導致自己讀不到也是失敗)
        if crypto.dec(token) != "觸發金鑰建立":
            fail("金鑰檔設了權限之後,自己反而解不開 —— 保護過頭")
        ok("設定權限後仍可正常讀寫金鑰")

    print("\n金鑰保護驗證通過。")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gateway import console
    console.use_utf8()   # Windows 主控台預設編碼編不出中文
    main()
