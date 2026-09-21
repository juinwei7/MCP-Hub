"""
靜態加密 —— 保護存在 SQLite 裡的密鑰(bearer token、env、headers、OAuth token)。

威脅模型:有人讀到 actions.db 檔(備份、雲端同步、誤 commit)不該看到明文密鑰。
金鑰本身存在獨立檔案 SECRET_KEY_PATH(0600 權限,不進版控),不在 DB 裡。
用 cryptography 的 Fernet(AES-128-CBC + HMAC 驗證),已隨 mcp 依賴安裝,不需另裝。

儲存格式:密文加前綴 "enc:v1:",讓 dec() 能分辨「已加密」vs「舊的明文」,
達成透明遷移 —— 舊明文照樣讀得到,新寫入自動加密。
空值 / 空 JSON 容器({}、[])不含密鑰,保持明文以利辨識、減少雜訊。
"""
import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from gateway import console
from gateway.config import SECRET_KEY_PATH

_PREFIX = "enc:v1:"
_SKIP = ("", "{}", "[]")   # 不含密鑰,不加密
_fernet = None


def _protect(path):
    """
    把金鑰檔限制成只有本人能讀。

    Windows 上 os.chmod 只認得 read-only 旗標,POSIX 的權限位元會被忽略 ——
    照抄 chmod(0o600) 等於沒保護。要真的限制存取得改 ACL。

    用內建的 icacls 而不是 pywin32:後者得處理 SID、DACL、security descriptor,
    出錯的方式更多,而這段在 macOS 上無法實測 —— 選更難寫錯的那個。
    """
    if sys.platform.startswith("win"):
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not user:
            _warn(path, "找不到目前的使用者名稱")
            return
        try:
            r = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                _warn(path, (r.stderr or r.stdout).strip()[:200])
        except Exception as e:
            _warn(path, str(e)[:200])
    else:
        os.chmod(path, 0o600)


def _warn(path, reason):
    """
    保護失敗不該靜默 —— 那會讓使用者以為金鑰是安全的。但也不該讓 Hub 起不來。

    用 console.eprint 而不是 print:Windows 主控台的預設編碼編不出中文,
    直接 print 會拋 UnicodeEncodeError,而這個函式正好是在出事時才被呼叫的 ——
    警告本身變成崩潰,原因還看起來跟編碼無關。
    """
    console.eprint(f"警告:無法限制金鑰檔 {path} 的存取權限({reason})。"
                   f"同一台機器的其他使用者可能讀得到它,進而解開 DB 內的所有 token。")


def _get_fernet():
    global _fernet
    if _fernet is None:
        p = Path(SECRET_KEY_PATH)
        if p.exists():
            key = p.read_bytes()
        else:
            key = Fernet.generate_key()
            p.write_bytes(key)
            _protect(p)
        _fernet = Fernet(key)
    return _fernet


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(_PREFIX)


def enc(plaintext):
    """明文 → "enc:v1:<密文>"。空值 / 空容器 / 已加密者原樣回傳。"""
    if not plaintext or plaintext in _SKIP:
        return plaintext or ""
    if is_encrypted(plaintext):
        return plaintext   # 已加密,別重複加
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def dec(stored):
    """"enc:v1:<密文>" → 明文;非加密格式(舊明文 / 空值)原樣回傳。"""
    if not is_encrypted(stored):
        return stored or ""
    try:
        return _get_fernet().decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return ""   # 金鑰不符 / 密文損毀 → 當作空,不要炸掉整台 Hub
