"""
冒煙測試 —— 用標準庫 unittest + FastAPI TestClient,不需 pytest。

跑法:
    ./.venv/bin/python -m unittest tests.test_smoke -v
或:
    ./.venv/bin/python -m tests.test_smoke

會把 DB 指到臨時檔(MCP_HUB_DB),不會動到正式 actions.db。
覆蓋:store CRUD、解析 helper、每個網頁 GET 200、路由不互相遮蔽。
"""
import os
import tempfile
import unittest

# 必須在 import gateway 之前設好,才會用臨時 DB + 臨時金鑰
_TMP_DB = tempfile.mkstemp(prefix="mcphub_test_", suffix=".db")[1]
_TMP_KEY = tempfile.mkstemp(prefix="mcphub_key_", suffix="")[1]
os.remove(_TMP_KEY)   # 讓 crypto 自己產生
os.environ["MCP_HUB_DB"] = _TMP_DB
os.environ["MCP_HUB_KEY"] = _TMP_KEY

from gateway import store, web, crypto  # noqa: E402
from gateway.web import (  # noqa: E402
    _entry_to_server, _extract_mcp_servers, _parse_tool_form,
)
from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Route  # noqa: E402


def setUpModule():
    store.init_db()


def tearDownModule():
    for f in (_TMP_DB, _TMP_KEY):
        try:
            os.remove(f)
        except OSError:
            pass


class TestStore(unittest.TestCase):
    def test_server_crud(self):
        store.add_server("smoke1", "Smoke One", "http://127.0.0.1:1/mcp", "none")
        slugs = {s["slug"] for s in store.list_servers()}
        self.assertIn("smoke1", slugs)
        self.assertIn("smoke1", {s["slug"] for s in store.enabled_servers()})
        store.set_server_enabled("smoke1", False)
        self.assertNotIn("smoke1", {s["slug"] for s in store.enabled_servers()})
        store.delete_server("smoke1")
        self.assertNotIn("smoke1", {s["slug"] for s in store.list_servers()})

    def test_tools_cache_and_desc_override(self):
        store.add_server("smoke2", "Smoke Two", "http://127.0.0.1:1/mcp", "none")
        store.cache_tools("smoke2", [("do_thing", "原始描述", {"type": "object"})])
        tools = store.list_cached_tools("smoke2")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["description"], "原始描述")
        # 描述覆寫來回
        store.set_tool_desc("smoke2", "do_thing", "策展後的描述")
        self.assertEqual(store.get_tool("smoke2", "do_thing")["desc_override"], "策展後的描述")
        store.set_tool_desc("smoke2", "do_thing", "")
        self.assertEqual(store.get_tool("smoke2", "do_thing")["desc_override"], "")
        # enable / confirm 旗標
        store.set_tool_flag("smoke2", "do_thing", needs_confirm=True)
        self.assertTrue(store.get_tool("smoke2", "do_thing")["needs_confirm"])
        store.delete_server("smoke2")

    def test_custom_tool_crud(self):
        store.upsert_custom_tool("my_api", "打某 API", "GET",
                                 "https://example.com/{id}", "{}", "[]", "測試群")
        self.assertIsNotNone(store.get_custom_tool("my_api"))
        store.delete_custom_tool("my_api")
        self.assertIsNone(store.get_custom_tool("my_api"))

    def test_logs(self):
        before = len(store.list_logs(500))
        store.log_call("smoke", "some_tool", {"a": 1}, "ok", duration_ms=12)
        after = store.list_logs(500)
        self.assertEqual(len(after), before + 1)
        self.assertEqual(after[0]["tool"], "some_tool")

    def test_category(self):
        store.create_category("測試類")
        named, _uncat = store.category_overview()   # 回傳 (具名分類, 未分類)
        self.assertIn("測試類", [c["name"] for c in named])
        store.delete_category("測試類")


class TestCrypto(unittest.TestCase):
    def test_roundtrip(self):
        for s in ["SECRET-123", '{"API_KEY":"x"}', "中文密鑰"]:
            token = crypto.enc(s)
            self.assertTrue(crypto.is_encrypted(token), token)
            self.assertNotIn(s, token)          # 密文不含明文
            self.assertEqual(crypto.dec(token), s)

    def test_transparent_and_empty(self):
        self.assertEqual(crypto.enc(""), "")       # 空值不加密
        self.assertEqual(crypto.enc("{}"), "{}")   # 空容器不加密
        self.assertEqual(crypto.enc("[]"), "[]")
        self.assertEqual(crypto.dec("明文舊值"), "明文舊值")  # 舊明文原樣讀回
        # 不重複加密
        once = crypto.enc("x")
        self.assertEqual(crypto.enc(once), once)

    def test_stored_ciphertext_at_rest(self):
        import sqlite3
        store.add_server("encsrv", "Enc", "https://x/mcp", "bearer",
                         bearer_token="TOP-SECRET-XYZ")
        raw = sqlite3.connect(_TMP_DB)
        val = raw.execute("SELECT bearer_token FROM servers WHERE slug='encsrv'").fetchone()[0]
        raw.close()
        self.assertNotIn("TOP-SECRET-XYZ", val)          # DB 裡不是明文
        self.assertTrue(crypto.is_encrypted(val))
        self.assertEqual(store.get_server("encsrv")["bearer_token"], "TOP-SECRET-XYZ")  # 讀回明文
        store.delete_server("encsrv")


class TestParsers(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(store.slugify("Hello World!"), "hello_world")
        self.assertEqual(store.slugify("   "), "server")  # 空 → 預設

    def test_extract_mcp_servers(self):
        self.assertEqual(
            _extract_mcp_servers({"mcpServers": {"a": {"url": "x"}}}), {"a": {"url": "x"}})
        # 裸單一條目 → 包成 {"server": ...}
        self.assertIn("server", _extract_mcp_servers({"command": "npx"}))
        self.assertEqual(_extract_mcp_servers("not a dict"), {})

    def test_entry_to_server(self):
        stdio = _entry_to_server("t", {"command": "npx", "args": ["-y", "pkg"]})
        self.assertEqual(stdio["transport"], "stdio")
        self.assertEqual(stdio["command"], "npx")

        http = _entry_to_server("t", {"url": "https://x/mcp",
                                      "headers": {"Authorization": "Bearer SECRET"}})
        self.assertEqual(http["transport"], "http")
        self.assertEqual(http["auth_type"], "bearer")
        self.assertEqual(http["bearer_token"], "SECRET")

        # 指向本 Hub 自己 → 跳過(避免迴圈)
        self.assertIsNone(_entry_to_server("self", {"command": "python",
                                                    "args": ["-m", "gateway.hub_server"]}))
        self.assertIsNone(_entry_to_server("x", {"nonsense": 1}))

    def test_parse_tool_form(self):
        h, p = _parse_tool_form("n", "d", "GET", "http://x",
                                '{"X-Key":"1"}', '[{"name":"id"}]')
        self.assertIn("X-Key", h)
        self.assertIn("id", p)
        with self.assertRaises(ValueError):
            _parse_tool_form("n", "d", "GET", "http://x", "不是JSON", "[]")
        with self.assertRaises(ValueError):
            _parse_tool_form("n", "d", "GET", "http://x", "{}", '[{"noname":1}]')


class TestWebPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(web.app)
        cls.client.__enter__()
        store.add_server("pagesrv", "Page Srv", "http://127.0.0.1:1/mcp", "none")
        store.cache_tools("pagesrv", [("t1", "工具一", {"type": "object"})])
        store.create_category("頁面類")

    @classmethod
    def tearDownClass(cls):
        store.delete_server("pagesrv")
        store.delete_category("頁面類")
        cls.client.__exit__(None, None, None)

    def test_pages_return_200(self):
        for path in ["/", "/servers/add", "/tools", "/tools/add", "/import",
                     "/directory", "/catalog", "/connect", "/logs",
                     "/servers/pagesrv", "/category?name=" + "頁面類"]:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code}")

    def test_config_export(self):
        r = self.client.get("/config/export")
        self.assertEqual(r.status_code, 200)


class TestRouteHygiene(unittest.TestCase):
    """防呆:同一 method 下,參數路由不可排在字面路由之前(否則字面路由被吃掉)。"""

    def test_no_param_route_shadows_literal(self):
        routes = [(r.path, set(r.methods or [])) for r in web.app.routes
                  if isinstance(r, Route)]

        def segs(p):
            return [s for s in p.strip("/").split("/") if s]

        def is_param(s):
            return s.startswith("{")

        shadows = []
        for i, (pi, mi) in enumerate(routes):
            si = segs(pi)
            if not any(is_param(s) for s in si):
                continue
            for j, (pj, mj) in enumerate(routes):
                if j <= i:
                    continue
                sj = segs(pj)
                common = mi & mj
                if len(si) != len(sj) or not common or pi == pj:
                    continue
                if all(is_param(s) for s in sj):
                    continue
                if all(is_param(a) or a == b for a, b in zip(si, sj)):
                    shadows.append(f"{pi} 遮蔽 {pj} {sorted(common)}")
        self.assertEqual(shadows, [], "發現參數路由遮蔽字面路由:\n" + "\n".join(shadows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
