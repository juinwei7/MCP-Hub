"""
把 Hub 接進各家 AI client 的設定檔。

在這之前只能「複製指令、貼到終端機」。那條路最容易出錯的地方是那三個環境變數 ——
少了它們,聚合器會去讀另一份空的資料庫,而且不會有任何錯誤訊息,只是
「工具怎麼都沒出現」。但那三個值後端自己最清楚(它就是用這份設定在跑的),
所以根本不該讓使用者轉手。

支援 Claude Desktop、Claude Code、Codex CLI。

寫別人的設定檔是有風險的動作,所以三條規則貫穿整個模組:
  1. 只碰自己那一段,其餘原樣保留 —— 包括我們不認得的欄位
  2. 寫之前先備份
  3. 原子寫入(寫暫存檔再 rename),中途當掉不會留下被截斷的設定檔
"""

import json
import os
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from gateway import config

# 在對方設定裡的名字。改這個會讓既有的安裝變成孤兒,所以固定。
SERVER_NAME = "my-hub"

# 我們寫的註解都帶這個前綴。移除時靠它認出「這是我寫的」——
# 沒有標記的話只能猜,而猜錯就是刪掉使用者自己的註解。
MARKER = "# [MCP Hub]"


# ── 這台機器上該用的啟動參數 ──────────────────────────────

def launch_spec():
    """
    聚合器的啟動方式。

    全部從「正在跑的這個後端」推導,不讓呼叫端傳 —— 呼叫端傳錯的那一刻,
    使用者就會得到一個指向錯誤資料庫的安裝,而且看不出哪裡錯。
    """
    repo = str(Path(__file__).resolve().parent.parent)
    return {
        "command": sys.executable,
        "args": ["-m", "gateway.hub_server"],
        "env": {
            "PYTHONPATH": repo,
            "MCP_HUB_DB": str(config.DB_PATH),
            "MCP_HUB_KEY": str(config.SECRET_KEY_PATH),
        },
    }


# ── 各家 client 的設定檔位置 ──────────────────────────────

def _claude_desktop_path():
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else None
    return home / ".config/Claude/claude_desktop_config.json"


CLIENTS = {
    "claude-desktop": {
        "label": "Claude Desktop",
        "format": "json",
        "path": _claude_desktop_path,
        # 設定檔不存在不代表沒裝 —— Claude Desktop 要到第一次改設定才建立它。
        # 所以改看應用程式本身在不在。
        "detect": lambda: _app_installed("Claude"),
        "note": "改完要重啟 Claude Desktop 才會生效。",
    },
    "claude-code": {
        "label": "Claude Code",
        "format": "json",
        "path": lambda: Path.home() / ".claude.json",
        "detect": lambda: (Path.home() / ".claude.json").exists()
                          or shutil.which("claude") is not None,
        "note": "下次開新的 Claude Code 工作階段就會生效。",
    },
    "codex": {
        "label": "Codex CLI",
        "format": "toml",
        "path": lambda: Path.home() / ".codex" / "config.toml",
        "detect": lambda: (Path.home() / ".codex").exists()
                          or shutil.which("codex") is not None,
        "note": "下次開新的 Codex 工作階段就會生效。",
    },
}


def _app_installed(name):
    """桌面應用程式在不在。只看標準安裝位置,不去掃整台機器。"""
    if sys.platform == "darwin":
        return Path(f"/Applications/{name}.app").exists() \
            or (Path.home() / f"Applications/{name}.app").exists()
    if sys.platform.startswith("win"):
        for var in ("LOCALAPPDATA", "PROGRAMFILES"):
            base = os.environ.get(var)
            if base and (Path(base) / name).exists():
                return True
        return False
    return shutil.which(name.lower()) is not None


# ── 狀態 ──────────────────────────────────────────────────

def status():
    """每個 client 現在是什麼狀態。不寫入任何東西。"""
    spec = launch_spec()
    out = []
    for key, c in CLIENTS.items():
        path = c["path"]()
        installed, current = (False, None) if path is None else _read_entry(path, c["format"])
        out.append({
            "id": key,
            "label": c["label"],
            "path": str(path) if path else "",
            "detected": bool(c["detect"]()),
            "installed": installed,
            # 已安裝但參數對不上 —— 通常是 app 搬過位置或換了資料目錄。
            # 這種情況比「沒安裝」更危險,因為使用者以為它在動。
            "stale": installed and current is not None and not _same(current, spec),
            "note": c["note"],
        })
    return out


def _same(current, spec):
    if current.get("command") != spec["command"]:
        return False
    if list(current.get("args") or []) != spec["args"]:
        return False
    env = current.get("env") or {}
    return all(env.get(k) == v for k, v in spec["env"].items())


def _read_entry(path, fmt):
    """回 (有沒有我們這一段, 那一段的內容)。檔案不存在或壞掉都當成沒安裝。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, None
    try:
        data = json.loads(text) if fmt == "json" else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError):
        return False, None
    table = data.get("mcpServers" if fmt == "json" else "mcp_servers") or {}
    entry = table.get(SERVER_NAME)
    return (entry is not None), (entry if isinstance(entry, dict) else None)


# ── 安裝 / 移除 ───────────────────────────────────────────

class ClientError(Exception):
    """可以直接顯示給使用者看的失敗原因。"""


def install(client_id):
    c = _client(client_id)
    path = c["path"]()
    if path is None:
        raise ClientError(f"這個平台上找不到 {c['label']} 的設定位置。")
    spec = launch_spec()
    if c["format"] == "json":
        _write_json(path, spec)
    else:
        _write_toml(path, spec)
    return {"path": str(path), "note": c["note"]}


def uninstall(client_id):
    c = _client(client_id)
    path = c["path"]()
    if path is None or not path.exists():
        return {"path": str(path) if path else "", "note": ""}
    if c["format"] == "json":
        _write_json(path, None)
    else:
        _write_toml(path, None)
    return {"path": str(path), "note": c["note"]}


def _client(client_id):
    c = CLIENTS.get(client_id)
    if c is None:
        raise ClientError(f"不認得的 client:{client_id}")
    return c


def _backup(path):
    """
    寫之前留一份。這是別人的設定檔 —— 我們有 bug 的話,受害的是他們的工作環境。
    只留一份(固定檔名),不累積成一堆垃圾。
    """
    if not path.exists():
        return
    try:
        shutil.copy2(path, path.with_suffix(path.suffix + ".mcphub-backup"))
    except OSError:
        pass   # 備份失敗不該擋住主要操作,但也不假裝備份過了


def _atomic_write(path, text):
    """寫暫存檔再 rename。中途當掉時,對方看到的是舊檔而不是半截的新檔。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".mcphub-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ClientError(f"寫入 {path} 失敗:{e}") from e


def _write_json(path, spec):
    """spec 為 None 代表移除。其餘欄位一律原樣保留。"""
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            raise ClientError(f"讀不到 {path}:{e}") from e
        except json.JSONDecodeError as e:
            # 不要覆蓋一個我們看不懂的檔案 —— 裡面可能有使用者花時間設定的東西
            raise ClientError(
                f"{path} 不是合法的 JSON(第 {e.lineno} 行),"
                "請先修好它再安裝,以免覆蓋掉你原本的設定。") from e
        if not isinstance(data, dict):
            raise ClientError(f"{path} 的內容不是物件,不敢動它。")

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ClientError(f"{path} 裡的 mcpServers 不是物件,不敢動它。")

    if spec is None:
        servers.pop(SERVER_NAME, None)
    else:
        servers[SERVER_NAME] = spec

    _backup(path)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _write_toml(path, spec):
    """
    TOML 用文字層級的增修,不整份重寫。

    標準庫只有 tomllib(唯讀),而「讀成 dict 再寫回去」會把使用者的註解、
    欄位順序、多行字串全部弄丟。Codex 的設定檔是人手寫的,那些東西是有意義的。
    所以只動 [mcp_servers.my-hub] 那一段,其餘一個字都不碰。
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ClientError(
                f"{path} 不是合法的 TOML({e}),"
                "請先修好它再安裝,以免覆蓋掉你原本的設定。") from e

    lines = _drop_our_sections(text.splitlines())

    if spec is not None:
        # env 寫成 inline table 而不是 [mcp_servers.my-hub.env] 子區段。
        # 子區段的話這一段就變成兩個 section,而移除時很容易只刪掉前一個、
        # 留下半截的 .env —— 那會讓下次安裝變成「重複宣告」的壞 TOML。
        # 第一版就是這樣壞的,測試抓到的。
        env = ", ".join(f"{k} = {_toml_str(v)}" for k, v in spec["env"].items())
        lines += [
            "",
            f"{MARKER} 由 MCP Hub 寫入({datetime.now():%Y-%m-%d %H:%M})。",
            f"{MARKER} 可以手動改,但下次從 app 重新安裝時整段會被覆蓋。",
            f"[mcp_servers.{SERVER_NAME}]",
            f"command = {_toml_str(spec['command'])}",
            "args = [" + ", ".join(_toml_str(a) for a in spec["args"]) + "]",
            "env = { " + env + " }",
        ]

    _backup(path)
    _atomic_write(path, "\n".join(lines).strip("\n") + "\n")


def _drop_our_sections(lines):
    """
    移除 [mcp_servers.my-hub] 以及它底下所有子區段。

    只刪「到下一個 [ 為止」是不夠的:子區段(.env)本身就是下一個 [,
    會被當成別人的東西留下來。所以改成逐行追蹤目前在哪個區段。
    """
    ours = f"mcp_servers.{SERVER_NAME}"
    out, dropping = [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            dropping = name == ours or name.startswith(ours + ".")
            if dropping:
                # 我們自己寫的註解在區段前面,不會被上面的判斷掃到 ——
                # 不回頭清掉的話,反覆安裝 / 移除會累積一堆孤兒註解。
                # 端對端測試才抓到這個,單元測試只檢查解析後的結果所以漏了。
                while out and (out[-1].startswith(MARKER) or not out[-1].strip()):
                    out.pop()
        if not dropping:
            out.append(line)
    # 尾端可能留下一串空行(區段被抽掉之後)
    while out and not out[-1].strip():
        out.pop()
    return out


def _toml_str(value):
    """TOML 的基本字串。Windows 路徑有反斜線,一定要跳脫。"""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
