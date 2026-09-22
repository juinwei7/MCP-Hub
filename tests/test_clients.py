"""
把 Hub 寫進別家 client 設定檔的測試。

這個模組動的是別人的檔案 —— 我們有 bug 的話,壞掉的是使用者的工作環境。
所以測的重點不是「有沒有寫進去」,而是「有沒有動到不該動的東西」。

跑法:./.venv/bin/python -m unittest tests.test_clients -v
"""
import json
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._env import DB_PATH as _TMP_DB  # noqa: F401  (先設好環境變數再 import gateway)

from gateway import clients  # noqa: E402


class WriteJson(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "claude_desktop_config.json"

    def spec(self):
        return clients.launch_spec()

    def test_creates_file_when_missing(self):
        clients._write_json(self.path, self.spec())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn(clients.SERVER_NAME, data["mcpServers"])

    def test_keeps_unrelated_top_level_keys(self):
        # 使用者的其他設定不該因為我們寫入而消失
        self.path.write_text(json.dumps({
            "globalShortcut": "Alt+Space",
            "theme": "dark",
            "mcpServers": {"their-server": {"command": "npx"}},
        }), encoding="utf-8")

        clients._write_json(self.path, self.spec())
        data = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(data["globalShortcut"], "Alt+Space")
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcpServers"]["their-server"], {"command": "npx"})
        self.assertIn(clients.SERVER_NAME, data["mcpServers"])

    def test_uninstall_removes_only_ours(self):
        self.path.write_text(json.dumps({
            "mcpServers": {"their-server": {"command": "npx"}},
        }), encoding="utf-8")
        clients._write_json(self.path, self.spec())
        clients._write_json(self.path, None)

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(clients.SERVER_NAME, data["mcpServers"])
        self.assertIn("their-server", data["mcpServers"])

    def test_refuses_to_clobber_broken_json(self):
        # 看不懂的檔案就不要動 —— 裡面可能有使用者花時間設定的東西
        broken = '{"mcpServers": {'
        self.path.write_text(broken, encoding="utf-8")
        with self.assertRaises(clients.ClientError):
            clients._write_json(self.path, self.spec())
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_backs_up_before_writing(self):
        self.path.write_text('{"theme": "dark"}', encoding="utf-8")
        clients._write_json(self.path, self.spec())
        backup = self.path.with_suffix(self.path.suffix + ".mcphub-backup")
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"theme": "dark"})

    def test_carries_the_three_environment_variables(self):
        # 少任何一個,聚合器都會去讀另一份資料庫 —— 而且不會報錯
        clients._write_json(self.path, self.spec())
        env = json.loads(self.path.read_text(encoding="utf-8"))["mcpServers"][
            clients.SERVER_NAME]["env"]
        self.assertIn("PYTHONPATH", env)
        self.assertIn("MCP_HUB_DB", env)
        self.assertIn("MCP_HUB_KEY", env)


class WriteToml(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "config.toml"

    def test_keeps_comments_and_other_sections(self):
        # Codex 的 config.toml 是人手寫的。整份讀成 dict 再寫回去會把註解
        # 和欄位順序全部弄丟 —— 所以才用文字層級的增修
        original = (
            "# 我的 Codex 設定\n"
            'model = "o3"\n'
            "\n"
            "[mcp_servers.their-server]\n"
            'command = "npx"\n'
        )
        self.path.write_text(original, encoding="utf-8")
        clients._write_toml(self.path, clients.launch_spec())
        text = self.path.read_text(encoding="utf-8")

        self.assertIn("# 我的 Codex 設定", text)
        self.assertIn('model = "o3"', text)
        self.assertIn("[mcp_servers.their-server]", text)

        data = tomllib.loads(text)
        self.assertIn(clients.SERVER_NAME, data["mcp_servers"])
        self.assertIn("their-server", data["mcp_servers"])

    def test_reinstall_does_not_duplicate(self):
        for _ in range(3):
            clients._write_toml(self.path, clients.launch_spec())
        text = self.path.read_text(encoding="utf-8")
        header = f"[mcp_servers.{clients.SERVER_NAME}]"
        self.assertEqual(text.count(header), 1)
        tomllib.loads(text)   # 仍是合法 TOML

    def test_uninstall_removes_only_ours(self):
        self.path.write_text(
            "[mcp_servers.their-server]\ncommand = \"npx\"\n", encoding="utf-8")
        clients._write_toml(self.path, clients.launch_spec())
        clients._write_toml(self.path, None)

        data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(clients.SERVER_NAME, data.get("mcp_servers", {}))
        self.assertIn("their-server", data["mcp_servers"])

    def test_uninstall_leaves_no_orphan_comments(self):
        # 只檢查解析後的 dict 會漏掉這個:註解在區段前面,解析時看不見,
        # 但它會留在檔案裡,而且每次安裝都多兩行
        original = 'model = "o3"\n'
        self.path.write_text(original, encoding="utf-8")
        for _ in range(3):
            clients._write_toml(self.path, clients.launch_spec())
            clients._write_toml(self.path, None)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(clients.MARKER, text)
        self.assertEqual(text.strip(), original.strip())

    def test_keeps_user_comments_next_to_our_section(self):
        # 只清自己標記過的註解。使用者寫在旁邊的註解不能碰
        self.path.write_text('# 使用者自己的註解\nmodel = "o3"\n', encoding="utf-8")
        clients._write_toml(self.path, clients.launch_spec())
        clients._write_toml(self.path, None)
        self.assertIn("# 使用者自己的註解",
                      self.path.read_text(encoding="utf-8"))

    def test_escapes_backslashes(self):
        # Windows 路徑全是反斜線。沒跳脫的話產出的 TOML 直接是壞的
        self.assertEqual(clients._toml_str(r"C:\Users\me\app.exe"),
                         '"C:\\\\Users\\\\me\\\\app.exe"')
        parsed = tomllib.loads("p = " + clients._toml_str(r"C:\Users\me"))
        self.assertEqual(parsed["p"], r"C:\Users\me")

    def test_refuses_to_clobber_broken_toml(self):
        broken = "[mcp_servers.oops\n"
        self.path.write_text(broken, encoding="utf-8")
        with self.assertRaises(clients.ClientError):
            clients._write_toml(self.path, clients.launch_spec())
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)


class Status(unittest.TestCase):
    def test_reports_every_known_client(self):
        ids = {c["id"] for c in clients.status()}
        self.assertEqual(ids, set(clients.CLIENTS))

    def test_never_writes_anything(self):
        # status() 是純查詢。不小心在裡面寫檔的話,只是打開 app 就會改別人的設定
        before = {c["path"]: Path(c["path"]).exists() for c in clients.status() if c["path"]}
        clients.status()
        after = {c["path"]: Path(c["path"]).exists() for c in clients.status() if c["path"]}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
