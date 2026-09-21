"""
自訂工具、複合工具、分類、匯入、匯出的 JSON API 測試。

這些端點是為了取代網頁管理台而存在的,所以測試的重點是:
每一件原本網頁做得到的事,這裡也做得到,而且**錯誤會說出來**而不是靜默。
網頁版有好幾處是 `except: pass`,那種行為不該被沿用。
"""
import json
import unittest

from tests._env import AUTH  # noqa: E402

from gateway import store, web  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def setUpModule():
    store.init_db()


class ToolsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(web.app)

    def mkcustom(self, name, **kw):
        body = {"name": name, "url_template": kw.get("url", "https://api.invalid/v")}
        body.update({k: v for k, v in kw.items() if k != "url"})
        r = self.c.post("/api/v1/custom-tools", headers=AUTH, json=body)
        self.assertEqual(r.status_code, 201, r.text)
        self.addCleanup(store.delete_custom_tool, name)
        return r.json()

    def mkcomposite(self, name, **kw):
        body = {"name": name, "steps": kw.pop("steps", [{"id": "s1", "tool": "x", "args": {}}])}
        body.update(kw)
        r = self.c.post("/api/v1/composite-tools", headers=AUTH, json=body)
        self.assertEqual(r.status_code, 201, r.text)
        self.addCleanup(store.delete_composite_tool, name)
        return r.json()


# ── 密鑰不得外洩 ──────────────────────────────────────────
class TestSecretsNeverLeak(ToolsApiTest):
    """
    自訂工具的 headers 通常放 API 金鑰,而 store 回傳時已經解密。
    網頁版直接把它渲染進 HTML;這裡一律只回報「有哪些 key、有沒有值」。
    """

    SECRET = "SUPER-SECRET-API-KEY"

    def test_headers_value_never_in_any_response(self):
        self.mkcustom("leaky", headers={"Authorization": f"Bearer {self.SECRET}"})
        for path in ("/api/v1/custom-tools", "/api/v1/custom-tools/leaky"):
            self.assertNotIn(self.SECRET, self.c.get(path, headers=AUTH).text,
                             f"{path} 洩漏了 header 內容")

    def test_headers_reported_as_presence_only(self):
        self.mkcustom("presence", headers={"Authorization": "Bearer x", "X-Region": "tw"})
        got = self.c.get("/api/v1/custom-tools/presence", headers=AUTH).json()["headers"]
        self.assertEqual(got, {"Authorization": {"set": True}, "X-Region": {"set": True}})

    def test_export_omits_secrets_by_default(self):
        store.add_server("expsrv", "Exp", "http://x/mcp", "bearer", "BEARER-SECRET-999")
        self.addCleanup(store.delete_server, "expsrv")
        body = self.c.get("/api/v1/config/export", headers=AUTH).text
        self.assertNotIn("BEARER-SECRET-999", body)
        self.assertIn("密鑰已略過", body)

    def test_export_includes_secrets_only_when_asked(self):
        # 使用者仍然做得到,但那是一個有意識的選擇,不是按一下就發生的副作用
        store.add_server("expsrv2", "Exp", "http://x/mcp", "bearer", "BEARER-SECRET-888")
        self.addCleanup(store.delete_server, "expsrv2")
        body = self.c.get("/api/v1/config/export?include_secrets=true", headers=AUTH).text
        self.assertIn("BEARER-SECRET-888", body)

    def test_headers_encrypted_at_rest(self):
        self.mkcustom("encrypted", headers={"Authorization": "Bearer AT-REST-SECRET"})
        import os
        with open(os.environ["MCP_HUB_DB"], "rb") as f:
            raw = f.read()
        self.assertNotIn(b"AT-REST-SECRET", raw)


# ── 自訂工具 ──────────────────────────────────────────────
class TestCustomTools(ToolsApiTest):
    def test_create_and_read(self):
        created = self.mkcustom("basic", description="說明", method="post",
                                params=[{"name": "q", "required": True}])
        self.assertEqual(created["method"], "POST")   # 一律大寫
        self.assertTrue(created["enabled"])
        self.assertFalse(created["needs_confirm"])
        self.assertEqual(self.c.get("/api/v1/custom-tools/basic", headers=AUTH).json()["description"], "說明")

    def test_rejects_invalid_name_characters(self):
        r = self.c.post("/api/v1/custom-tools", headers=AUTH,
                        json={"name": "有中文", "url_template": "https://x"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "name_conflict")

    def test_rejects_namespace_separator(self):
        # __ 是下游的 slug__tool 命名空間,自訂工具用了會撞在一起
        r = self.c.post("/api/v1/custom-tools", headers=AUTH,
                        json={"name": "a__b", "url_template": "https://x"})
        self.assertEqual(r.status_code, 409)

    def test_rejects_reserved_names(self):
        for reserved in ("check_action", "skill_workbench"):
            r = self.c.post("/api/v1/custom-tools", headers=AUTH,
                            json={"name": reserved, "url_template": "https://x"})
            self.assertEqual(r.status_code, 409, reserved)

    def test_rejects_duplicate(self):
        self.mkcustom("dup")
        r = self.c.post("/api/v1/custom-tools", headers=AUTH,
                        json={"name": "dup", "url_template": "https://x"})
        self.assertEqual(r.status_code, 409)

    def test_rejects_conflict_with_downstream_tool(self):
        store.add_server("dsrv", "D", "http://x/mcp")
        self.addCleanup(store.delete_server, "dsrv")
        store.cache_tools("dsrv", [("collide", "d", {})])
        r = self.c.post("/api/v1/custom-tools", headers=AUTH,
                        json={"name": "collide", "url_template": "https://x"})
        self.assertEqual(r.status_code, 409)

    def test_requires_url(self):
        r = self.c.post("/api/v1/custom-tools", headers=AUTH, json={"name": "nourl"})
        self.assertEqual(r.status_code, 400)

    def test_flags_use_explicit_values_not_toggle(self):
        self.mkcustom("flags")
        for _ in range(2):
            r = self.c.patch("/api/v1/custom-tools/flags", headers=AUTH, json={"enabled": False})
        self.assertFalse(r.json()["enabled"], "重複送同一個值不該被反轉")

    def test_omitted_header_is_preserved(self):
        # API 不會把 header 值送給 client,所以 client 無法送回完整的 headers ——
        # 省略必須代表「不動」,否則每次編輯描述就會把金鑰洗掉
        self.mkcustom("keephdr", headers={"Authorization": "Bearer keep-me"})
        self.c.patch("/api/v1/custom-tools/keephdr", headers=AUTH, json={"description": "改了"})
        self.assertEqual(store.get_custom_tool("keephdr")["headers"],
                         {"Authorization": "Bearer keep-me"})

    def test_empty_string_header_deletes_it(self):
        self.mkcustom("delhdr", headers={"A": "1", "B": "2"})
        self.c.patch("/api/v1/custom-tools/delhdr", headers=AUTH, json={"headers": {"A": ""}})
        self.assertEqual(store.get_custom_tool("delhdr")["headers"], {"B": "2"})

    def test_delete(self):
        self.mkcustom("goner")
        self.assertEqual(self.c.delete("/api/v1/custom-tools/goner", headers=AUTH).status_code, 204)
        self.assertIsNone(store.get_custom_tool("goner"))

    def test_missing_returns_404(self):
        for method, path in (("get", "/api/v1/custom-tools/ghost"),
                             ("delete", "/api/v1/custom-tools/ghost")):
            r = getattr(self.c, method)(path, headers=AUTH)
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.json()["error"], "custom_tool_not_found")


# ── 複合工具 ──────────────────────────────────────────────
class TestCompositeTools(ToolsApiTest):
    def test_create_and_read(self):
        created = self.mkcomposite("comp", description="彙整")
        self.assertEqual(created["output"], "collect")
        self.assertEqual(len(created["steps"]), 1)

    def test_requires_at_least_one_step(self):
        r = self.c.post("/api/v1/composite-tools", headers=AUTH,
                        json={"name": "nosteps", "steps": []})
        self.assertEqual(r.status_code, 400)

    def test_step_must_have_id_and_tool(self):
        r = self.c.post("/api/v1/composite-tools", headers=AUTH,
                        json={"name": "badstep", "steps": [{"id": "a"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("第 1 個步驟", r.json()["message"])

    def test_step_args_must_be_object(self):
        r = self.c.post("/api/v1/composite-tools", headers=AUTH,
                        json={"name": "badargs", "steps": [{"id": "a", "tool": "t", "args": []}]})
        self.assertEqual(r.status_code, 400)

    def test_only_collect_output_supported(self):
        r = self.c.post("/api/v1/composite-tools", headers=AUTH,
                        json={"name": "badout", "steps": [{"id": "a", "tool": "t"}],
                              "output": "last"})
        self.assertEqual(r.status_code, 400)

    def test_delete_cascades_skill_draft(self):
        self.mkcomposite("withskill")
        store.save_skill_draft("withskill", "---\nname: withskill\ndescription: x\n---\n\nbody")
        self.assertIsNotNone(store.get_skill_draft("withskill"))
        self.c.delete("/api/v1/composite-tools/withskill", headers=AUTH)
        self.assertIsNone(store.get_skill_draft("withskill"))

    def test_skill_returns_starter_when_no_draft(self):
        self.mkcomposite("skillstart")
        got = self.c.get("/api/v1/composite-tools/skillstart/skill", headers=AUTH).json()
        self.assertFalse(got["is_draft"])
        self.assertIn("name: skillstart", got["markdown"])
        self.assertEqual(got["problem"], "", "自動產生的 starter 不該是無效的")

    def test_skill_rejects_invalid_markdown(self):
        self.mkcomposite("skillbad")
        r = self.c.put("/api/v1/composite-tools/skillbad/skill", headers=AUTH,
                       json={"markdown": "沒有 frontmatter"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_skill")

    def test_skill_download_returns_zip(self):
        self.mkcomposite("skillzip")
        md = self.c.get("/api/v1/composite-tools/skillzip/skill", headers=AUTH).json()["markdown"]
        r = self.c.post("/api/v1/composite-tools/skillzip/skill/download",
                        headers=AUTH, json={"markdown": md})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertTrue(r.content.startswith(b"PK"), "應該是真的 ZIP 位元組")


# ── 分類與批次 ────────────────────────────────────────────
class TestCategories(ToolsApiTest):
    def test_create_and_overview(self):
        self.c.post("/api/v1/categories", headers=AUTH, json={"name": "分類甲"})
        self.addCleanup(store.delete_category, "分類甲")
        names = [c["name"] for c in self.c.get("/api/v1/categories", headers=AUTH).json()["categories"]]
        self.assertIn("分類甲", names)

    def test_bulk_move(self):
        self.mkcustom("mover")
        self.c.post("/api/v1/categories", headers=AUTH, json={"name": "目的地"})
        self.addCleanup(store.delete_category, "目的地")
        r = self.c.post("/api/v1/custom-tools/bulk", headers=AUTH,
                        json={"names": ["mover"], "action": "move", "category": "目的地"})
        self.assertEqual(r.json()["affected"], 1)
        self.assertEqual(store.get_custom_tool("mover")["group_name"], "目的地")

    def test_bulk_rejects_unknown_action(self):
        r = self.c.post("/api/v1/custom-tools/bulk", headers=AUTH,
                        json={"names": ["x"], "action": "explode"})
        self.assertEqual(r.status_code, 400)

    def test_bulk_rejects_empty_names(self):
        # 網頁版對空選取是靜默 no-op,使用者按了沒反應也不知道為什麼
        r = self.c.post("/api/v1/custom-tools/bulk", headers=AUTH,
                        json={"names": [], "action": "delete"})
        self.assertEqual(r.status_code, 400)

    def test_category_headers_rejects_non_object(self):
        # 網頁版是 except: pass —— 填錯的 JSON 被默默忽略,完全沒有回饋
        r = self.c.patch("/api/v1/categories/x", headers=AUTH, json={"headers": "not-an-object"})
        self.assertEqual(r.status_code, 400)

    def test_delete_category_removes_its_tools(self):
        self.c.post("/api/v1/categories", headers=AUTH, json={"name": "要刪的"})
        self.mkcustom("in_cat", group_name="要刪的")
        self.c.delete("/api/v1/categories/要刪的", headers=AUTH)
        self.assertIsNone(store.get_custom_tool("in_cat"), "刪分類會連同裡面的工具一起刪")


# ── 匯入 ──────────────────────────────────────────────────
class TestImport(ToolsApiTest):
    def test_import_mcp_servers_from_object(self):
        r = self.c.post("/api/v1/import/mcp-servers", headers=AUTH, json={
            "config": {"mcpServers": {"imp1": {"url": "http://imp1/mcp"}}}})
        self.assertEqual(r.status_code, 200)
        self.assertIn("imp1", r.json()["added"])
        self.addCleanup(store.delete_server, "imp1")

    def test_import_from_json_string(self):
        r = self.c.post("/api/v1/import/mcp-servers", headers=AUTH, json={
            "config": json.dumps({"mcpServers": {"imp2": {"url": "http://imp2/mcp"}}})})
        self.assertIn("imp2", r.json()["added"])
        self.addCleanup(store.delete_server, "imp2")

    def test_import_rejects_bad_json(self):
        r = self.c.post("/api/v1/import/mcp-servers", headers=AUTH, json={"config": "{not json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_json")

    def test_import_extracts_bearer_token(self):
        self.c.post("/api/v1/import/mcp-servers", headers=AUTH, json={"config": {
            "mcpServers": {"imp3": {"url": "http://imp3/mcp",
                                    "headers": {"Authorization": "Bearer tok-123"}}}}})
        self.addCleanup(store.delete_server, "imp3")
        srv = store.get_server("imp3")
        self.assertEqual(srv["auth_type"], "bearer")
        self.assertEqual(srv["bearer_token"], "tok-123")

    def test_import_skips_self_reference(self):
        # 匯入指向本 Hub 自己的設定會造成迴圈
        r = self.c.post("/api/v1/import/mcp-servers", headers=AUTH, json={"config": {
            "mcpServers": {"selfref": {"command": "python", "args": ["-m", "gateway.hub_server"]}}}})
        self.assertEqual(r.json()["added"], [])
        self.assertIn("selfref", r.json()["skipped"])

    def test_import_skips_existing_slug(self):
        store.add_server("dupimp", "Dup", "http://x/mcp")
        self.addCleanup(store.delete_server, "dupimp")
        r = self.c.post("/api/v1/import/mcp-servers", headers=AUTH, json={
            "config": {"mcpServers": {"dupimp": {"url": "http://other/mcp"}}}})
        self.assertEqual(r.json()["added"], [])
        self.assertEqual(store.get_server("dupimp")["base_url"], "http://x/mcp", "不該覆寫既有的")

    def test_claude_config_preview_does_not_write(self):
        before = len(store.list_servers())
        r = self.c.get("/api/v1/import/claude-config", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn("entries", r.json())
        self.assertEqual(len(store.list_servers()), before, "預覽不該寫入任何東西")

    def test_openapi_import_requires_op_ids(self):
        r = self.c.post("/api/v1/openapi/import", headers=AUTH,
                        json={"spec_text": '{"openapi":"3.0.0","paths":{}}', "op_ids": []})
        self.assertEqual(r.status_code, 400)

    def test_openapi_preview_rejects_bad_spec(self):
        r = self.c.post("/api/v1/openapi/preview", headers=AUTH, json={"spec_text": "not a spec"})
        self.assertEqual(r.status_code, 400)


# ── 目錄項 ────────────────────────────────────────────────
class TestDirectoryEntries(ToolsApiTest):
    def test_list_includes_builtin_seeds(self):
        entries = self.c.get("/api/v1/directory-entries", headers=AUTH).json()
        self.assertTrue(entries)
        self.assertTrue(any(e["builtin"] for e in entries))

    def test_builtin_cannot_be_deleted(self):
        # 網頁版是靜默失敗(SQL 帶 builtin=0),按了沒反應也不知道為什麼
        builtin = next(e for e in self.c.get("/api/v1/directory-entries", headers=AUTH).json()
                       if e["builtin"])
        r = self.c.delete(f"/api/v1/directory-entries/{builtin['id']}", headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "builtin_entry")

    def test_add_rejects_non_list_args(self):
        # 網頁版把壞掉的 args 默默吞成 [],使用者不會知道自己填錯了
        r = self.c.post("/api/v1/directory-entries", headers=AUTH,
                        json={"name": "bad", "args": "not-a-list"})
        self.assertEqual(r.status_code, 400)

    def test_install_creates_server(self):
        self.c.post("/api/v1/directory-entries", headers=AUTH, json={
            "id": "installme", "name": "Install Me", "transport": "http",
            "base_url": "http://install/mcp", "auth": "token"})
        self.addCleanup(store.delete_directory_entry, "installme")
        r = self.c.post("/api/v1/directory-entries/installme/install", headers=AUTH)
        self.assertEqual(r.status_code, 201)
        slug = r.json()["slug"]
        self.addCleanup(store.delete_server, slug)
        self.assertEqual(store.get_server(slug)["auth_type"], "bearer")
        self.assertIn("token", r.json()["next_step"])

    def test_install_missing_returns_404(self):
        r = self.c.post("/api/v1/directory-entries/ghost/install", headers=AUTH)
        self.assertEqual(r.status_code, 404)


# ── 驗證 ──────────────────────────────────────────────────
class TestAuthRequired(ToolsApiTest):
    def test_all_new_endpoints_require_token(self):
        for path in ("/api/v1/custom-tools", "/api/v1/composite-tools",
                     "/api/v1/categories", "/api/v1/directory-entries",
                     "/api/v1/config/export"):
            self.assertEqual(self.c.get(path).status_code, 401, path)


if __name__ == "__main__":
    unittest.main()
