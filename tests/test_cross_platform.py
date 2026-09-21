"""
跨平台測試。

**這些測試驗證的是「有沒有選對分支」,不是「Windows 上的實際效果」。**
我在 macOS 上開發,icacls 的真實行為與 Windows 的 ACL 語意無法在此驗證,
那些需要在 Windows 實機上確認 —— CI 的 windows 格子就是為此存在。

但「路徑拼錯」「參數順序反了」「忘記 import」這類錯誤,是擋得掉的 ——
事實上就抓到一個:web.py 原本沒有 import os,而 Windows 分支會用到 os.name。
在 macOS 上永遠不會執行到那行,所以編譯與既有測試都看不出來。
"""
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests._env import DB_PATH as _TMP_DB  # noqa: E402

from gateway import web, crypto, store  # noqa: E402


def setUpModule():
    store.init_db()   # 單獨執行本模組時也要有資料表


class TestClaudeConfigPaths(unittest.TestCase):
    """各平台要找對 Claude Desktop 的設定檔位置。"""

    def paths_on(self, platform, env=None):
        """
        回傳的路徑一律normalize成正斜線。

        這些測試會在三個平台上跑,而 Path 在 Windows 上產生的是反斜線 ——
        直接比對字串的話,同一段程式碼在 Windows 上就會「失敗」,但失敗的是
        斷言的寫法,不是被測的邏輯。
        """
        with mock.patch.object(sys, "platform", platform), \
             mock.patch.dict(os.environ, env or {}, clear=False):
            return [str(p).replace("\\", "/") for p in web._claude_config_paths()]

    def test_macos(self):
        paths = self.paths_on("darwin")
        self.assertTrue(any("Library/Application Support/Claude" in p for p in paths))

    def test_windows_uses_appdata(self):
        paths = self.paths_on("win32", {"APPDATA": r"C:\Users\me\AppData\Roaming"})
        self.assertTrue(any("AppData" in p and "Claude" in p for p in paths),
                        f"Windows 應該查 %APPDATA%\\Claude:{paths}")
        self.assertFalse(any("Library/Application Support" in p for p in paths),
                         "Windows 不該查 macOS 的路徑")

    def test_windows_without_appdata_does_not_crash(self):
        # APPDATA 不存在是不正常但可能發生的,不該讓整個匯入功能爆掉。
        # 只拿掉 APPDATA —— clear=True 會連 USERPROFILE 一起清掉,
        # 那樣 Path.home() 在 Windows 上會直接 RuntimeError,測到的是別的東西。
        env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, env, clear=True):
            paths = [str(p) for p in web._claude_config_paths()]
        self.assertTrue(paths, "至少要留下 ~/.claude.json")

    def test_linux(self):
        paths = self.paths_on("linux")
        self.assertTrue(any(".config/Claude" in p for p in paths))
        self.assertFalse(any("Library/Application Support" in p for p in paths))

    def test_claude_code_config_on_every_platform(self):
        # ~/.claude.json 三個平台都一樣,不該被分支漏掉
        for platform in ("darwin", "win32", "linux"):
            paths = self.paths_on(platform, {"APPDATA": r"C:\x"})
            self.assertTrue(any(p.endswith(".claude.json") for p in paths),
                            f"{platform} 漏了 ~/.claude.json")

    def test_import_claude_survives_missing_files(self):
        # 路徑指到不存在的檔案時要安靜跳過,不是拋例外
        with mock.patch.object(web, "_claude_config_paths",
                               return_value=[Path("/nonexistent/a.json")]):
            resp = web.import_claude()
        self.assertEqual(resp.status_code, 303)


class TestSecretKeyProtection(unittest.TestCase):
    """金鑰檔必須真的只有本人能讀 —— 各平台用的機制不同。"""

    @unittest.skipIf(sys.platform.startswith("win"),
                     "Windows 的 os.chmod 只認 read-only 旗標,沒有 POSIX 權限位元 —— "
                     "那正是 _protect() 改走 ACL 的原因,見 test_windows_calls_icacls")
    def test_posix_sets_0600(self):
        import tempfile
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.remove, path)
        os.chmod(path, 0o644)   # 先弄成寬鬆的

        with mock.patch.object(sys, "platform", "darwin"):
            crypto._protect(Path(path))

        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600, f"權限應為 0600,實際 {oct(mode)}")

    def test_windows_calls_icacls_with_right_arguments(self):
        # Windows 上 os.chmod 只認 read-only 旗標,照抄 chmod 等於沒保護。
        # 這裡驗證有改走 ACL,以及參數沒拼錯。
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"USERNAME": "testuser"}), \
             mock.patch.object(crypto, "subprocess") as sp:
            sp.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            crypto._protect(Path(r"C:\data\.secret_key"))

        sp.run.assert_called_once()
        argv = sp.run.call_args[0][0]
        self.assertEqual(argv[0], "icacls")
        self.assertIn("/inheritance:r", argv, "必須移除繼承的 ACL,否則等於沒限制")
        self.assertIn("/grant:r", argv)
        self.assertIn("testuser:F", argv)

    def test_windows_does_not_use_chmod(self):
        # 防止有人「順手」把 chmod 加回 Windows 路徑
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"USERNAME": "u"}), \
             mock.patch.object(crypto, "subprocess") as sp, \
             mock.patch.object(os, "chmod") as chmod:
            sp.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            crypto._protect(Path(r"C:\x"))
        chmod.assert_not_called()

    def test_icacls_failure_warns_and_does_not_raise(self):
        # 保護失敗不該讓 Hub 起不來,但也絕不能靜默
        import io
        err = io.StringIO()
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"USERNAME": "u"}), \
             mock.patch.object(crypto, "subprocess") as sp, \
             mock.patch.object(sys, "stderr", err):
            sp.run.return_value = mock.Mock(returncode=1, stdout="", stderr="拒絕存取")
            crypto._protect(Path(r"C:\x"))   # 不該拋例外

        self.assertIn("警告", err.getvalue())
        self.assertIn("其他使用者可能讀得到", err.getvalue())

    def test_missing_username_warns(self):
        import io
        err = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k not in ("USERNAME", "USER")}
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(sys, "stderr", err):
            crypto._protect(Path(r"C:\x"))
        self.assertIn("警告", err.getvalue())

    def test_real_secret_key_is_protected_on_this_machine(self):
        # 端對端:測試用的金鑰檔實際上就該是 0600(macOS)
        if sys.platform.startswith("win"):
            self.skipTest("Windows 用 ACL,不檢查 POSIX 權限位元")
        key_path = Path(os.environ["MCP_HUB_KEY"])
        crypto.enc("觸發金鑰建立")   # 確保檔案存在
        self.assertTrue(key_path.exists())
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        self.assertEqual(mode, 0o600, f"金鑰檔權限應為 0600,實際 {oct(mode)}")


class TestConsoleEncoding(unittest.TestCase):
    """
    Windows 主控台的預設編碼(cp950 / cp1252)編不出中文。

    這不是美觀問題:crypto._warn 是在「金鑰保護失敗」時才呼叫的,警告本身
    若拋 UnicodeEncodeError,會一路傳到 _get_fernet() —— 那是每次加解密的
    必經之路,整個 Hub 會崩潰,而且原因看起來跟編碼毫無關係。
    """

    def cp1252_stream(self):
        import io
        return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    def test_plain_print_would_crash(self):
        # 先證明這個情境是真的 —— 否則下面的測試等於沒測到東西
        with self.assertRaises(UnicodeEncodeError):
            print("警告:中文訊息", file=self.cp1252_stream())

    def test_eprint_survives_unencodable_console(self):
        from gateway import console
        stream = self.cp1252_stream()
        with mock.patch.object(sys, "stderr", stream):
            console.eprint("警告:中文訊息")   # 不該拋例外

    def test_warn_survives_unencodable_console(self):
        # 端對端:保護失敗的警告在編不出中文的主控台上也不能崩潰
        stream = self.cp1252_stream()
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"USERNAME": "u"}), \
             mock.patch.object(crypto, "subprocess") as sp, \
             mock.patch.object(sys, "stderr", stream):
            sp.run.return_value = mock.Mock(returncode=1, stdout="", stderr="拒絕存取")
            crypto._protect(Path(r"C:\x"))   # 不該拋例外

    def test_message_is_not_silently_dropped(self):
        # 退路可以讓訊息變難看,但不能讓它消失
        from gateway import console
        import io
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stderr", stream):
            console.eprint("警告:金鑰未受保護")
        self.assertTrue(raw.getvalue(), "訊息不該被默默丟掉")


if __name__ == "__main__":
    unittest.main()
