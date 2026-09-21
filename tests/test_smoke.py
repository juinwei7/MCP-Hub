"""
冒煙測試 —— 用標準庫 unittest + FastAPI TestClient,不需 pytest。

跑法:
    ./.venv/bin/python -m unittest tests.test_smoke -v
或:
    ./.venv/bin/python -m tests.test_smoke

會把 DB 指到臨時檔(MCP_HUB_DB),不會動到正式 actions.db。
覆蓋:store CRUD、解析 helper、每個網頁 GET 200、路由不互相遮蔽。
"""
import json
import unittest

# 必須在 import gateway 之前,臨時 DB / 金鑰的設定集中在這裡(見 tests/_env.py)
from tests._env import DB_PATH as _TMP_DB  # noqa: E402

from gateway import store, web, crypto, skills, auth  # noqa: E402
from mcp.shared.auth import OAuthToken, ProtectedResourceMetadata  # noqa: E402
from gateway.web import (  # noqa: E402
    _entry_to_server, _extract_mcp_servers, _parse_tool_form, _parse_composite_form,
)
from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Route  # noqa: E402


def setUpModule():
    store.init_db()
    # 臨時檔的清除由 tests/_env.py 的 atexit 負責 —— 不能在這裡刪,
    # 那會在別的測試模組還在用同一個 DB 時就把它砍掉。


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

    def test_composite_tool_crud(self):
        store.upsert_composite_tool(
            "weekly_report", "週報", '[{"name":"week"}]',
            '[{"id":"digest","tool":"demo__digest","args":{"week":"{{input.week}}"}}]', "collect", "報表")
        self.assertIsNotNone(store.get_composite_tool("weekly_report"))
        store.set_composite_tool_flag("weekly_report", needs_confirm=True)
        self.assertTrue(store.get_composite_tool("weekly_report")["needs_confirm"])
        store.delete_composite_tool("weekly_report")
        self.assertIsNone(store.get_composite_tool("weekly_report"))

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

    def test_skill_draft(self):
        store.upsert_composite_tool("draft_source", "測試", "[]", '[{"id":"x","tool":"a__b","args":{}}]')
        store.save_skill_draft("draft_source", "---\nname: draft-source\ndescription: test\n---\n", "purpose")
        self.assertEqual(store.get_skill_draft("draft_source")["purpose"], "purpose")
        store.delete_composite_tool("draft_source")
        self.assertIsNone(store.get_skill_draft("draft_source"))


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


class TestOAuth(unittest.IsolatedAsyncioTestCase):
    async def test_public_client_metadata_uses_pkce_without_secret(self):
        metadata = auth._client_metadata()
        self.assertEqual(metadata.token_endpoint_auth_method, "none")
        self.assertEqual(metadata.grant_types, ["authorization_code", "refresh_token"])

    async def test_callback_is_matched_by_state(self):
        first = auth.ConsoleOAuthFlow.start("first")
        second = auth.ConsoleOAuthFlow.start("second")
        await first.redirect_handler("https://auth.example/authorize?state=state-one")
        await second.redirect_handler("https://auth.example/authorize?state=state-two")

        self.assertFalse(await auth.ConsoleOAuthFlow.resolve_callback("wrong", "unknown"))
        self.assertTrue(await auth.ConsoleOAuthFlow.resolve_callback("code-two", "state-two"))
        self.assertEqual(await second.callback_handler(), ("code-two", "state-two"))
        self.assertFalse(first.code.done())

        first.close()
        second.close()

    async def test_authorization_url_requires_state(self):
        flow = auth.ConsoleOAuthFlow.start("missing-state")
        with self.assertRaisesRegex(ValueError, "missing state"):
            await flow.redirect_handler("https://auth.example/authorize")
        flow.close()

    async def test_callback_error_is_forwarded_to_waiting_flow(self):
        flow = auth.ConsoleOAuthFlow.start("denied")
        await flow.redirect_handler("https://auth.example/authorize?state=denied-state")

        self.assertTrue(await auth.ConsoleOAuthFlow.resolve_callback(
            "", "denied-state", "The user denied access"))
        with self.assertRaisesRegex(RuntimeError, "denied access"):
            await flow.callback_handler()

        flow.close()

    async def test_task_group_error_shows_nested_registration_failure(self):
        error = ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError('Registration failed: 400 {"error":"invalid_client_metadata"}')],
        )
        message = web._oauth_error_hint(error)
        self.assertIn("client metadata", message)
        self.assertIn("invalid_client_metadata", message)

    async def test_incompatible_stored_secret_client_is_cleared(self):
        slug = "legacy-oauth-client"
        store.set_oauth_client_info(slug, json.dumps({
            "client_id": "legacy-client",
            "redirect_uris": ["http://localhost:8765/oauth/callback"],
            "token_endpoint_auth_method": "client_secret_post",
        }))
        store.set_oauth_tokens(slug, json.dumps({
            "access_token": "legacy-token",
            "token_type": "Bearer",
        }))

        storage = auth.SqliteTokenStorage(slug)
        self.assertIsNone(await storage.get_tokens())
        self.assertIsNone(await storage.get_client_info())
        self.assertIsNone(store.get_oauth_tokens(slug))

    async def test_token_without_expires_in_clears_stale_expiry(self):
        # 換到一顆沒有 expires_in 的 token 時,舊的到期時間要跟著清掉,
        # 否則時間一過,還有效的 token 會被 is_token_valid() 永遠判成過期。
        slug = "expiring-oauth"
        storage = auth.SqliteTokenStorage(slug)
        await storage.set_tokens(OAuthToken(access_token="first", expires_in=3600))
        self.assertIsNotNone(store.get_oauth_expiry(slug))

        await storage.set_tokens(OAuthToken(access_token="second"))
        self.assertIsNone(store.get_oauth_expiry(slug))
        self.assertIn("second", store.get_oauth_tokens(slug))

    def test_oauth_metadata_roundtrip(self):
        slug = "metadata-oauth"
        store.set_oauth_metadata(slug, '{"issuer": "https://as.example"}', '{"resource": "https://rs.example"}')
        self.assertIn("as.example", store.get_oauth_metadata(slug))
        self.assertIn("rs.example", store.get_oauth_resource_metadata(slug))
        # 沒探查到 PRM 時傳 None → 清掉,不會留著上一次的
        store.set_oauth_metadata(slug, '{"issuer": "https://as2.example"}')
        self.assertIsNone(store.get_oauth_resource_metadata(slug))

    def test_prm_resource_must_cover_server_url(self):
        prm = ProtectedResourceMetadata(
            resource="https://api.example.com/mcp",
            authorization_servers=["https://as.example.com"],
        )
        self.assertTrue(auth._resource_matches(prm, "https://api.example.com/mcp"))
        self.assertFalse(auth._resource_matches(prm, "https://evil.example.com/mcp"))


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

    def test_parse_composite_form(self):
        p, s = _parse_composite_form(
            '[{"name":"week","type":"string"}]',
            '[{"id":"digest","tool":"orgpulse__get_my_weekly_digest","args":{"week":"{{input.week}}"}}]',
            "collect",
        )
        self.assertIn("week", p)
        self.assertIn("digest", s)
        with self.assertRaises(ValueError):
            _parse_composite_form("{}", "[]", "collect")
        with self.assertRaises(ValueError):
            _parse_composite_form("[]", '[{"tool":"x"}]', "collect")

    def test_skill_package(self):
        import io
        import zipfile
        tool = {
            "name": "weekly_report",
            "description": "產生週報",
            "params": [{"name": "week", "type": "string", "required": False, "description": "週次"}],
        }
        markdown = skills.starter_skill(tool)
        self.assertIn("name: weekly-report", markdown)
        skills.validate_skill_markdown(markdown, "weekly-report")
        with zipfile.ZipFile(io.BytesIO(skills.skill_zip(tool["name"], markdown))) as archive:
            self.assertEqual(archive.namelist(), ["weekly-report/SKILL.md"])
            self.assertEqual(archive.read("weekly-report/SKILL.md").decode(), markdown)


class TestSkillWorkbench(unittest.IsolatedAsyncioTestCase):
    async def test_inspect_validate_and_save(self):
        store.upsert_composite_tool(
            "tune_report", "整理報告", "[]",
            '[{"id":"data","tool":"demo__read","args":{}}]', "collect", "測試")

        async def no_run(_name, _arguments):
            return []

        inspected = await skills.execute_workbench(
            {"action": "inspect", "composite_name": "tune_report"}, no_run)
        self.assertIn('"required_skill_name": "tune-report"', inspected)
        markdown = "---\nname: tune-report\ndescription: 使用者需要整理報告時使用\n---\n\n# Workflow\n"
        validated = await skills.execute_workbench(
            {"action": "validate", "composite_name": "tune_report", "skill_md": markdown}, no_run)
        self.assertIn('"ok": true', validated)
        saved = await skills.execute_workbench(
            {"action": "save", "composite_name": "tune_report", "skill_md": markdown}, no_run)
        self.assertIn('"ok": true', saved)
        self.assertEqual(store.get_skill_draft("tune_report")["skill_md"], markdown)
        store.delete_composite_tool("tune_report")


class TestWebPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(web.app)
        cls.client.__enter__()
        store.add_server("pagesrv", "Page Srv", "http://127.0.0.1:1/mcp", "none")
        store.cache_tools("pagesrv", [("t1", "工具一", {"type": "object"})])
        store.create_category("頁面類")
        store.upsert_composite_tool(
            "page_report", "頁面測試複合工具", "[]",
            '[{"id":"digest","tool":"pagesrv__t1","args":{}}]', "collect", "測試")

    @classmethod
    def tearDownClass(cls):
        store.delete_server("pagesrv")
        store.delete_category("頁面類")
        store.delete_composite_tool("page_report")
        cls.client.__exit__(None, None, None)

    def test_pages_return_200(self):
        for path in ["/", "/servers/add", "/tools", "/tools/add", "/composites", "/composites/add",
                     "/composites/page_report", "/composites/page_report/skill", "/import",
                     "/directory", "/registry", "/connect", "/logs", "/guide",
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
