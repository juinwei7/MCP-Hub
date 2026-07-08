"""
Hub 的下游連線層 —— Hub 當 MCP *client* 去連遠端/本機的 MCP server。

支援兩種下游:
    http  → streamablehttp_client(遠端 URL;授權:無 / bearer / oauth)
    stdio → stdio_client(啟動本機指令,像 Claude Desktop 設定的 command/args/env)

連線策略仍是「每次操作開一條」(對 http 沒問題;對 stdio 每次會啟動子程序,
正確但非最省——常駐連線池是之後的優化)。搭配管理台的背景健康檢查,
掛掉的下游恢復後會自動變綠、呼叫自動恢復。
"""

import os
import json
from contextlib import asynccontextmanager

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


def headers_for(server):
    """靜態 bearer token → Authorization header;其餘 None。"""
    if server.get("auth_type") == "bearer" and server.get("bearer_token"):
        return {"Authorization": f"Bearer {server['bearer_token']}"}
    return None


def connect_kwargs(server):
    """http 下游的連線參數(headers / auth)。"""
    if server.get("auth_type") == "oauth":
        from gateway import auth  # 延遲匯入避免循環
        return {"auth": auth.provider_for(server)}
    headers = headers_for(server)
    return {"headers": headers} if headers else {}


@asynccontextmanager
async def _http_session(url, headers=None, auth=None):
    async with streamablehttp_client(url, headers=headers, auth=auth) as (read, write, _get_sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def _stdio_session(command, args, env):
    params = StdioServerParameters(command=command, args=args, env={**os.environ, **env})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def _session(server):
    """依 transport 開一條已 initialize 的 session。"""
    if server.get("transport") == "stdio":
        async with _stdio_session(
            server.get("command") or "",
            json.loads(server.get("args") or "[]"),
            json.loads(server.get("env") or "{}"),
        ) as s:
            yield s
    else:
        kw = connect_kwargs(server)
        async with _http_session(server["base_url"], headers=kw.get("headers"), auth=kw.get("auth")) as s:
            yield s


async def fetch_tools(server):
    """回傳下游的工具清單(list[Tool])。"""
    async with _session(server) as s:
        return (await s.list_tools()).tools


async def call_tool(server, name, arguments):
    """把一次工具呼叫轉發給下游,回傳 CallToolResult。"""
    async with _session(server) as s:
        return await s.call_tool(name, arguments or {})


async def check_server(server):
    """健康檢查:回傳 (status, detail, tools_or_None)。"""
    try:
        tools = await fetch_tools(server)
        return "ok", f"{len(tools)} 個工具", tools
    except Exception as e:
        return "error", str(e)[:200], None
