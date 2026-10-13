"""
下游連線池(C1)—— 常駐 session + 斷線重連。

關鍵教訓一:MCP 連線內部用 anyio task group,cancel scope 必須「同一 task、正確 LIFO 順序」進出。
    錯:AsyncExitStack 疊多個 session → 會違反順序,關閉時炸 cancel-scope。
    對:每條連線一個 task,用「自然的 async with 巢狀」持有 session,靠 channel 收發請求。

所以:
    · 每個下游 = 一個常駐 task(_conn_serve),task 內 async with 開著 session,重複使用。
    · 呼叫者透過該連線的 channel 送請求、等回覆。
    · 同一條連線上的請求是**併發**處理的,不排隊(見 _conn_serve)。
    · 呼叫出錯(通常斷線)→ 封住該連線 → 下次自動重建(重連)。

關鍵教訓二:連線 task 的例外絕對不能外漏。這是 Hub 會「三不五時斷線」的真正原因。

    連線 task 跟 hub_server 的 server.run() 是同一個 task group 的兄弟。anyio 的規則是
    「任一子 task 拋例外 → 整個 group 連同兄弟 task 一起取消」,所以下游只要吐一個
    HTTP 500,例外就會一路往上炸掉 server.run(),整個 Hub 行程結束。Claude 那端看到的
    依序是:這次工具呼叫永遠等不到回覆(Request timed out)、下一次呼叫
    Connection closed、接下來整場工作階段 Not connected。

    而且這不是假設性的風險 —— mcp SDK 的 streamable-http transport 用
    tg.start_soon(handle_request_async) 發 POST,裡面的 response.raise_for_status()
    沒有任何保護,下游回 5xx 就直接往它自己的 task group 拋,我們一定會收到。

    所以:連線 task 一旦對外宣告「啟動成功」,之後就只准自己消化例外、自己收攤。
    啟動階段的失敗要照常往上拋 —— 那是 _get() 在等的回報,不是意外。

關鍵教訓三:每一個排進來的請求都必須拿到答覆。
    連線 task 收攤時若把 channel buffer 裡的請求丟掉,呼叫者會永遠卡在等回覆上。
    對 Claude 來說那就是「工具呼叫沒有回應」,最後一樣是逾時斷線 —— 跟行程死掉
    的症狀幾乎分不出來,但更難查。所以收攤有固定順序,見 _retire()。
"""

import os
import json
import asyncio
from contextlib import asynccontextmanager

import anyio
from anyio import create_memory_object_stream

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

from gateway import console, hub
from gateway.config import (
    CONNECT_TIMEOUT, DOWNSTREAM_TIMEOUT, REPLY_TIMEOUT, TOTAL_TIMEOUT)

_conns = {}       # slug -> _Conn
_tg = None        # 由 hub_server 綁進來的 task group
_locks = {}       # slug -> anyio.Lock;建立連線時的互斥鎖


class _Conn:
    def __init__(self, server):
        self.server = server
        self.send, self.recv = create_memory_object_stream(32)
        self.alive = False
        self.sealed = False
        self.retired = False
        # 正在處理中的那些請求的回覆管道。它們已經離開 channel buffer 了,
        # 排空 buffer 補不到 —— 連線被取消時要靠這個集合補回覆。
        self.pending = set()
        # 這條連線是為什麼壞掉的。收攤時拿它當回給呼叫者的錯誤。
        self.reason = None


def bind(task_group):
    """hub_server 啟動時把它的 task group 綁進來,連線 task 都跑在裡面。"""
    global _tg
    _tg = task_group
    _locks.clear()


def _lock_for(slug):
    """
    每台下游一把鎖,不是全池共用一把。

    建立連線要換 OAuth 票、連 TCP、跑 initialize,最久 CONNECT_TIMEOUT 秒,
    而那段時間鎖是被握著的。共用一把鎖的話,一台連不上的下游會讓其他所有下游
    的呼叫一起排隊等它逾時。
    """
    lock = _locks.get(slug)
    if lock is None:
        lock = _locks[slug] = anyio.Lock()
    return lock


@asynccontextmanager
async def _open_session(server):
    """自然巢狀開一條連線(不用 AsyncExitStack)。"""
    if server.get("transport") == "stdio":
        params = StdioServerParameters(
            command=server.get("command") or "",
            args=json.loads(server.get("args") or "[]"),
            env={**os.environ, **json.loads(server.get("env") or "{}")},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), DOWNSTREAM_TIMEOUT)
                yield session
    else:
        kw = await hub.connect_kwargs(server)
        async with streamablehttp_client(
            server["base_url"],
            headers=kw.get("headers"),
            auth=kw.get("auth"),
            # 不傳的話 SDK 預設給 30 秒,比我們自己的逾時還長 —— 等於我們的逾時
            # 先到、連線被丟掉,而底層那個 POST 還在跑。
            timeout=DOWNSTREAM_TIMEOUT,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), DOWNSTREAM_TIMEOUT)
                yield session


async def _answer(reply, message):
    """
    回覆呼叫者。

    對方可能已經放棄了(Claude 取消了這次工具呼叫、或 _request 自己逾時),
    那是正常的事,不是錯誤 —— 更不該讓「回覆失敗」變成往上拋的例外。
    """
    try:
        await reply.send(message)
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        pass


async def _seal(conn):
    """
    封住連線:不再收新請求,但已經排進來的照樣處理完。

    這是「這條連線不可靠了」跟「這條連線結束了」之間的那一步。關掉寄件端之後,
    _request 的 send 會立刻拋 ClosedResourceError,它就會改抓一條新連線;而
    _conn_serve 的 async for 會把 buffer 裡剩下的請求讀完才收到 EndOfStream。
    所以沒有任何請求是被硬生生丟掉的。
    """
    if conn.sealed:
        return
    conn.sealed = True
    conn.alive = False
    slug = conn.server["slug"]
    if _conns.get(slug) is conn:
        _conns.pop(slug, None)
    try:
        await conn.send.aclose()
    except Exception:
        pass


async def _retire(conn, reason):
    """
    連線收攤。順序是關鍵,反了就會有請求永遠等不到回覆:

      1. 先封起來(關寄件端)—— 之後沒人能再把請求丟進沒人看的 buffer。
      2. 補上「處理中那些」的回覆。它們已經不在 buffer 裡了(見 _Conn.pending)。
      3. 再把還排在 buffer 裡的請求一一回錯。
         先關寄件端才排空,中間就沒有「剛好擠進來又被丟掉」的空隙。
      4. 最後關收件端。
    """
    if conn.retired:
        return
    conn.retired = True
    await _seal(conn)

    slug = conn.server["slug"]
    err = reason or conn.reason or ConnectionError(f"下游 {slug} 的連線已結束")

    pending, conn.pending = conn.pending, set()
    for reply in pending:
        await _answer(reply, ("err", err))

    while True:
        try:
            _, _, reply = conn.recv.receive_nowait()
        except (anyio.WouldBlock, anyio.EndOfStream, anyio.ClosedResourceError):
            break
        await _answer(reply, ("err", err))

    try:
        await conn.recv.aclose()
    except Exception:
        pass


async def _handle(conn, session, kind, payload, reply):
    """
    處理一個請求。

    絕不往外拋非取消的例外 —— 外漏會連帶取消同一條連線上其他還在跑的請求。
    """
    try:
        if kind == "list":
            res = (await asyncio.wait_for(session.list_tools(), DOWNSTREAM_TIMEOUT)).tools
        else:
            res = await asyncio.wait_for(
                session.call_tool(payload[0], payload[1] or {}), DOWNSTREAM_TIMEOUT)
    except Exception as e:
        # 逾時、或 session 壞了 → 這條連線不再可靠,封起來讓下次呼叫重建。
        # 不是硬拆:已經排進來的請求還是會被處理完。
        conn.reason = conn.reason or e
        await _answer(reply, ("err", e))
        conn.pending.discard(reply)
        await _seal(conn)
        return
    await _answer(reply, ("ok", res))
    # 回覆成功才從 pending 移除。被取消時留著,讓 _retire 去補回覆。
    conn.pending.discard(reply)


async def _conn_serve(conn, *, task_status=anyio.TASK_STATUS_IGNORED):
    """
    一條連線的常駐 task:持有 session,把它的請求併發送出去。

    併發而不是排隊:MCP 的 ClientSession 本來就支援同時多個請求(每個請求有
    自己的 id 和回覆管道),SDK 的 HTTP transport 也是每個 POST 一個 task。
    排隊是這裡自己加上去的限制,而代價很具體 —— Claude 一次批次打十幾個工具
    呼叫是常態,排隊會讓後面那幾個等到超過上限而失敗。
    """
    started = False
    try:
        async with _open_session(conn.server) as session:
            conn.alive = True
            task_status.started()
            started = True
            # 巢狀的 task group 在同一個 task 內進出,而且一定比 session 先關 ——
            # 所以處理中的請求都會跑完才關 session(見本檔開頭的關鍵教訓一)。
            async with anyio.create_task_group() as rtg:
                async for (kind, payload, reply) in conn.recv:
                    conn.pending.add(reply)
                    rtg.start_soon(_handle, conn, session, kind, payload, reply)
    except anyio.get_cancelled_exc_class():
        raise      # 關機、或 _get 的連線逾時把我們取消掉:照常往上
    except BaseException as e:
        # 下游、或 SDK transport 自己的 task group 拋出來的東西(常是 ExceptionGroup)。
        # started 之後絕對不能外漏 —— 外漏一次就會連帶取消 server.run(),整個 Hub 結束。
        if not started:
            raise  # 啟動階段的失敗:這是 _get() 在等的回報
        conn.reason = conn.reason or e
        console.eprint(f"[pool] 下游 {conn.server['slug']} 連線中斷(下次呼叫會自動重連):"
                       f"{hub.exception_details(e)}")
    finally:
        # 收攤要在取消中也跑得完 —— 否則排隊的請求會跟著連線一起消失。
        with anyio.CancelScope(shield=True):
            await _retire(conn, None)


async def _get(server):
    slug = server["slug"]
    async with _lock_for(slug):
        conn = _conns.get(slug)
        if conn is not None and conn.alive:
            return conn
        conn = _Conn(server)
        _conns[slug] = conn
        try:
            # 連線階段也要有上限。OAuth 換票跟 TCP 都可能卡住,而這段時間
            # Claude 是在等 tools/list 或 tools/call 的回應。
            with anyio.fail_after(CONNECT_TIMEOUT):
                await _tg.start(_conn_serve, conn)  # 等到 session 就緒(或連線失敗拋出)
        except BaseException:
            _conns.pop(slug, None)
            raise
        return conn


async def _request(server, kind, payload, retries=1):
    if _tg is None:
        raise RuntimeError("連線池尚未 bind task group")
    conn = None
    reply = None
    try:
        conn = await _get(server)
        reply = create_memory_object_stream(1)
        reply_send, reply_recv = reply
        await conn.send.send((kind, payload, reply_send))
        # 最後一道防線。正常情況下一定會有人回覆:成功、失敗、或連線收攤時補回錯誤。
        # 但「永遠等下去」的後果太嚴重(Claude 等不到回應就斷線),所以寧可多押一個
        # 上限。比單次操作的逾時寬鬆一點,留給建立連線的時間。
        with anyio.fail_after(REPLY_TIMEOUT):
            status, val = await reply_recv.receive()
        if status == "ok":
            return val
        err = val
    except Exception as e:
        err = e
    except BaseException:
        # 呼叫者被取消(Claude 取消了這次工具呼叫、或 list_tools 的總預算到了)。
        # 連線 task 可能還在處理我們剛送進去的請求,最久要再跑完 DOWNSTREAM_TIMEOUT。
        # 留著這條連線,下一個請求就會排在它後面白等 —— 一次取消拖累接下來每一次
        # 呼叫。所以也一併收掉,讓下一個請求拿一條乾淨的。
        if conn is not None:
            with anyio.CancelScope(shield=True):
                await _retire(conn, None)
        raise
    finally:
        # 關掉這次用的回覆管道。不關的話每個請求都留下一對沒收掉的 channel,
        # 而且連線 task 還會以為有人在等、把回覆寫進沒人看的 buffer。
        if reply is not None:
            with anyio.CancelScope(shield=True):
                for stream in reply:
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
    # 走到這裡代表失敗:收掉「我們剛剛用的那一條」。
    # 不能用 _conns 裡現在那一條 —— 期間可能已經有別人重建了一條好的,收掉它
    # 會連帶把別人排隊中的請求一起回錯。
    if conn is not None:
        await _retire(conn, err)
    # 逾時就別重試(只是再等一次逾時);其他(連線斷掉)才重連再試一次
    if retries > 0 and not isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return await _request(server, kind, payload, retries - 1)
    raise err


async def fetch_tools(server):
    with anyio.fail_after(TOTAL_TIMEOUT):
        return await _request(server, "list", None)


async def call_tool(server, name, arguments):
    # 總上限要套在最外層:_request 失敗時會重連再試一次,每一次都有自己的
    # 連線逾時跟回覆逾時,加起來會超過 Claude 等一次工具呼叫的耐心。
    with anyio.fail_after(TOTAL_TIMEOUT):
        return await _request(server, "call", (name, arguments))


async def stop():
    """優雅收尾:關掉每條連線的 channel → 各連線 task 自然結束、正確關閉 session。"""
    for conn in list(_conns.values()):
        try:
            await conn.send.aclose()
        except Exception:
            pass
