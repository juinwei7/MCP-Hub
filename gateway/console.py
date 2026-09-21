"""
往主控台輸出訊息 —— 不會因為編碼而崩潰。

Windows 的 Python 預設用系統的 ANSI 編碼(繁中環境是 cp950,英文環境是 cp1252),
兩者都編不出我們訊息裡的中文。直接 print 會拋 UnicodeEncodeError。

這不是美觀問題:crypto._warn() 是在「金鑰保護失敗」時呼叫的,如果那個警告
自己又拋例外,例外會往上傳到 _get_fernet(),而那是每次加解密的必經之路 ——
整個 Hub 會在 Windows 上崩潰,而且原因看起來跟編碼毫無關係。

(這是 CI 的 Windows runner 抓到的;在 macOS 上永遠不會發生。)
"""
import sys


def eprint(*parts):
    """把訊息寫到 stderr。編碼不支援時退而求其次,絕不讓輸出本身變成錯誤來源。"""
    _write(sys.stderr, " ".join(str(p) for p in parts))


def use_utf8():
    """
    讓這個行程的 stdout / stderr 改用 UTF-8。

    給「自己擁有主控台」的進入點用(gateway.web、獨立腳本)。
    stdio 型的 MCP server 也適用 —— MCP 的 JSON-RPC 本來就規定 UTF-8。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass   # 串流被接管(測試、管線)時就算了,_write 還有後備


def _write(stream, text):
    try:
        print(text, file=stream, flush=True)
        return
    except UnicodeEncodeError:
        pass
    # 主控台編碼編不出來 —— 直接寫位元組,不可表示的字元用替代符號
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(text.encode("utf-8", errors="replace") + b"\n")
            buffer.flush()
            return
        except Exception:
            pass
    # 最後手段:把字元換成 ASCII 可表示的形式,訊息會難看但不會消失
    try:
        print(text.encode("ascii", errors="backslashreplace").decode("ascii"),
              file=stream, flush=True)
    except Exception:
        pass   # 真的寫不出去就算了,絕不因為「印訊息」而讓程式掛掉
