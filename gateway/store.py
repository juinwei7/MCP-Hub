"""
SQLite 儲存 —— MCP Server 程序和管理台程序共用的那份狀態。

主要表:
  servers          下游 MCP 清單(你在管理台加的)
  tools_cache      每台下游的工具快取(讓管理台不用連線就能顯示;含啟用/需確認開關)
  custom_tools     Hub 自己的 HTTP 自訂工具
  composite_tools  Hub 自己的複合工具(多步驟巨集)
  call_logs        每一次轉發呼叫的紀錄
  actions          v1 的核准票券(給「可選確認」用)
"""

import sqlite3
import json
import secrets
import datetime
import re
from contextlib import contextmanager

from gateway.config import DB_PATH
from gateway import crypto

# ── 票券狀態常數(v1) ──────────────────────────────
WAITING = "WAITING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXECUTED = "EXECUTED"


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def slugify(text):
    """把名稱轉成合法的 slug(只留英數與底線,當作工具命名空間前綴)。"""
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower()).strip("_")
    return s or "server"


def init_db():
    with _db() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS servers (
                slug         TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                base_url     TEXT NOT NULL DEFAULT '',
                auth_type    TEXT NOT NULL DEFAULT 'none',   -- none | bearer | oauth
                bearer_token TEXT,
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL
            )
            """
        )
        # 遷移:stdio 下游 + 健康監測欄位
        scols = [r["name"] for r in c.execute("PRAGMA table_info(servers)").fetchall()]
        for col, decl in [
            ("transport", "TEXT NOT NULL DEFAULT 'http'"),   # http | stdio
            ("command", "TEXT DEFAULT ''"),                   # stdio 啟動指令
            ("args", "TEXT DEFAULT '[]'"),                    # json:指令參數
            ("env", "TEXT DEFAULT '{}'"),                     # json:環境變數(可放金鑰)
            ("status", "TEXT DEFAULT 'unknown'"),             # ok | error | unknown
            ("status_detail", "TEXT DEFAULT ''"),
            ("checked_at", "TEXT DEFAULT ''"),
        ]:
            if col not in scols:
                c.execute(f"ALTER TABLE servers ADD COLUMN {col} {decl}")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tools_cache (
                server_slug   TEXT NOT NULL,
                name          TEXT NOT NULL,
                description   TEXT,
                input_schema  TEXT,                          -- json
                enabled       INTEGER NOT NULL DEFAULT 1,
                needs_confirm INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (server_slug, name)
            )
            """
        )
        # 遷移:工具描述覆寫(策展給 AI 看的描述)
        tcols = [r["name"] for r in c.execute("PRAGMA table_info(tools_cache)").fetchall()]
        if "desc_override" not in tcols:
            c.execute("ALTER TABLE tools_cache ADD COLUMN desc_override TEXT DEFAULT ''")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS call_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                time        TEXT NOT NULL,
                server_slug TEXT,
                tool        TEXT,
                arguments   TEXT,
                status      TEXT,          -- ok | error | blocked_need_confirm
                duration_ms INTEGER,
                error       TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id          TEXT PRIMARY KEY,
                tool        TEXT NOT NULL,
                arguments   TEXT NOT NULL,
                status      TEXT NOT NULL,
                result      TEXT,
                created_at  TEXT NOT NULL,
                decided_at  TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                server_slug TEXT PRIMARY KEY,
                client_info TEXT,   -- 動態註冊拿到的 client 資訊(json)
                tokens      TEXT    -- access/refresh token(json)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_tools (
                name          TEXT PRIMARY KEY,
                description   TEXT,
                method        TEXT NOT NULL DEFAULT 'GET',
                url_template  TEXT NOT NULL,
                headers       TEXT DEFAULT '{}',   -- json:靜態 header(可放金鑰)
                params        TEXT DEFAULT '[]',   -- json:參數清單 [{name,type,required,description}]
                enabled       INTEGER NOT NULL DEFAULT 1,
                needs_confirm INTEGER NOT NULL DEFAULT 0,
                group_name    TEXT NOT NULL DEFAULT ''
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS composite_tools (
                name          TEXT PRIMARY KEY,
                description   TEXT,
                params        TEXT DEFAULT '[]',
                steps         TEXT DEFAULT '[]',
                output        TEXT NOT NULL DEFAULT 'collect',
                enabled       INTEGER NOT NULL DEFAULT 1,
                needs_confirm INTEGER NOT NULL DEFAULT 0,
                group_name    TEXT NOT NULL DEFAULT ''
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_drafts (
                composite_name TEXT PRIMARY KEY,
                purpose        TEXT NOT NULL DEFAULT '',
                skill_md       TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
            """
        )
        # 內嵌 AI 已由 MCP 調適模式取代；移除舊設定與加密 API key。
        c.execute("DROP TABLE IF EXISTS ai_settings")
        # 遷移:舊 DB 補上 group_name 欄
        cols = [r["name"] for r in c.execute("PRAGMA table_info(custom_tools)").fetchall()]
        if "group_name" not in cols:
            c.execute("ALTER TABLE custom_tools ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
        ccols = [r["name"] for r in c.execute("PRAGMA table_info(composite_tools)").fetchall()]
        if "group_name" not in ccols:
            c.execute("ALTER TABLE composite_tools ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
        # 分類表(讓「空分類」也能存在)。group_name 即分類名,'' = 未分類。
        c.execute(
            "CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )
        # MCP 參考目錄(不自動安裝,只列表 + 一鍵建立未設定的下游讓你完成授權)
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS directory_entries (
                id        TEXT PRIMARY KEY,
                name      TEXT NOT NULL,
                kind      TEXT NOT NULL DEFAULT 'remote',   -- remote | local | ref
                transport TEXT NOT NULL DEFAULT 'http',     -- http | stdio
                base_url  TEXT DEFAULT '',
                command   TEXT DEFAULT '',
                args      TEXT DEFAULT '[]',                -- json
                auth      TEXT DEFAULT 'none',              -- oauth | token | none
                needs     TEXT DEFAULT '',                  -- 需求註記:Node.js / API token / 連線字串…
                doc_url   TEXT DEFAULT '',
                descr     TEXT DEFAULT '',
                builtin   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        for e in _DIRECTORY_SEED:
            # builtin 項以程式為準:每次啟動 upsert,讓種子的修改會更新到既有 DB。
            c.execute(
                "INSERT INTO directory_entries "
                "(id,name,kind,transport,base_url,command,args,auth,needs,doc_url,descr,builtin) "
                "VALUES (:id,:name,:kind,:transport,:base_url,:command,:args,:auth,:needs,:doc_url,:descr,1) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,transport=excluded.transport,"
                "base_url=excluded.base_url,command=excluded.command,args=excluded.args,auth=excluded.auth,"
                "needs=excluded.needs,doc_url=excluded.doc_url,descr=excluded.descr,builtin=1",
                {"kind": "remote", "transport": "http", "base_url": "", "command": "",
                 "args": "[]", "auth": "none", "needs": "", "doc_url": "", "descr": "", **e},
            )
    _encrypt_existing_secrets()   # 一次性:把既有明文密鑰補加密(冪等,已加密的跳過)


# 內建 MCP 目錄種子(URL/套件皆已驗證存在)。builtin 項每次啟動 INSERT OR IGNORE 補回。
_DIRECTORY_SEED = [
    # 遠端 hosted:貼 URL + OAuth 授權
    {"id": "github", "name": "GitHub", "kind": "remote", "transport": "http",
     "base_url": "https://api.githubcopilot.com/mcp/", "auth": "oauth",
     "descr": "GitHub 官方遠端 MCP:issue / PR / repo。"},
    {"id": "gitlab", "name": "GitLab", "kind": "remote", "transport": "http",
     "base_url": "https://gitlab.com/api/v4/mcp", "auth": "oauth",
     "descr": "GitLab 官方遠端 MCP。"},
    {"id": "sentry", "name": "Sentry", "kind": "remote", "transport": "http",
     "base_url": "https://mcp.sentry.dev/mcp", "auth": "oauth",
     "descr": "Sentry 錯誤追蹤:查 issue / 事件。"},
    {"id": "notion", "name": "Notion", "kind": "remote", "transport": "http",
     "base_url": "https://mcp.notion.com/mcp", "auth": "oauth",
     "descr": "Notion 頁面 / 資料庫。"},
    {"id": "linear", "name": "Linear", "kind": "remote", "transport": "http",
     "base_url": "https://mcp.linear.app/mcp", "auth": "oauth",
     "descr": "Linear 議題追蹤。"},
    # Google 家:只吃 OAuth 且不支援自動註冊(DCR)→ Hub 接不了,請用 Claude 內建連接器
    {"id": "google-calendar", "name": "Google Calendar", "kind": "ref",
     "base_url": "https://calendarmcp.googleapis.com/mcp/v1", "auth": "oauth", "needs": "Claude 連接器",
     "descr": "Google 日曆。只支援 Google OAuth、不支援自動註冊 → 請用 Claude 內建連接器,Hub 不接。"},
    {"id": "gmail", "name": "Gmail", "kind": "ref",
     "base_url": "https://gmailmcp.googleapis.com/mcp/v1", "auth": "oauth", "needs": "Claude 連接器",
     "descr": "Gmail 郵件。只支援 Google OAuth、不支援自動註冊 → 請用 Claude 內建連接器,Hub 不接。"},
    # 本機 stdio:需 Node(npx)
    {"id": "filesystem", "name": "Filesystem", "kind": "local", "transport": "stdio",
     "command": "npx", "args": '["-y","@modelcontextprotocol/server-filesystem","."]',
     "auth": "none", "needs": "Node.js",
     "descr": "讀寫本機檔案。args 最後一項改成你要開放的目錄。"},
    {"id": "playwright", "name": "Playwright", "kind": "local", "transport": "stdio",
     "command": "npx", "args": '["-y","@playwright/mcp"]', "auth": "none", "needs": "Node.js",
     "descr": "瀏覽器自動化(Microsoft)。"},
    # 參考:需連線字串 / 社群版,不預填(避免給錯設定)
    {"id": "postgres", "name": "PostgreSQL / MySQL", "kind": "ref", "auth": "token",
     "needs": "連線字串", "doc_url": "https://github.com/modelcontextprotocol/servers",
     "descr": "資料庫查詢。需連線字串,套件多為社群版,請自行確認後用「新增下游」加入。"},
    {"id": "redis", "name": "Redis", "kind": "ref", "auth": "token",
     "needs": "連線字串", "doc_url": "https://github.com/redis/mcp-redis",
     "descr": "Redis 存取。需連線字串。"},
    {"id": "slack", "name": "Slack / Discord", "kind": "ref", "auth": "token",
     "needs": "API token", "doc_url": "https://github.com/modelcontextprotocol/servers",
     "descr": "團隊通訊。官方舊套件已棄用,現多為社群版 + 需 token。"},
]


def _encrypt_existing_secrets():
    """把 DB 裡尚未加密的密鑰欄位就地加密。冪等:enc() 對已加密 / 空值不動作。"""
    with _db() as c:
        for r in c.execute("SELECT slug, bearer_token, env FROM servers").fetchall():
            bt, ev = crypto.enc(r["bearer_token"]), crypto.enc(r["env"])
            if bt != r["bearer_token"] or ev != r["env"]:
                c.execute("UPDATE servers SET bearer_token=?, env=? WHERE slug=?", (bt, ev, r["slug"]))
        for r in c.execute("SELECT name, headers FROM custom_tools").fetchall():
            h = crypto.enc(r["headers"])
            if h != r["headers"]:
                c.execute("UPDATE custom_tools SET headers=? WHERE name=?", (h, r["name"]))
        for r in c.execute("SELECT server_slug, client_info, tokens FROM oauth_tokens").fetchall():
            ci, tk = crypto.enc(r["client_info"]), crypto.enc(r["tokens"])
            if ci != r["client_info"] or tk != r["tokens"]:
                c.execute("UPDATE oauth_tokens SET client_info=?, tokens=? WHERE server_slug=?",
                          (ci, tk, r["server_slug"]))
# ── Skill drafts ─────────────────────────────────────────
def get_skill_draft(composite_name):
    with _db() as c:
        row = c.execute("SELECT * FROM skill_drafts WHERE composite_name=?", (composite_name,)).fetchone()
    return dict(row) if row else None


def save_skill_draft(composite_name, skill_md, purpose=""):
    with _db() as c:
        c.execute(
            "INSERT INTO skill_drafts (composite_name, purpose, skill_md, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(composite_name) DO UPDATE SET purpose=excluded.purpose, skill_md=excluded.skill_md, updated_at=excluded.updated_at",
            (composite_name, purpose, skill_md, _now()),
        )


# ── MCP 參考目錄 ─────────────────────────────────────────
def list_directory_entries():
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM directory_entries ORDER BY builtin DESC, kind, name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_directory_entry(entry_id):
    with _db() as c:
        row = c.execute("SELECT * FROM directory_entries WHERE id=?", (entry_id,)).fetchone()
    return dict(row) if row else None


def add_directory_entry(entry_id, name, kind, transport, base_url, command, args_json, auth, needs, doc_url, descr):
    with _db() as c:
        c.execute(
            "INSERT INTO directory_entries "
            "(id,name,kind,transport,base_url,command,args,auth,needs,doc_url,descr,builtin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,0) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,transport=excluded.transport,"
            "base_url=excluded.base_url,command=excluded.command,args=excluded.args,auth=excluded.auth,"
            "needs=excluded.needs,doc_url=excluded.doc_url,descr=excluded.descr",
            (entry_id, name, kind, transport, base_url, command, args_json, auth, needs, doc_url, descr),
        )


def delete_directory_entry(entry_id):
    """只能刪自訂項;builtin 項刪了下次啟動會補回。"""
    with _db() as c:
        c.execute("DELETE FROM directory_entries WHERE id=? AND builtin=0", (entry_id,))


# ── servers ───────────────────────────────────────────────
def add_server(slug, name, base_url, auth_type="none", bearer_token=None,
               transport="http", command="", args_json="[]", env_json="{}"):
    with _db() as c:
        c.execute(
            "INSERT INTO servers (slug, name, base_url, auth_type, bearer_token, enabled, created_at, "
            "transport, command, args, env) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (slug, name, base_url, auth_type, crypto.enc(bearer_token), _now(),
             transport, command, args_json, crypto.enc(env_json)),
        )


def update_server(slug, name, base_url, auth_type, bearer_token, transport, command, args_json, env_json):
    with _db() as c:
        c.execute(
            "UPDATE servers SET name=?, base_url=?, auth_type=?, bearer_token=?, "
            "transport=?, command=?, args=?, env=? WHERE slug=?",
            (name, base_url, auth_type, crypto.enc(bearer_token),
             transport, command, args_json, crypto.enc(env_json), slug),
        )


def _dec_server(d):
    """解密下游 dict 裡的密鑰欄位(bearer_token、env),供上層透明使用。"""
    if d is None:
        return None
    d["bearer_token"] = crypto.dec(d.get("bearer_token"))
    d["env"] = crypto.dec(d.get("env")) or "{}"
    return d


def set_server_status(slug, status, detail=""):
    with _db() as c:
        c.execute(
            "UPDATE servers SET status = ?, status_detail = ?, checked_at = ? WHERE slug = ?",
            (status, detail, _now(), slug),
        )


def list_servers():
    with _db() as c:
        rows = c.execute("SELECT * FROM servers ORDER BY created_at").fetchall()
    return [_dec_server(dict(r)) for r in rows]


def get_server(slug):
    with _db() as c:
        row = c.execute("SELECT * FROM servers WHERE slug = ?", (slug,)).fetchone()
    return _dec_server(dict(row)) if row else None


def enabled_servers():
    with _db() as c:
        rows = c.execute("SELECT * FROM servers WHERE enabled = 1").fetchall()
    return [_dec_server(dict(r)) for r in rows]


def set_server_enabled(slug, enabled):
    with _db() as c:
        c.execute("UPDATE servers SET enabled = ? WHERE slug = ?", (1 if enabled else 0, slug))


def delete_server(slug):
    with _db() as c:
        c.execute("DELETE FROM servers WHERE slug = ?", (slug,))
        c.execute("DELETE FROM tools_cache WHERE server_slug = ?", (slug,))
        c.execute("DELETE FROM oauth_tokens WHERE server_slug = ?", (slug,))


# ── tools_cache ───────────────────────────────────────────
def cache_tools(server_slug, tools):
    """tools = list[(name, description, input_schema_dict)];整批取代該 server 的快取。"""
    with _db() as c:
        # 保留既有的 enabled / needs_confirm 設定,不因重新整理而被重置
        existing = {
            r["name"]: (r["enabled"], r["needs_confirm"])
            for r in c.execute(
                "SELECT name, enabled, needs_confirm FROM tools_cache WHERE server_slug = ?",
                (server_slug,),
            ).fetchall()
        }
        c.execute("DELETE FROM tools_cache WHERE server_slug = ?", (server_slug,))
        for name, desc, schema in tools:
            en, nc = existing.get(name, (1, 0))
            c.execute(
                "INSERT INTO tools_cache (server_slug, name, description, input_schema, enabled, needs_confirm) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (server_slug, name, desc, json.dumps(schema, ensure_ascii=False), en, nc),
            )


def list_cached_tools(server_slug):
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM tools_cache WHERE server_slug = ? ORDER BY name", (server_slug,)
        ).fetchall()
    return [dict(r) for r in rows]


def count_tools(server_slug):
    with _db() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM tools_cache WHERE server_slug = ?", (server_slug,)
        ).fetchone()
    return row["n"]


def set_tool_flag(server_slug, name, *, enabled=None, needs_confirm=None):
    sets, params = [], []
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if needs_confirm is not None:
        sets.append("needs_confirm = ?"); params.append(1 if needs_confirm else 0)
    if not sets:
        return
    params += [server_slug, name]
    with _db() as c:
        c.execute(f"UPDATE tools_cache SET {', '.join(sets)} WHERE server_slug = ? AND name = ?", params)


def set_all_tools_enabled(server_slug, enabled):
    """整台下游的工具一次全開 / 全關。"""
    with _db() as c:
        c.execute("UPDATE tools_cache SET enabled = ? WHERE server_slug = ?",
                  (1 if enabled else 0, server_slug))


def set_tool_desc(server_slug, name, desc):
    with _db() as c:
        c.execute("UPDATE tools_cache SET desc_override = ? WHERE server_slug = ? AND name = ?",
                  (desc, server_slug, name))


def get_tool(server_slug, name):
    with _db() as c:
        row = c.execute(
            "SELECT * FROM tools_cache WHERE server_slug = ? AND name = ?", (server_slug, name)
        ).fetchone()
    return dict(row) if row else None


# ── call_logs ─────────────────────────────────────────────
def log_call(server_slug, tool, arguments, status, duration_ms=None, error=None):
    with _db() as c:
        c.execute(
            "INSERT INTO call_logs (time, server_slug, tool, arguments, status, duration_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), server_slug, tool, json.dumps(arguments, ensure_ascii=False), status, duration_ms, error),
        )


def list_logs(limit=100, offset=0):
    with _db() as c:
        rows = c.execute("SELECT * FROM call_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                         (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def count_logs(errors_only=False):
    q = "SELECT COUNT(*) AS n FROM call_logs"
    if errors_only:
        q += " WHERE status = 'error'"
    with _db() as c:
        return c.execute(q).fetchone()["n"]


# ── actions(v1 票券,可選確認用) ────────────────────────
def create_action(tool, arguments):
    action_id = secrets.token_urlsafe(8)
    with _db() as c:
        c.execute(
            "INSERT INTO actions (id, tool, arguments, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (action_id, tool, json.dumps(arguments, ensure_ascii=False), WAITING, _now()),
        )
    return action_id


def get_action(action_id):
    with _db() as c:
        row = c.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        return None
    action = dict(row)
    action["arguments"] = json.loads(action["arguments"])
    action["result"] = json.loads(action["result"]) if action["result"] else None
    return action


def list_actions():
    with _db() as c:
        rows = c.execute("SELECT * FROM actions ORDER BY created_at DESC").fetchall()
    out = []
    for row in rows:
        a = dict(row)
        a["arguments"] = json.loads(a["arguments"])
        out.append(a)
    return out


def decide(action_id, approve):
    new_status = APPROVED if approve else REJECTED
    with _db() as c:
        c.execute(
            "UPDATE actions SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
            (new_status, _now(), action_id, WAITING),
        )


def save_result(action_id, result):
    with _db() as c:
        c.execute(
            "UPDATE actions SET status = ?, result = ? WHERE id = ?",
            (EXECUTED, json.dumps(result, ensure_ascii=False), action_id),
        )


# ── oauth_tokens(OAuth 授權用,給 auth.SqliteTokenStorage) ──
def _upsert_oauth(slug, column, value):
    with _db() as c:
        c.execute(
            f"INSERT INTO oauth_tokens (server_slug, {column}) VALUES (?, ?) "
            f"ON CONFLICT(server_slug) DO UPDATE SET {column} = excluded.{column}",
            (slug, value),
        )


def set_oauth_client_info(slug, info_json):
    _upsert_oauth(slug, "client_info", crypto.enc(info_json))


def get_oauth_client_info(slug):
    with _db() as c:
        row = c.execute("SELECT client_info FROM oauth_tokens WHERE server_slug = ?", (slug,)).fetchone()
    return crypto.dec(row["client_info"]) or None if row and row["client_info"] else None


def set_oauth_tokens(slug, tokens_json):
    _upsert_oauth(slug, "tokens", crypto.enc(tokens_json))


def get_oauth_tokens(slug):
    with _db() as c:
        row = c.execute("SELECT tokens FROM oauth_tokens WHERE server_slug = ?", (slug,)).fetchone()
    return crypto.dec(row["tokens"]) or None if row and row["tokens"] else None


def clear_oauth(slug):
    """清除一台下游的 OAuth client 與 token，下次連線會重走 DCR。"""
    with _db() as c:
        c.execute("DELETE FROM oauth_tokens WHERE server_slug = ?", (slug,))


# ── custom_tools(自訂工具:把 HTTP API 包成 MCP 工具) ──────
def _custom_row(row):
    d = dict(row)
    d["headers"] = json.loads(crypto.dec(d["headers"]) or "{}")   # headers 可能含 API 金鑰
    d["params"] = json.loads(d["params"] or "[]")
    return d


def upsert_custom_tool(name, description, method, url_template, headers_json, params_json, group_name=""):
    with _db() as c:
        c.execute(
            """
            INSERT INTO custom_tools (name, description, method, url_template, headers, params, group_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description, method=excluded.method,
                url_template=excluded.url_template, headers=excluded.headers,
                params=excluded.params, group_name=excluded.group_name
            """,
            (name, description, method, url_template, crypto.enc(headers_json), params_json, group_name),
        )


def list_custom_tools():
    with _db() as c:
        rows = c.execute("SELECT * FROM custom_tools ORDER BY name").fetchall()
    return [_custom_row(r) for r in rows]


def get_custom_tool(name):
    with _db() as c:
        row = c.execute("SELECT * FROM custom_tools WHERE name = ?", (name,)).fetchone()
    return _custom_row(row) if row else None


def enabled_custom_tools():
    with _db() as c:
        rows = c.execute("SELECT * FROM custom_tools WHERE enabled = 1").fetchall()
    return [_custom_row(r) for r in rows]


def delete_custom_tool(name):
    with _db() as c:
        c.execute("DELETE FROM custom_tools WHERE name = ?", (name,))


def delete_custom_tools(names):
    if not names:
        return
    with _db() as c:
        c.executemany("DELETE FROM custom_tools WHERE name = ?", [(n,) for n in names])


def delete_custom_tools_by_group(group_name):
    with _db() as c:
        c.execute("DELETE FROM custom_tools WHERE group_name = ?", (group_name,))


def set_custom_tools_enabled(names, enabled):
    if not names:
        return
    with _db() as c:
        c.executemany(
            "UPDATE custom_tools SET enabled = ? WHERE name = ?",
            [(1 if enabled else 0, n) for n in names],
        )


# ── categories(分類) ─────────────────────────────────────
def backfill_github_category():
    """一次性:把之前誤匯入、還在未分類的 GitHub 工具歸到「GitHub」分類。"""
    with _db() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM custom_tools "
            "WHERE group_name = '' AND url_template LIKE '%api.github.com%'"
        ).fetchone()
        if row["n"]:
            c.execute("INSERT OR IGNORE INTO categories (name, created_at) VALUES ('GitHub', ?)", (_now(),))
            c.execute(
                "UPDATE custom_tools SET group_name = 'GitHub' "
                "WHERE group_name = '' AND url_template LIKE '%api.github.com%'"
            )


def create_category(name):
    name = (name or "").strip()
    if not name:
        return
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO categories (name, created_at) VALUES (?, ?)", (name, _now()))


def _category_names():
    with _db() as c:
        rows = c.execute("SELECT name FROM categories ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def category_overview():
    """回傳 (named_categories, uncategorized_or_None)。每筆:{name,total,enabled}。'' = 未分類。"""
    agg = {}
    for t in list_custom_tools():
        g = t["group_name"] or ""
        a = agg.setdefault(g, {"name": g, "total": 0, "enabled": 0})
        a["total"] += 1
        if t["enabled"]:
            a["enabled"] += 1
    for name in _category_names():           # 空分類也要出現
        agg.setdefault(name, {"name": name, "total": 0, "enabled": 0})
    named = sorted((v for k, v in agg.items() if k != ""), key=lambda v: v["name"].lower())
    return named, agg.get("")


def list_tools_in_category(name):
    return [t for t in list_custom_tools() if (t["group_name"] or "") == name]


def delete_category(name):
    """刪分類 + 連同裡面的工具一起刪(依使用者選擇)。"""
    with _db() as c:
        c.execute("DELETE FROM custom_tools WHERE group_name = ?", (name,))
        c.execute("DELETE FROM categories WHERE name = ?", (name,))


def set_category_enabled(name, enabled):
    with _db() as c:
        c.execute("UPDATE custom_tools SET enabled = ? WHERE group_name = ?", (1 if enabled else 0, name))


def set_tools_category(names, category):
    category = (category or "").strip()
    if category:
        create_category(category)
    if not names:
        return
    with _db() as c:
        c.executemany("UPDATE custom_tools SET group_name = ? WHERE name = ?", [(category, n) for n in names])


def set_category_headers(category, headers_json):
    """把同一組 headers(例如金鑰)套到整個分類的所有自訂工具。"""
    with _db() as c:
        c.execute("UPDATE custom_tools SET headers = ? WHERE group_name = ?",
                  (crypto.enc(headers_json), category))


def set_custom_tool_flag(name, *, enabled=None, needs_confirm=None):
    sets, params = [], []
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if needs_confirm is not None:
        sets.append("needs_confirm = ?"); params.append(1 if needs_confirm else 0)
    if not sets:
        return
    params.append(name)
    with _db() as c:
        c.execute(f"UPDATE custom_tools SET {', '.join(sets)} WHERE name = ?", params)


# ── composite_tools(複合工具:依序執行多個 Hub 工具) ──────
def _composite_row(row):
    d = dict(row)
    d["params"] = json.loads(d["params"] or "[]")
    d["steps"] = json.loads(d["steps"] or "[]")
    return d


def upsert_composite_tool(name, description, params_json, steps_json, output="collect", group_name=""):
    with _db() as c:
        c.execute(
            """
            INSERT INTO composite_tools (name, description, params, steps, output, group_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                params=excluded.params,
                steps=excluded.steps,
                output=excluded.output,
                group_name=excluded.group_name
            """,
            (name, description, params_json, steps_json, output, group_name),
        )


def list_composite_tools():
    with _db() as c:
        rows = c.execute("SELECT * FROM composite_tools ORDER BY name").fetchall()
    return [_composite_row(r) for r in rows]


def get_composite_tool(name):
    with _db() as c:
        row = c.execute("SELECT * FROM composite_tools WHERE name = ?", (name,)).fetchone()
    return _composite_row(row) if row else None


def enabled_composite_tools():
    with _db() as c:
        rows = c.execute("SELECT * FROM composite_tools WHERE enabled = 1").fetchall()
    return [_composite_row(r) for r in rows]


def delete_composite_tool(name):
    with _db() as c:
        c.execute("DELETE FROM composite_tools WHERE name = ?", (name,))
        c.execute("DELETE FROM skill_drafts WHERE composite_name = ?", (name,))


def set_composite_tool_flag(name, *, enabled=None, needs_confirm=None):
    sets, params = [], []
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if needs_confirm is not None:
        sets.append("needs_confirm = ?"); params.append(1 if needs_confirm else 0)
    if not sets:
        return
    params.append(name)
    with _db() as c:
        c.execute(f"UPDATE composite_tools SET {', '.join(sets)} WHERE name = ?", params)
