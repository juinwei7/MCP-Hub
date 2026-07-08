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
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from gateway.config import SECRET_KEY_PATH

_PREFIX = "enc:v1:"
_SKIP = ("", "{}", "[]")   # 不含密鑰,不加密
_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None:
        p = Path(SECRET_KEY_PATH)
        if p.exists():
            key = p.read_bytes()
        else:
            key = Fernet.generate_key()
            p.write_bytes(key)
            os.chmod(p, 0o600)   # 僅本人可讀寫
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
