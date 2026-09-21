"""
JSON API 測試。

跑法:./.venv/bin/python -m unittest tests.test_api -v

用臨時 DB / 金鑰,不碰正式 actions.db。
每個端點至少兩個案例:成功路徑,以及最有意義的失敗路徑。
"""
import json
import unittest

# 必須在 import gateway 之前,臨時 DB / 金鑰 / token 的設定集中在這裡(見 tests/_env.py)
from tests._env import AUTH, DB_PATH as _TMP_DB  # noqa: E402

from gateway import store, web, api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def setUpModule():
    store.init_db()
    # 臨時檔的清除由 tests/_env.py 的 atexit 負責。


class ApiTest(unittest.TestCase):
    """共用:每個測試自己建需要的資料,用獨一無二的 slug 避免互相干擾。"""

    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(web.app)

    def mkserver(self, slug, **kw):
        store.add_server(
            slug, kw.get("name", slug), kw.get("base_url", "http://example.invalid/mcp"),
            kw.get("auth_type", "none"), kw.get("bearer_token"),
            kw.get("transport", "http"), kw.get("command", ""),
            kw.get("args_json", "[]"), kw.get("env_json", "{}"),
        )
        self.addCleanup(store.delete_server, slug)


# ── B. 驗證 ───────────────────────────────────────────────
class TestAuth(ApiTest):
    def test_missing_token_rejected(self):
        r = self.c.get("/api/v1/servers")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "unauthorized")

    def test_wrong_token_rejected(self):
        r = self.c.get("/api/v1/servers", headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)

    def test_non_bearer_scheme_rejected(self):
        r = self.c.get("/api/v1/servers", headers={"Authorization": "Basic abc"})
        self.assertEqual(r.status_code, 401)

    def test_valid_token_accepted(self):
        self.assertEqual(self.c.get("/api/v1/servers", headers=AUTH).status_code, 200)

    def test_health_needs_no_token(self):
        r = self.c.get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ready"])
        self.assertEqual(r.json()["db_path"], _TMP_DB)   # 確實用臨時 DB

    def test_api_disabled_when_no_token_configured(self):
        # 沒設 token → API 回 503,但網頁管理台照常
        original = api.API_TOKEN
        api.API_TOKEN = ""
        try:
            r = self.c.get("/api/v1/servers", headers=AUTH)
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()["error"], "api_disabled")
            self.assertEqual(self.c.get("/").status_code, 200)
        finally:
            api.API_TOKEN = original


# ── D. 密鑰不得外洩────────────────────────────
class TestSecretsNeverLeak(ApiTest):
    def test_bearer_token_not_in_any_response(self):
        self.mkserver("leaky", auth_type="bearer", bearer_token="super-secret-value")

        for path in ("/api/v1/servers", "/api/v1/servers/leaky"):
            body = self.c.get(path, headers=AUTH).text
            self.assertNotIn("super-secret-value", body, f"{path} 洩漏了 bearer token")

        one = self.c.get("/api/v1/servers/leaky", headers=AUTH).json()
        self.assertTrue(one["has_token"])
        self.assertNotIn("bearer_token", one)

    def test_stdio_env_not_in_any_response(self):
        self.mkserver("envy", transport="stdio", command="npx",
                      env_json=json.dumps({"API_KEY": "secret-env-value"}))
        body = self.c.get("/api/v1/servers/envy", headers=AUTH).text
        self.assertNotIn("secret-env-value", body)
        self.assertTrue(self.c.get("/api/v1/servers/envy", headers=AUTH).json()["has_env"])

    def test_secret_encrypted_at_rest(self):
        # DB 內必須是密文
        self.mkserver("enc", auth_type="bearer", bearer_token="plain-token-xyz")
        with open(_TMP_DB, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"plain-token-xyz", raw)


# ── C/D. 下游 CRUD ───────────────────────────────────────
class TestServers(ApiTest):
    def test_list_shape(self):
        self.mkserver("listed")
        rows = self.c.get("/api/v1/servers", headers=AUTH).json()
        row = next(r for r in rows if r["slug"] == "listed")
        self.assertIn("tool_count", row)
        self.assertIsInstance(row["enabled"], bool)     # 0/1 → 布林
        self.assertEqual(row["status"], "unknown")

    def test_get_includes_tools(self):
        self.mkserver("withtools")
        store.cache_tools("withtools", [("alpha", "第一個", {"type": "object"})])
        got = self.c.get("/api/v1/servers/withtools", headers=AUTH).json()
        self.assertEqual(len(got["tools"]), 1)
        t = got["tools"][0]
        self.assertEqual(t["name"], "alpha")
        self.assertIsInstance(t["input_schema"], dict)   # JSON 字串 → 物件
        self.assertIsInstance(t["enabled"], bool)

    def test_get_missing_returns_404(self):
        r = self.c.get("/api/v1/servers/ghost", headers=AUTH)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "server_not_found")

    def test_create_requires_name(self):
        r = self.c.post("/api/v1/servers", headers=AUTH, json={"name": "  "})
        self.assertEqual(r.status_code, 400)

    def test_create_derives_slug(self):
        r = self.c.post("/api/v1/servers", headers=AUTH,
                        json={"name": "My Server!", "base_url": "http://example.invalid/x"})
        self.assertEqual(r.status_code, 201)
        slug = r.json()["slug"]
        self.addCleanup(store.delete_server, slug)
        self.assertEqual(slug, "my_server")

    def test_create_explicit_duplicate_slug_conflicts(self):
        self.mkserver("taken")
        r = self.c.post("/api/v1/servers", headers=AUTH, json={"name": "x", "slug": "taken"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "slug_conflict")

    def test_create_rejects_bad_args_type(self):
        r = self.c.post("/api/v1/servers", headers=AUTH,
                        json={"name": "bad", "transport": "stdio", "args": "not-a-list"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_json")

    def test_patch_preserves_omitted_token(self):
        # 編輯時沒帶 bearer_token,原值必須保留
        self.mkserver("keepme", auth_type="bearer", bearer_token="keep-this-token")
        r = self.c.patch("/api/v1/servers/keepme", headers=AUTH, json={"name": "改名了"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "改名了")
        self.assertEqual(store.get_server("keepme")["bearer_token"], "keep-this-token")

    def test_patch_missing_returns_404(self):
        r = self.c.patch("/api/v1/servers/ghost", headers=AUTH, json={"name": "x"})
        self.assertEqual(r.status_code, 404)

    def test_delete_cascades(self):
        self.mkserver("doomed")
        store.cache_tools("doomed", [("t", "d", {})])
        store.set_oauth_tokens("doomed", '{"access_token":"a"}')
        self.assertEqual(self.c.delete("/api/v1/servers/doomed", headers=AUTH).status_code, 204)
        self.assertIsNone(store.get_server("doomed"))
        self.assertEqual(store.count_tools("doomed"), 0)
        self.assertIsNone(store.get_oauth_tokens("doomed"))

    def test_delete_missing_returns_404(self):
        self.assertEqual(self.c.delete("/api/v1/servers/ghost", headers=AUTH).status_code, 404)

    def test_enabled_takes_explicit_value_not_toggle(self):
        # 重複送同一個值必須冪等,不是每次反轉
        self.mkserver("switch")
        for _ in range(2):
            r = self.c.patch("/api/v1/servers/switch", headers=AUTH, json={})
        r = self.c.patch("/api/v1/servers/switch/enabled", headers=AUTH, json={"enabled": False})
        self.assertFalse(r.json()["enabled"])
        r = self.c.patch("/api/v1/servers/switch/enabled", headers=AUTH, json={"enabled": False})
        self.assertFalse(r.json()["enabled"])   # 仍是 False,沒有被反轉回 True

    def test_enabled_requires_field(self):
        self.mkserver("needfield")
        r = self.c.patch("/api/v1/servers/needfield/enabled", headers=AUTH, json={})
        self.assertEqual(r.status_code, 400)


# ── C. 工具開關 ───────────────────────────────────────────
class TestTools(ApiTest):
    def setUp(self):
        self.mkserver("toolsrv")
        store.cache_tools("toolsrv", [("a", "A", {}), ("b", "B", {}), ("c", "C", {})])

    def test_list_tools(self):
        rows = self.c.get("/api/v1/servers/toolsrv/tools", headers=AUTH).json()
        self.assertEqual({t["name"] for t in rows}, {"a", "b", "c"})

    def test_bulk_disable_then_enable(self):
        self.c.patch("/api/v1/servers/toolsrv/tools", headers=AUTH, json={"enabled": False})
        rows = self.c.get("/api/v1/servers/toolsrv/tools", headers=AUTH).json()
        self.assertTrue(all(not t["enabled"] for t in rows))

        self.c.patch("/api/v1/servers/toolsrv/tools", headers=AUTH, json={"enabled": True})
        rows = self.c.get("/api/v1/servers/toolsrv/tools", headers=AUTH).json()
        self.assertTrue(all(t["enabled"] for t in rows))

    def test_patch_single_tool_flags(self):
        r = self.c.patch("/api/v1/servers/toolsrv/tools/a", headers=AUTH,
                         json={"enabled": False, "needs_confirm": True})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertTrue(r.json()["needs_confirm"])
        # 其他工具不受影響
        self.assertTrue(store.get_tool("toolsrv", "b")["enabled"])

    def test_desc_override_and_clear(self):
        self.c.patch("/api/v1/servers/toolsrv/tools/a", headers=AUTH,
                     json={"desc_override": "  自訂描述  "})
        self.assertEqual(store.get_tool("toolsrv", "a")["desc_override"], "自訂描述")
        self.c.patch("/api/v1/servers/toolsrv/tools/a", headers=AUTH, json={"desc_override": ""})
        self.assertEqual(store.get_tool("toolsrv", "a")["desc_override"], "")

    def test_unknown_tool_returns_404(self):
        r = self.c.patch("/api/v1/servers/toolsrv/tools/nope", headers=AUTH, json={"enabled": True})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "tool_not_found")

    def test_tools_of_unknown_server_returns_404(self):
        self.assertEqual(self.c.get("/api/v1/servers/ghost/tools", headers=AUTH).status_code, 404)


# ── C. 呼叫記錄 ───────────────────────────────────────────
class TestLogs(ApiTest):
    def test_pagination_and_error_filter(self):
        for i in range(7):
            store.log_call("s", f"t{i}", {"i": i}, "error" if i % 2 else "ok", 5)

        r = self.c.get("/api/v1/logs?per_page=3", headers=AUTH).json()
        self.assertEqual(len(r["rows"]), 3)
        self.assertGreaterEqual(r["total"], 7)
        self.assertGreaterEqual(r["pages"], 3)
        self.assertIsInstance(r["rows"][0]["arguments"], dict)   # JSON 字串 → 物件

        only = self.c.get("/api/v1/logs?errors_only=true&per_page=100", headers=AUTH).json()
        self.assertTrue(all(row["status"] == "error" for row in only["rows"]))
        self.assertEqual(only["total"], only["errors"])

    def test_page_is_clamped_not_404(self):
        r = self.c.get("/api/v1/logs?page=99999", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["page"], r.json()["pages"])


# ── C. 人工確認 ───────────────────────────────────────────
class TestActions(ApiTest):
    def test_list_and_get(self):
        aid = store.create_action("some__tool", {"x": 1})
        rows = self.c.get("/api/v1/actions", headers=AUTH).json()
        self.assertIn(aid, [r["id"] for r in rows])

        one = self.c.get(f"/api/v1/actions/{aid}", headers=AUTH).json()
        self.assertEqual(one["status"], store.WAITING)
        self.assertEqual(one["arguments"], {"x": 1})

    def test_filter_by_status(self):
        aid = store.create_action("filter__tool", {})
        rows = self.c.get(f"/api/v1/actions?status={store.WAITING}", headers=AUTH).json()
        self.assertIn(aid, [r["id"] for r in rows])
        rows = self.c.get("/api/v1/actions?status=EXECUTED", headers=AUTH).json()
        self.assertNotIn(aid, [r["id"] for r in rows])

    def test_approve_moves_to_approved(self):
        aid = store.create_action("approve__me", {})
        r = self.c.post(f"/api/v1/actions/{aid}/decision", headers=AUTH, json={"approve": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], store.APPROVED)

    def test_reject_moves_to_rejected(self):
        aid = store.create_action("reject__me", {})
        r = self.c.post(f"/api/v1/actions/{aid}/decision", headers=AUTH, json={"approve": False})
        self.assertEqual(r.json()["status"], store.REJECTED)

    def test_second_decision_conflicts(self):
        # store.decide 不回報 rowcount,API 必須自己擋住重複決定
        aid = store.create_action("twice__me", {})
        self.c.post(f"/api/v1/actions/{aid}/decision", headers=AUTH, json={"approve": True})
        r = self.c.post(f"/api/v1/actions/{aid}/decision", headers=AUTH, json={"approve": False})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "action_not_waiting")
        self.assertEqual(store.get_action(aid)["status"], store.APPROVED)  # 未被改掉

    def test_decision_requires_field(self):
        aid = store.create_action("nofield", {})
        r = self.c.post(f"/api/v1/actions/{aid}/decision", headers=AUTH, json={})
        self.assertEqual(r.status_code, 400)

    def test_unknown_action_returns_404(self):
        self.assertEqual(self.c.get("/api/v1/actions/zzz", headers=AUTH).status_code, 404)
        r = self.c.post("/api/v1/actions/zzz/decision", headers=AUTH, json={"approve": True})
        self.assertEqual(r.status_code, 404)


# ── E. OAuth ──────────────────────────────────────────────
class TestOAuth(ApiTest):
    def test_stdio_server_rejected(self):
        self.mkserver("stdiosrv", transport="stdio", command="npx")
        r = self.c.post("/api/v1/servers/stdiosrv/oauth/start", headers=AUTH)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "unsupported_transport")

    def test_unknown_server_returns_404(self):
        r = self.c.post("/api/v1/servers/ghost/oauth/start", headers=AUTH)
        self.assertEqual(r.status_code, 404)


# ── C. 健檢與重抓 ─────────────────────────────────────────
class TestHealthChecks(ApiTest):
    """用連不上的位址,讓失敗路徑成為確定性的測試,不依賴外部網路。"""

    def test_check_unreachable_server_records_error(self):
        self.mkserver("unreach", base_url="http://127.0.0.1:1/mcp")
        r = self.c.post("/api/v1/servers/unreach/check", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "error")
        self.assertTrue(r.json()["detail"])
        self.assertEqual(store.get_server("unreach")["status"], "error")
        self.assertTrue(store.get_server("unreach")["checked_at"])

    def test_refresh_unreachable_returns_502(self):
        self.mkserver("unreach2", base_url="http://127.0.0.1:1/mcp")
        r = self.c.post("/api/v1/servers/unreach2/refresh", headers=AUTH)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["error"], "downstream_error")

    def test_check_all_counts_only_enabled(self):
        self.mkserver("ca_on", base_url="http://127.0.0.1:1/mcp")
        self.mkserver("ca_off", base_url="http://127.0.0.1:1/mcp")
        store.set_server_enabled("ca_off", False)
        r = self.c.post("/api/v1/servers/check-all", headers=AUTH).json()
        self.assertEqual(r["checked"], r["ok"] + r["failed"])
        self.assertEqual(store.get_server("ca_off")["status"], "unknown")   # 停用的沒被檢查

    def test_check_missing_returns_404(self):
        self.assertEqual(self.c.post("/api/v1/servers/ghost/check", headers=AUTH).status_code, 404)

    def test_error_detail_is_legible_not_taskgroup_noise(self):
        """
        下游錯誤要說出真正的原因。

        MCP SDK 走 anyio task group,錯誤會被包成 ExceptionGroup,直接 str() 只會
        得到「unhandled errors in a TaskGroup」—— 使用者無從判斷是網址錯、沒授權、
        還是對方掛了。這個問題是在原生 app 的畫面上看到才發現的。
        """
        self.mkserver("legible", base_url="http://127.0.0.1:1/mcp")
        detail = self.c.post("/api/v1/servers/legible/check", headers=AUTH).json()["detail"]
        self.assertNotIn("TaskGroup", detail, f"錯誤訊息仍是 TaskGroup 雜訊:{detail}")
        self.assertTrue(detail.strip(), "錯誤訊息不可為空")


# ── E. port 防護───────────────────────────────
class TestPreflight(unittest.TestCase):
    def test_occupied_port_is_refused(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            port = held.getsockname()[1]
            # 暫時把「OAuth 專用 port」當成被佔用的那個,才能測到拒絕路徑
            original = api.OAUTH_PORT
            api.OAUTH_PORT = port
            try:
                ok, msg = api.preflight("127.0.0.1", port)
            finally:
                api.OAUTH_PORT = original
        self.assertFalse(ok)
        self.assertIn(str(port), msg)

    def test_free_default_port_passes(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        original = api.OAUTH_PORT
        api.OAUTH_PORT = free
        try:
            ok, msg = api.preflight("127.0.0.1", free)
        finally:
            api.OAUTH_PORT = original
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_time_wait_socket_does_not_block_startup(self):
        """
        preflight 必須跟 uvicorn 一樣設 SO_REUSEADDR。

        少了它,剛關掉的後端留下的 TIME_WAIT socket 會讓 bind 失敗,於是明明能啟動
        卻被擋下 —— 這個情境在「重啟 app」時每次都會遇到。
        """
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        s.close()   # 關掉 → socket 進入 TIME_WAIT,但已無人 LISTEN

        original = api.OAUTH_PORT
        api.OAUTH_PORT = port
        try:
            ok, msg = api.preflight("127.0.0.1", port)
        finally:
            api.OAUTH_PORT = original
        self.assertTrue(ok, f"TIME_WAIT 的 port 不該擋住啟動:{msg}")

    def test_non_default_port_warns_but_allows(self):
        ok, msg = api.preflight("127.0.0.1", api.OAUTH_PORT + 1)
        self.assertTrue(ok)
        self.assertIn("redirect_uri", msg)   # 說明為什麼換 port 有風險


# ── F. 即時推送 ───────────────────────────────────────────
class TestEvents(ApiTest):
    def test_requires_auth(self):
        self.assertEqual(self.c.get("/api/v1/events").status_code, 401)

    def test_publish_reaches_subscribers(self):
        import asyncio
        q = asyncio.Queue(maxsize=10)
        api._subscribers.add(q)
        try:
            api.publish("server_status", {"slug": "x", "status": "ok"})
            event, data = q.get_nowait()
        finally:
            api._subscribers.discard(q)
        self.assertEqual(event, "server_status")
        self.assertEqual(data["slug"], "x")

    def test_full_queue_does_not_raise(self):
        # 慢的 client 不該拖住健檢迴圈
        import asyncio
        q = asyncio.Queue(maxsize=1)
        api._subscribers.add(q)
        try:
            api.publish("a")
            api.publish("b")   # 佇列已滿,必須默默丟棄而不是拋例外
        finally:
            api._subscribers.discard(q)

    def test_write_endpoints_publish(self):
        import asyncio
        self.mkserver("pubsrv")
        q = asyncio.Queue(maxsize=50)
        api._subscribers.add(q)
        try:
            self.c.patch("/api/v1/servers/pubsrv/enabled", headers=AUTH, json={"enabled": False})
            aid = store.create_action("pub__tool", {})
            self.c.post(f"/api/v1/actions/{aid}/decision", headers=AUTH, json={"approve": True})
            events = []
            while not q.empty():
                events.append(q.get_nowait()[0])
        finally:
            api._subscribers.discard(q)
        self.assertIn("server_changed", events)
        self.assertIn("action_decided", events)


# ── H. 未繞過 store 層──────────────────────────
class TestLayering(unittest.TestCase):
    def test_api_does_not_touch_sqlite_directly(self):
        import inspect
        src = inspect.getsource(api)
        self.assertNotIn("sqlite3", src)
        self.assertNotIn("SELECT ", src)
        self.assertNotIn("INSERT ", src)


if __name__ == "__main__":
    unittest.main()
