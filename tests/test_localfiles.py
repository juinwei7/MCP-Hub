"""
本機檔案參數:下游用 contentEncoding: base64 標檔案欄位,AI 填路徑,Hub 讀檔轉發。

跑法:
    ./.venv/bin/python -m unittest tests.test_localfiles -v
"""
import asyncio
import base64
import json
import os
import tempfile
import unittest
from unittest import mock

# 必須在 import gateway 之前(見 tests/_env.py)
from tests import _env  # noqa: F401,E402

from gateway import dispatch, localfiles, store  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "attachments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "data": {"type": "string", "contentEncoding": "base64", "description": "檔案內容"},
                },
            },
        },
        "cover": {"type": "string", "contentEncoding": "base64"},
    },
}


def setUpModule():
    store.init_db()


class TestSchema(unittest.TestCase):
    def test_file_fields_become_path_fields(self):
        out = localfiles.for_client(SCHEMA)
        data = out["properties"]["attachments"]["items"]["properties"]["data"]
        self.assertNotIn("contentEncoding", data)
        self.assertTrue(data["description"].startswith("檔案內容。"))
        self.assertIn("絕對路徑", data["description"])
        self.assertIn("絕對路徑", out["properties"]["cover"]["description"])
        # 原本那份不能被改到:轉發時還要靠它找出檔案欄位
        self.assertEqual(SCHEMA["properties"]["cover"]["contentEncoding"], "base64")

    def test_schema_without_file_fields_is_untouched(self):
        plain = {"type": "object", "properties": {"q": {"type": "string"}}}
        self.assertIs(localfiles.for_client(plain), plain)
        self.assertFalse(localfiles.has_file_fields(plain))
        self.assertFalse(localfiles.has_file_fields(None))


class TestExpand(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".png")
        os.write(fd, b"\x89PNG fake")
        os.close(fd)

    def tearDown(self):
        os.remove(self.path)

    def test_paths_are_replaced_with_base64(self):
        args = {"content": "/not/a/file/field", "attachments": [{"name": "a.png", "data": self.path}], "cover": self.path}
        out = localfiles.expand(SCHEMA, args)
        b64 = base64.b64encode(b"\x89PNG fake").decode()
        self.assertEqual(out["attachments"][0]["data"], b64)
        self.assertEqual(out["cover"], b64)
        self.assertEqual(out["attachments"][0]["name"], "a.png")
        self.assertEqual(out["content"], "/not/a/file/field")  # 一般字串不碰
        self.assertEqual(args["cover"], self.path)              # 原參數不變

    def test_bad_paths(self):
        with self.assertRaisesRegex(localfiles.LocalFileError, "不是絕對路徑"):
            localfiles.expand(SCHEMA, {"cover": "relative.png"})
        with self.assertRaisesRegex(localfiles.LocalFileError, "找不到檔案"):
            localfiles.expand(SCHEMA, {"cover": self.path + ".missing"})
        with mock.patch.object(localfiles, "MAX_BYTES", 3):
            with self.assertRaisesRegex(localfiles.LocalFileError, "超過"):
                localfiles.expand(SCHEMA, {"cover": self.path})


class TestForward(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".png")
        os.write(fd, b"img")
        os.close(fd)
        store.add_server("filesrv", "File Server", "http://127.0.0.1:1/mcp", "none")
        store.cache_tools("filesrv", [("send", "", SCHEMA)])

    def tearDown(self):
        store.delete_server("filesrv")
        os.remove(self.path)

    def _last_log(self):
        return store.list_logs(limit=1)[0]

    def test_downstream_gets_base64_but_log_keeps_path(self):
        sent = {}

        async def fake_call(srv, tool, arguments):
            sent.update(arguments)
            return mock.Mock(content=[mock.Mock(type="text", text="ok")])

        with mock.patch.object(dispatch.pool, "call_tool", fake_call):
            blocks = asyncio.run(dispatch.dispatch_tool("filesrv__send", {"cover": self.path}))
        self.assertEqual(blocks[0].text, "ok")
        self.assertEqual(sent["cover"], base64.b64encode(b"img").decode())
        log = self._last_log()
        self.assertIn(self.path, log["arguments"])
        self.assertNotIn(sent["cover"], log["arguments"])

    def test_bad_path_is_reported_without_calling_downstream(self):
        with mock.patch.object(dispatch.pool, "call_tool", side_effect=AssertionError("不該呼叫下游")):
            blocks = asyncio.run(dispatch.dispatch_tool("filesrv__send", {"cover": "nope.png"}))
        self.assertIn("不是絕對路徑", blocks[0].text)
        self.assertEqual(self._last_log()["status"], "error")


if __name__ == "__main__":
    unittest.main()
