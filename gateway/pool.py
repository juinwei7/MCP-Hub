"""
下游連線池(C1)—— 常駐 session + 斷線重連。

關鍵教訓:MCP 連線內部用 anyio task group,cancel scope 必須「同一 task、正確 LIFO 順序」進出。
    ❌ AsyncExitStack 疊多個 session → 會違反順序,關閉時炸 cancel-scope。
    ✅ 每條連線一個 task,用「自然的 async with 巢狀」持有 session,靠 channel 收發請求。

所以:
    · 每個下游 = 一個常駐 task(_conn_serve),task 內 async with 開著 session,重複使用。
    · 呼叫者透過該連線的 channel 送請求、等回覆。
    · 呼叫出錯(通常斷線)→ 該 task 結束 → 下次自動重建(重連)。
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

from gateway import hub
from gateway.config import DOWNSTREAM_TIMEOUT

_conns = {}       # slug -> _Conn
_tg = None        # 由 hub_server 綁進來的 task group
_lock = None      # 建立連線時的互斥鎖


class _Conn:
    def __init__(self, server):
        self.server = server
        self.send, self.recv = create_memory_object_stream(32)
        self.alive = False


def bind(task_group):
    """hub_server 啟動時把它的 task group 綁進來,連線 task 都跑在裡面。"""
    global _tg, _lock
    _tg = task_group
    _lock = anyio.Lock()


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
            server["base_url"], headers=kw.get("headers"), auth=kw.get("auth")
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), DOWNSTREAM_TIMEOUT)
                yield session


async def _conn_serve(conn, *, task_status=anyio.TASK_STATUS_IGNORED):
    """一條連線的常駐 task:持有 session,序列處理它的請求。"""
    slug = conn.server["slug"]
    try:
        async with _open_session(conn.server) as session:
            conn.alive = True
            task_status.started()
            async for (kind, payload, reply) in conn.recv:
                try:
                    if kind == "list":
                        res = (await asyncio.wait_for(session.list_tools(), DOWNSTREAM_TIMEOUT)).tools
                    else:
                        res = await asyncio.wait_for(
                            session.call_tool(payload[0], payload[1] or {}), DOWNSTREAM_TIMEOUT)
                    await reply.send(("ok", res))
                except Exception as e:
                    await reply.send(("err", e))
                    break  # 逾時或連線壞了 → 結束此 task,下次重建
    finally:
        conn.alive = False
        if _conns.get(slug) is conn:
            _conns.pop(slug, None)


async def _get(server):
    slug = server["slug"]
    async with _lock:
        conn = _conns.get(slug)
        if conn is not None and conn.alive:
            return conn
        conn = _Conn(server)
        _conns[slug] = conn
        await _tg.start(_conn_serve, conn)  # 等到 session 就緒(或連線失敗拋出)
        return conn


async def _request(server, kind, payload, retries=1):
    if _tg is None:
        raise RuntimeError("連線池尚未 bind task group")
    try:
        conn = await _get(server)
        reply_send, reply_recv = create_memory_object_stream(1)
        await conn.send.send((kind, payload, reply_send))
        status, val = await reply_recv.receive()
        if status == "ok":
            return val
        err = val
    except Exception as e:
        err = e
    # 走到這裡代表失敗:丟掉連線
    _conns.pop(server["slug"], None)
    # 逾時就別重試(只是再等一次逾時);其他(連線斷掉)才重連再試一次
    if retries > 0 and not isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return await _request(server, kind, payload, retries - 1)
    raise err


async def fetch_tools(server):
    return await _request(server, "list", None)


async def call_tool(server, name, arguments):
    return await _request(server, "call", (name, arguments))


async def stop():
    """優雅收尾:關掉每條連線的 channel → 各連線 task 自然結束、正確關閉 session。"""
    for conn in list(_conns.values()):
        try:
            await conn.send.aclose()
        except Exception:
            pass
