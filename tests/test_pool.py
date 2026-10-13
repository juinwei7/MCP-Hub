"""
連線池的斷線行為 —— 這些測試守的是「Hub 會三不五時斷線」那個缺陷。

症狀是這樣的:下游(例如回一個 HTTP 500)壞一次,Claude 那端就先等不到這次
工具呼叫的回覆(Request timed out),接著下一次呼叫 Connection closed,然後
整場工作階段都是 Not connected。

原因有兩層,兩層都在這裡測:
  1. 連線 task 跟 hub_server 的 server.run() 是同一個 task group 的兄弟。
     anyio 的規則是「子 task 拋例外 → 整個 group 一起取消」,所以下游的例外
     只要外漏一次,就會連帶炸掉 server.run(),整個 Hub 行程結束。
  2. 連線收攤時若不補回覆,已經排進 channel 的請求就沒人處理,呼叫者永遠在等。

所以每個測試都問同一件事:下游壞掉的時候,**server.run() 還活著嗎、呼叫者
拿到答覆了嗎**。
"""
import functools
import time
import unittest
from contextlib import asynccontextmanager

import anyio

from tests import _env  # noqa: F401  （必須在 import gateway 之前）
from gateway import hub, pool, store

SRV = {"slug": "probe", "transport": "http", "base_url": "http://127.0.0.1:1/mcp"}


class _Session:
    """假的下游 session。list_tools / call_tool 的行為由建構參數決定。"""

    def __init__(self, *, raises=None, hangs=False, delay=0.0, fail_calls=()):
        self.raises = raises
        self.hangs = hangs
        self.delay = delay
        self.fail_calls = set(fail_calls)   # 第幾次呼叫要失敗(1 起算)
        self.calls = 0

    async def _act(self):
        self.calls += 1
        nth = self.calls          # 先編號再 await,併發時順序才是確定的
        if self.delay:
            await anyio.sleep(self.delay)
        if self.hangs:
            await anyio.sleep(3600)
        if nth in self.fail_calls:
            raise RuntimeError(f"第 {nth} 次呼叫失敗")
        if self.raises is not None:
            raise self.raises
        return "pong"

    async def call_tool(self, name, arguments):
        return await self._act()

    async def list_tools(self):
        await self._act()
        class _R:
            tools = []
        return _R()


def _opener(session=None, *, fail_enter=None, hang_enter=False, fail_exit=None):
    """造一個替身 _open_session。"""

    @asynccontextmanager
    async def _open(server):
        if hang_enter:
            await anyio.sleep(3600)
        if fail_enter is not None:
            raise fail_enter
        try:
            yield session if session is not None else _Session()
        finally:
            if fail_exit is not None:
                raise fail_exit

    return _open


async def _run_hub(opener, body, *, grace=5):
    """
    照 hub_server.main() 的形狀跑一次:連線 task 與「server.run()」同一個 task group。

    回傳 (body 的結果, body 的例外, server.run 有沒有被連累而提早中止)。
    """
    pool._conns.clear()
    pool._locks.clear()
    original = pool._open_session
    pool._open_session = opener
    state = {"body_done": False, "killed_early": False}
    out = {"value": None, "error": None}
    try:
        async with anyio.create_task_group() as tg:
            pool.bind(tg)

            async def server_run():
                try:
                    while True:
                        await anyio.sleep(0.01)
                except BaseException:
                    # 關機時被取消是正常的;body 還沒跑完就被取消才是缺陷。
                    if not state["body_done"]:
                        state["killed_early"] = True
                    raise

            tg.start_soon(server_run)
            try:
                with anyio.fail_after(grace):
                    out["value"] = await body()
            except BaseException as e:      # noqa: BLE001 —— 要看的就是它丟什麼
                out["error"] = e
            state["body_done"] = True
            await pool.stop()
            tg.cancel_scope.cancel()
    finally:
        pool._open_session = original
        pool._conns.clear()
    return out["value"], out["error"], state["killed_early"]


class ConnectionFailuresDoNotKillTheHub(unittest.TestCase):
    """下游怎麼壞,server.run() 都要活著 —— 否則 Claude 看到的就是斷線。"""

    def test_downstream_error_during_call(self):
        async def body():
            return await pool.call_tool(SRV, "t", {})

        _, err, killed = anyio.run(
            _run_hub, _opener(_Session(raises=RuntimeError("下游 500"))), body)
        self.assertFalse(killed, "下游呼叫失敗不該連帶取消 server.run()")
        self.assertIsNotNone(err, "呼叫者必須拿到錯誤,而不是永遠等下去")

    def test_exception_escaping_session_teardown(self):
        """
        最接近真實的形狀:錯誤是從離開 session 時炸出來的。

        mcp SDK 的 streamable-http transport 用 tg.start_soon 發 POST,而裡面的
        response.raise_for_status() 沒有保護。下游回 5xx 時,例外會從 transport
        自己的 task group 拋出來 —— 也就是在我們離開 async with 的那一刻,
        包成 ExceptionGroup。
        """
        boom = BaseExceptionGroup(
            "unhandled errors in a TaskGroup", [RuntimeError("Server error '500'")])

        async def body():
            return await pool.call_tool(SRV, "t", {})

        _, err, killed = anyio.run(
            _run_hub, _opener(_Session(raises=RuntimeError("下游 500")), fail_exit=boom), body)
        self.assertFalse(killed, "session 收尾時拋的例外不該殺掉整個 Hub")
        self.assertIsNotNone(err)

    def test_connect_failure_still_reaches_the_caller(self):
        """啟動階段的失敗要照常往上報 —— 那是 _get() 在等的回報,不該被吞掉。"""
        async def body():
            return await pool.call_tool(SRV, "t", {})

        _, err, killed = anyio.run(
            _run_hub, _opener(fail_enter=ConnectionRefusedError("連不上")), body)
        self.assertFalse(killed)
        self.assertIsInstance(err, ConnectionRefusedError)

    def test_connect_that_never_finishes_is_bounded(self):
        """連線卡住(OAuth 換票、TCP)時要逾時放棄,不能讓 Claude 無限等。"""
        original = pool.CONNECT_TIMEOUT
        pool.CONNECT_TIMEOUT = 0.2

        async def body():
            return await pool.call_tool(SRV, "t", {})

        try:
            _, err, killed = anyio.run(_run_hub, _opener(hang_enter=True), body)
        finally:
            pool.CONNECT_TIMEOUT = original
        self.assertFalse(killed)
        self.assertIsInstance(err, TimeoutError)


class EveryRequestGetsAnAnswer(unittest.TestCase):
    """沒有答覆的請求跟行程死掉一樣會造成斷線,而且更難查。"""

    def test_queued_requests_are_answered_when_the_connection_dies(self):
        """
        連線在處理第一個請求時壞掉,排在後面的請求不能跟著消失。

        以前連線 task 的 finally 只是把自己從 _conns 移除,沒有關掉收件端,
        所以後面的請求照樣送得進 buffer —— 然後永遠沒人處理。
        """
        session = _Session(raises=RuntimeError("下游斷了"))

        async def body():
            errors = []

            async def one():
                try:
                    await pool.call_tool(SRV, "t", {})
                    errors.append(None)
                except BaseException as e:   # noqa: BLE001
                    errors.append(e)

            async with anyio.create_task_group() as tg:
                for _ in range(5):
                    tg.start_soon(one)
            return errors

        errors, err, killed = anyio.run(_run_hub, _opener(session), body)
        self.assertFalse(killed)
        self.assertIsNone(err, "五個併發請求應該全部在期限內有結果")
        self.assertEqual(len(errors), 5)
        self.assertTrue(all(e is not None for e in errors), "每個請求都該拿到錯誤")

    def test_inflight_request_is_answered_when_the_connection_is_retired(self):
        """
        正在處理中的請求已經離開 channel buffer,排空 buffer 補不到它 ——
        所以連線得另外記著它(_Conn.inflight)。
        """
        async def body():
            async with anyio.create_task_group() as tg:
                async def retire_soon():
                    await anyio.sleep(0.1)
                    conn = pool._conns.get(SRV["slug"])
                    if conn is not None:
                        await pool._retire(conn, RuntimeError("連線被收掉"))

                tg.start_soon(retire_soon)
                # retries=0:這裡要看的是「這一次嘗試」有沒有被回覆,
                # 不要讓自動重連的第二次嘗試混進來。
                return await pool._request(SRV, "call", ("t", {}), retries=0)

        _, err, killed = anyio.run(
            functools.partial(_run_hub, _opener(_Session(hangs=True)), body, grace=3))
        self.assertFalse(killed)
        self.assertIsNotNone(err, "處理中的請求被收攤時必須拿到錯誤,不能無聲無息")
        self.assertNotIsInstance(err, TimeoutError, "不該是靠 grace 期限才結束")
        self.assertIn("連線被收掉", hub.exception_details(err))

    def test_a_hanging_downstream_answers_before_claude_gives_up(self):
        """
        下游完全不回應時,我們自己要先回錯誤。

        等過 Claude 給一次工具呼叫的耐心,它放棄的方式是把整條連線判死 ——
        那就是一次斷線。所以 pool.call_tool 從頭到尾都有總上限。
        """
        originals = (pool.TOTAL_TIMEOUT, pool.REPLY_TIMEOUT, pool.DOWNSTREAM_TIMEOUT)
        pool.TOTAL_TIMEOUT, pool.REPLY_TIMEOUT, pool.DOWNSTREAM_TIMEOUT = 0.5, 10, 10

        async def body():
            return await pool.call_tool(SRV, "t", {})

        try:
            _, err, killed = anyio.run(
                functools.partial(_run_hub, _opener(_Session(hangs=True)), body, grace=4))
        finally:
            pool.TOTAL_TIMEOUT, pool.REPLY_TIMEOUT, pool.DOWNSTREAM_TIMEOUT = originals
        self.assertFalse(killed)
        self.assertIsInstance(err, TimeoutError, "應該是我們自己的總上限先到,而不是 grace")

    def test_a_broken_downstream_recovers_on_the_next_call(self):
        """壞掉之後要能自己重連 —— 不然使用者得重啟 Claude。"""
        session = _Session(raises=RuntimeError("第一次失敗"))

        async def body():
            try:
                await pool.call_tool(SRV, "t", {})
            except BaseException:  # noqa: BLE001
                pass
            session.raises = None          # 下游復原
            return await pool.call_tool(SRV, "t", {})

        value, err, killed = anyio.run(_run_hub, _opener(session), body)
        self.assertFalse(killed)
        self.assertIsNone(err)
        self.assertEqual(value, "pong")


class RequestsOnOneConnectionRunConcurrently(unittest.TestCase):
    """
    同一條連線上的請求不排隊。

    Claude 一次批次打十幾個工具呼叫是常態。排隊的話,後面那幾個會等到超過
    逾時上限而失敗 —— 實測 20 個各 2 秒的呼叫要 40 秒,其中一個失敗。
    MCP 的 ClientSession 本來就支援併發請求,沒有理由讓它們排隊。
    """

    def test_a_batch_takes_one_round_trip_not_n(self):
        session = _Session(delay=0.2)

        async def body():
            results = []

            async def one():
                results.append(await pool.call_tool(SRV, "t", {}))

            started = time.monotonic()
            async with anyio.create_task_group() as tg:
                for _ in range(10):
                    tg.start_soon(one)
            return time.monotonic() - started, results

        value, err, killed = anyio.run(_run_hub, _opener(session), body)
        self.assertFalse(killed)
        self.assertIsNone(err)
        elapsed, results = value
        self.assertEqual(results, ["pong"] * 10)
        self.assertLess(elapsed, 1.0,
                        f"10 個各 0.2 秒的呼叫花了 {elapsed:.1f} 秒 —— 排隊了(序列要 2 秒)")

    def test_one_failure_does_not_cancel_its_siblings(self):
        """一個請求失敗時,同一條連線上其他正在跑的請求要照樣跑完。"""
        session = _Session(delay=0.1, fail_calls={1})

        async def body():
            outcomes = []

            async def one():
                try:
                    # retries=0:不要讓自動重連的第二次嘗試把結果洗掉
                    outcomes.append(await pool._request(SRV, "call", ("t", {}), retries=0))
                except BaseException as e:   # noqa: BLE001
                    outcomes.append(e)

            async with anyio.create_task_group() as tg:
                for _ in range(5):
                    tg.start_soon(one)
            return outcomes

        outcomes, err, killed = anyio.run(_run_hub, _opener(session), body)
        self.assertFalse(killed)
        self.assertIsNone(err, "五個請求都該在期限內有結果")
        self.assertEqual(len(outcomes), 5)
        self.assertEqual(sum(1 for o in outcomes if o == "pong"), 4,
                         "一個失敗不該連帶弄掉另外四個")


class ToolListSurvivesABrokenDownstream(unittest.TestCase):
    """
    tools/list 連不上下游時要用快取,不能回空清單。

    Claude 等 tools/list 的耐心有限(Claude Code 給 30 秒),而「工具全部消失」
    對它來說跟 Hub 壞掉沒兩樣。
    """

    SLUG = "cachetest"

    def setUp(self):
        store.init_db()
        store.add_server(self.SLUG, "快取測試", "http://127.0.0.1:1/mcp",
                         auth_type="none", transport="http")
        store.set_server_enabled(self.SLUG, 1)
        store.cache_tools(self.SLUG, [
            ("echo", "回聲", {"type": "object", "properties": {"s": {"type": "string"}}}),
        ])

    def tearDown(self):
        store.delete_server(self.SLUG)

    def test_falls_back_to_cached_tools(self):
        from gateway import hub_server

        async def boom(server):
            raise ConnectionRefusedError("下游不在")

        original = pool.fetch_tools
        pool.fetch_tools = boom
        try:
            tools = anyio.run(hub_server.list_tools)
        finally:
            pool.fetch_tools = original

        names = [t.name for t in tools]
        self.assertIn(f"{self.SLUG}__echo", names,
                      "下游連不上時應該拿快取頂著,而不是讓工具消失")

    def test_broken_cached_schema_does_not_sink_the_whole_list(self):
        from gateway import hub_server
        self.assertEqual(
            hub_server._cached_schema({"input_schema": "{{not json"}),
            {"type": "object", "properties": {}})
        self.assertEqual(
            hub_server._cached_schema({"input_schema": None}),
            {"type": "object", "properties": {}})


class ErrorMessagesSaySomething(unittest.TestCase):
    def test_timeout_does_not_produce_an_empty_message(self):
        """
        逾時是最常見的失敗,而 str(TimeoutError()) 是空字串 —— 空訊息會一路
        傳到呼叫記錄跟 AI 看到的句子,變成「呼叫下游失敗:」。
        """
        self.assertTrue(hub.exception_details(TimeoutError()))
        self.assertIn("逾時", hub.exception_details(TimeoutError()))

    def test_exception_group_is_unwrapped(self):
        group = BaseExceptionGroup("unhandled errors in a TaskGroup",
                                   [RuntimeError("真正的原因")])
        self.assertEqual(hub.exception_details(group), "真正的原因")


if __name__ == "__main__":
    unittest.main()
