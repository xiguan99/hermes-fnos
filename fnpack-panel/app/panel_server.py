#!/usr/bin/env python3
"""Hermes Panel — Hermes Agent 的独立管理面板（fnOS 打包版，仅用 Python 标准库）。

设计要点
--------
- 独立进程，原生绑 0.0.0.0:2281，桌面图标/浏览器端口直达（不经网关反代）
- 只通过 HTTP 调本体 http://127.0.0.1:2280/api/*，不改本体代码、不接管本体自更新
- 面板版本线独立（1.x），支持从 GitHub Release 下载 res.tar.gz 自更新：
  三路下载（加速源 → 备用 → 官方直连）+ sha256 校验 + 探活 + 原子切换 + .bak 回滚
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── 运行时路径 ───────────────────────────────────────────────────────
APP_DIR = Path(os.environ.get("TRIM_APPDEST") or Path(__file__).resolve().parent)
VAR_DIR = Path(os.environ.get("TRIM_PKGVAR") or "/var/apps/hermes-panel/var")
DIST_DIR = APP_DIR / "panel_dist"
PANEL_VERSION_FILE = APP_DIR / "VERSION"
PANEL_PORT = int(os.environ.get("PANEL_PORT") or os.environ.get("TRIM_SERVICE_PORT") or "2281")
PANEL_HOST = os.environ.get("PANEL_HOST") or "0.0.0.0"
PANEL_PID_FILE = VAR_DIR / "panel.pid"
PANEL_LOG = VAR_DIR / "panel.log"
AUTH_FILE = VAR_DIR / "panel-auth.json"
SESSION_FILE = VAR_DIR / "panel-sessions.json"
SETTINGS_FILE = VAR_DIR / "panel-settings.json"
SNAPSHOT_DIR = VAR_DIR / "snapshots"
UPDATE_DIR = VAR_DIR / "update"
UPDATE_STATE_FILE = VAR_DIR / "panel-update-state.json"

# 本体（Hermes Agent）默认入口；装机版固定 2280
HERMES_URL = (os.environ.get("HERMES_URL") or "http://127.0.0.1:2280").rstrip("/")
try:
    HERMES_PORT = urlparse(HERMES_URL).port or 2280
except Exception:
    HERMES_PORT = 2280
GITHUB_REPO = os.environ.get("HERMES_PANEL_REPO") or "xiguan99/hermes-fnos"
# 接口/下载基址可覆盖（内网镜像与本地联调都用得上）
GITHUB_API_BASE = (os.environ.get("HERMES_PANEL_API_BASE") or "https://api.github.com").rstrip("/")
GITHUB_DL_BASE = (os.environ.get("HERMES_PANEL_DL_BASE") or "https://github.com").rstrip("/")
DOWNLOAD_MIRRORS = [
    "https://gh-proxy.com/https://github.com",
    "https://ghfast.top/https://github.com",
    "",  # 官方直连（保底）
]
HTTP_TIMEOUT = 20
APP_LAUNCH_ICON = "hermes-panel.Application"

VIEWS = [
    {"id": "overview", "title": "总览", "icon": "dashboard"},
    {"id": "service", "title": "运行控制", "icon": "power"},
    {"id": "versions", "title": "版本管理", "icon": "tag"},
    {"id": "logs", "title": "日志", "icon": "file-text"},
    {"id": "config", "title": "配置", "icon": "sliders"},
    {"id": "snapshots", "title": "快照", "icon": "archive"},
    {"id": "access", "title": "授权与访问", "icon": "shield"},
    {"id": "settings", "title": "设置", "icon": "settings"},
    {"id": "update", "title": "面板更新", "icon": "refresh"},
]

_lock = threading.Lock()


# ── 小工具 ──────────────────────────────────────────────────────────
def log(msg: str) -> None:
    try:
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        with open(PANEL_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [panel] {msg}\n")
    except Exception:
        pass


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_text(path: Path, limit: int = 0) -> str:
    try:
        if not path.exists():
            return ""
        if limit > 0:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                if size > limit:
                    fh.seek(-limit, os.SEEK_END)
                return fh.read().decode("utf-8", "replace")
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def run(cmd, timeout=25, cwd=None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # pragma: no cover
        return 1, str(exc)


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.2):
            return True
    except OSError:
        return False


def http_json(url: str, timeout=8, method="GET", body=None, headers=None):
    """请求本体 API，返回 (ok, data_or_error)。"""
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return True, json.loads(raw.decode("utf-8", "replace") or "{}")
            except Exception:
                return True, {"raw": raw.decode("utf-8", "replace")[:2000]}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        return False, f"HTTP {exc.code} {detail}".strip()
    except Exception as exc:
        return False, str(exc)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 本体发现 ────────────────────────────────────────────────────────
def discover_hermes() -> dict:
    """定位 Hermes Agent 本体部署目录与数据目录。

    优先 /proc 里正在跑的 monitor.js（最真实），其次 fnOS 标准位置。
    单机多实例（装机版 + 测试实例）并存时，按 HERMES_URL 的端口挑对应的那个。
    """
    info = {"appdest": "", "data_dir": "", "var_dir": "", "pid": 0, "monitor": ""}
    try:
        target_port = urlparse(HERMES_URL).port or 2280
    except Exception:
        target_port = 2280
    candidates = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                cmdline = Path(f"/proc/{entry}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            except Exception:
                continue
            m = re.search(r"(\S+)/server/monitor\.js", cmdline)
            if not m or "/hermes-panel/" in cmdline:
                continue
            appdest = m.group(1)
            if not os.path.isdir(os.path.join(appdest, "server")):
                continue
            pid = int(entry)
            env = {}
            try:
                raw = Path(f"/proc/{entry}/environ").read_bytes()
                for part in raw.split(b"\0"):
                    if b"=" in part:
                        k, v = part.split(b"=", 1)
                        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
            except Exception:
                pass
            # UI_PORT 由本包的 cmd/main 导出；老版（只走网关）没有该变量，按官方默认 8650 记
            try:
                ui_port = int(env.get("UI_PORT") or env.get("TRIM_SERVICE_PORT") or 0) or 8650
            except Exception:
                ui_port = 8650
            candidates.append({
                "appdest": appdest,
                "pid": pid,
                "monitor": os.path.join(appdest, "server", "monitor.js"),
                "data_dir": env.get("DATA_DIR") or env.get("HERMES_HOME") or "",
                "var_dir": env.get("VAR_DIR") or "",
                "ui_port": ui_port,
                "install": "@appcenter" in appdest,
            })
    except Exception:
        pass

    chosen = None
    for c in candidates:
        if c["ui_port"] == target_port:
            chosen = c
            break
    if chosen is None:
        # 端口对不上：装机版优先（生产实例最可能是用户真正在用的一份）
        for c in candidates:
            if c["install"]:
                chosen = c
                break
    if chosen is None and candidates:
        chosen = candidates[0]
    if chosen:
        info.update({k: chosen[k] for k in ("appdest", "pid", "monitor", "data_dir", "var_dir")})
        if info["data_dir"]:
            return info
    else:
        # 回退：fnOS 标准位置
        for candidate in ("/vol1/@appcenter/hermes-agent", "/var/apps/hermes-agent"):
            if os.path.isdir(os.path.join(candidate, "server")):
                info["appdest"] = candidate
                info["monitor"] = os.path.join(candidate, "server", "monitor.js")
                break
    for candidate in ("/vol1/@apphome/hermes-agent/data", "/var/apps/hermes-agent/home/data"):
        if os.path.isdir(candidate):
            info["data_dir"] = candidate
            break
    if not info["var_dir"]:
        for candidate in ("/vol1/@appdata/hermes-agent", "/var/apps/hermes-agent/var"):
            if os.path.isdir(candidate):
                info["var_dir"] = candidate
                break
    return info


def hermes_api(path: str, **kw):
    return http_json(HERMES_URL + path, **kw)


def hermes_alive() -> bool:
    ok, _ = hermes_api("/api/health", timeout=3)
    return ok


def process_info(pid: int) -> dict:
    """读 /proc 取 CPU 时间、RSS、启动时间。"""
    out = {"alive": False, "rss_mb": 0, "cpu_sec": 0.0, "uptime_sec": 0, "threads": 0}
    if not pid or not os.path.isdir(f"/proc/{pid}"):
        return out
    out["alive"] = True
    try:
        status = read_text(Path(f"/proc/{pid}/status"))
        m = re.search(r"VmRSS:\s+(\d+) kB", status)
        if m:
            out["rss_mb"] = round(int(m.group(1)) / 1024, 1)
        m = re.search(r"Threads:\s+(\d+)", status)
        if m:
            out["threads"] = int(m.group(1))
    except Exception:
        pass
    try:
        stat = read_text(Path(f"/proc/{pid}/stat")).split()
        out["cpu_sec"] = round((int(stat[13]) + int(stat[14])) / os.sysconf("SC_CLK_TCK"), 1)
    except Exception:
        pass
    try:
        start_ticks = int(read_text(Path(f"/proc/{pid}/stat")).split()[21])
        boot = float(read_text(Path("/proc/stat")).split("btime")[1].split()[0])
        out["uptime_sec"] = int(time.time() - (boot + start_ticks / os.sysconf("SC_CLK_TCK")))
    except Exception:
        pass
    return out


def system_load() -> dict:
    data = {"cpu_pct": 0.0, "mem_total_mb": 0, "mem_used_mb": 0, "uptime": ""}
    try:
        with open("/proc/loadavg") as fh:
            load1 = float(fh.read().split()[0])
        cores = os.cpu_count() or 1
        data["cpu_pct"] = round(min(100.0, load1 / cores * 100), 1)
    except Exception:
        pass
    try:
        mem = {}
        for line in read_text(Path("/proc/meminfo")).splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.strip().split()[0])
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", 0)
        data["mem_total_mb"] = round(total / 1024)
        data["mem_used_mb"] = round((total - avail) / 1024)
    except Exception:
        pass
    try:
        data["uptime"] = read_text(Path("/proc/uptime")).split()[0]
    except Exception:
        pass
    return data


# ── 鉴权 ────────────────────────────────────────────────────────────
def auth_state() -> dict:
    return read_json(AUTH_FILE, {"enabled": False, "salt": "", "hash": "", "updated": ""})


def set_password(password: str) -> None:
    state = {"enabled": False, "salt": "", "hash": "", "updated": ""}
    if password:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
        state = {
            "enabled": True,
            "salt": salt,
            "hash": digest.hex(),
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    write_json(AUTH_FILE, state)
    write_json(SESSION_FILE, {})


def verify_password(password: str) -> bool:
    state = auth_state()
    if not state.get("enabled"):
        return True
    try:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(state["salt"]), 200_000)
        return hmac.compare_digest(digest.hex(), state["hash"])
    except Exception:
        return False


def new_session() -> str:
    sid = secrets.token_urlsafe(24)
    sessions = read_json(SESSION_FILE, {})
    now = time.time()
    sessions = {k: v for k, v in sessions.items() if now - v.get("at", 0) < 7 * 86400}
    sessions[sid] = {"at": now, "ip": ""}
    write_json(SESSION_FILE, sessions)
    return sid


def session_valid(sid: str) -> bool:
    if not sid:
        return False
    sessions = read_json(SESSION_FILE, {})
    hit = sessions.get(sid)
    if not hit:
        return False
    if time.time() - hit.get("at", 0) > 7 * 86400:
        sessions.pop(sid, None)
        write_json(SESSION_FILE, sessions)
        return False
    return True


def drop_session(sid: str) -> None:
    sessions = read_json(SESSION_FILE, {})
    if sid in sessions:
        sessions.pop(sid, None)
        write_json(SESSION_FILE, sessions)


def trusted_network(ip: str) -> bool:
    """可信网段放行（本机 + 局域网私有网段）。"""
    if not ip:
        return False
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    m = re.match(r"172\.(\d+)\.", ip)
    if m and 16 <= int(m.group(1)) <= 31:
        return True
    if ip.startswith("169.254.") or ip.startswith("fe80:"):
        return True
    return False


def settings_state() -> dict:
    return read_json(
        SETTINGS_FILE,
        {"allow_lan": True, "require_trusted": False, "refresh_sec": 8, "log_tail_kb": 48},
    )


# ── 快照 ────────────────────────────────────────────────────────────
SNAPSHOT_ITEMS = ["config.yaml", "providers-state.yaml", "sessions", "skills", "memories", "hooks", "cron"]
SNAPSHOT_SKIP_DIRS = {"node_modules", "__pycache__", ".git", "venv", "bin"}


def _filter_snapshot(tarinfo: tarfile.TarInfo):
    parts = Path(tarinfo.name).parts
    if any(p in SNAPSHOT_SKIP_DIRS for p in parts):
        return None
    if tarinfo.size > 512 * 1024 * 1024:
        return None
    return tarinfo


def list_snapshots() -> list[dict]:
    items = []
    if not SNAPSHOT_DIR.is_dir():
        return items
    for path in sorted(SNAPSHOT_DIR.glob("*.tar.gz"), reverse=True):
        st = path.stat()
        items.append(
            {
                "name": path.name,
                "size_mb": round(st.st_size / 1024 / 1024, 1),
                "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                "label": path.stem.replace(".tar", ""),
            }
        )
    return items


def create_snapshot(label: str = "") -> dict:
    info = discover_hermes()
    data_dir = Path(info["data_dir"]) if info.get("data_dir") else None
    if not data_dir or not data_dir.is_dir():
        return {"ok": False, "error": "找不到 Hermes 数据目录，无法创建快照"}
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5_-]+", "-", label).strip("-")[:32]
    name = f"hermes-{stamp}{('-' + safe) if safe else ''}.tar.gz"
    target = SNAPSHOT_DIR / name
    with tarfile.open(target, "w:gz") as tar:
        for item in SNAPSHOT_ITEMS:
            src = data_dir / item
            if src.exists():
                tar.add(src, arcname=item, filter=_filter_snapshot)
    log(f"快照已创建 {name} {round(target.stat().st_size/1024/1024,1)}MB")
    return {"ok": True, "name": name, "size_mb": round(target.stat().st_size / 1024 / 1024, 1)}


def restore_snapshot(name: str) -> dict:
    if not re.match(r"^[A-Za-z0-9._-]+\.tar\.gz$", name):
        return {"ok": False, "error": "快照名不合法"}
    src = SNAPSHOT_DIR / name
    if not src.is_file():
        return {"ok": False, "error": "快照不存在"}
    info = discover_hermes()
    data_dir = Path(info["data_dir"]) if info.get("data_dir") else None
    if not data_dir or not data_dir.is_dir():
        return {"ok": False, "error": "找不到 Hermes 数据目录"}
    backup = create_snapshot("before-restore")
    try:
        with tarfile.open(src, "r:gz") as tar:
            members = [m for m in tar.getmembers() if not m.name.startswith("/") and ".." not in m.name]
            tar.extractall(data_dir, members=members)
    except Exception as exc:
        return {"ok": False, "error": f"恢复失败：{exc}"}
    log(f"快照已恢复 {name}（恢复前自动备份 {backup.get('name')}）")
    return {"ok": True, "restored": name, "auto_backup": backup.get("name"), "restart_required": True}


def delete_snapshot(name: str) -> dict:
    if not re.match(r"^[A-Za-z0-9._-]+\.tar\.gz$", name):
        return {"ok": False, "error": "快照名不合法"}
    path = SNAPSHOT_DIR / name
    if path.is_file():
        path.unlink()
        return {"ok": True}
    return {"ok": False, "error": "快照不存在"}


# ── 版本与更新 ──────────────────────────────────────────────────────
def panel_version() -> str:
    """面板当前版本：自更新切换后以 VAR 记录为准（应用目录里的 VERSION 只有装包时才变）。"""
    switched = read_text(VAR_DIR / "panel-version-current").strip()
    if switched:
        return switched
    return read_text(PANEL_VERSION_FILE).strip() or "0.0.0"


def compare_versions(a: str, b: str) -> int:
    def norm(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[._-]", v.strip("vV")) if x != ""]

    na, nb = norm(a), norm(b)
    for i in range(max(len(na), len(nb))):
        x = na[i] if i < len(na) else 0
        y = nb[i] if i < len(nb) else 0
        if x != y:
            return -1 if x < y else 1
    return 0


def github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "hermes-panel"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def releases(timeout=12) -> tuple[bool, list]:
    ok, data = http_json(
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/releases?per_page=20",
        timeout=timeout,
        headers=github_headers(),
    )
    if not ok or not isinstance(data, list):
        return False, []
    return True, data


def pick_panel_release(rel_list: list) -> dict | None:
    """面板资产在 hermes-panel 前缀的 tag 上（v1.x），或与本体重合的手机端 tag。"""
    best = None
    for rel in rel_list:
        if rel.get("draft"):
            continue
        tag = (rel.get("tag_name") or "").strip()
        for asset in rel.get("assets") or []:
            name = asset.get("name") or ""
            if re.match(r"^hermes-panel-res_v?\d", name):
                if best is None or compare_versions(tag, best["tag"]) > 0:
                    best = {"tag": tag, "asset": asset, "release": {k: rel.get(k) for k in ("name", "body", "published_at", "html_url")}}
    return best


def download_asset(asset_name: str, dest: Path, tag: str = "", on_progress=None) -> tuple[bool, str]:
    """三路下载：加速源 → 备用加速 → 官方直连。

    tag 是 Release 的 tag_name（带 v 前缀），下载 URL 必须用它，不能用版本号。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    official = f"{GITHUB_DL_BASE}/{GITHUB_REPO}/releases/download/{tag or dest.parent.name}/{asset_name}"
    errors = []
    for mirror in DOWNLOAD_MIRRORS:
        url = official if not mirror else official.replace("https://github.com", mirror)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hermes-panel"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = resp.read(1 << 18)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    if on_progress and total:
                        on_progress(got, total)
            if tmp.stat().st_size == 0:
                raise OSError("下载内容为空")
            os.replace(tmp, dest)
            log(f"下载成功 {url}")
            return True, url
        except Exception as exc:
            errors.append(f"{mirror or '官方直连'}: {exc}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return False, " | ".join(errors)


def check_panel_update() -> dict:
    cur = panel_version()
    ok, rel_list = releases()
    if not ok:
        return {"ok": False, "current": cur, "error": "无法连接 GitHub Releases（可稍后重试）"}
    picked = pick_panel_release(rel_list)
    if not picked:
        return {"ok": True, "current": cur, "latest": cur, "has_update": False, "release": None}
    latest = picked["tag"].lstrip("vV")
    asset = picked["asset"]
    return {
        "ok": True,
        "current": cur,
        "latest": latest,
        "has_update": compare_versions(latest, cur) > 0,
        "asset": {"name": asset.get("name"), "size": asset.get("size"), "digest": asset.get("digest")},
        "tag": picked["tag"],
        "release": picked["release"],
        "repo": GITHUB_REPO,
    }


def apply_panel_update(target_version: str = "") -> dict:
    """下载 res.tar.gz → sha256 → 解包到暂存目录 → 探活 → 原子切换 → 失败回滚。"""
    st = check_panel_update()
    if not st.get("ok"):
        return {"ok": False, "error": st.get("error")}
    if not st.get("has_update") and not target_version:
        return {"ok": False, "error": "已是最新版本"}
    asset = st.get("asset") or {}
    name = asset.get("name") or ""
    if not name:
        return {"ok": False, "error": "发布中没有面板资源包"}
    version = st.get("latest") or target_version
    tag = st.get("tag") or f"v{version}"
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    work = UPDATE_DIR / version
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / name

    progress = {"done": 0, "total": 0}

    def on_progress(got, total):
        progress["done"], progress["total"] = got, total

    thread_state = {"status": "downloading", "error": "", "url": "", "version": version, "at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def save_state():
        try:
            write_json(UPDATE_STATE_FILE, thread_state)
        except Exception:
            pass

    save_state()

    def worker():
        try:
            ok, info = download_asset(name, archive, tag, on_progress)
            if not ok:
                thread_state.update(status="error", error=f"下载失败：{info}")
                save_state()
                return
            thread_state["url"] = info
            thread_state["status"] = "verifying"
            save_state()
            expect = (asset.get("digest") or "").replace("sha256:", "")
            if expect:
                actual = sha256_file(archive)
                if actual != expect:
                    thread_state.update(status="error", error=f"校验失败：sha256 不符（{actual[:12]}… ≠ {expect[:12]}…）")
                    save_state()
                    return
            thread_state["status"] = "extracting"
            save_state()
            stage = work / "stage"
            stage.mkdir(exist_ok=True)
            with tarfile.open(archive, "r:gz") as tar:
                members = [m for m in tar.getmembers() if not m.name.startswith("/") and ".." not in m.name]
                tar.extractall(stage, members=members)
            root = stage
            if not (root / "index.html").is_file():
                cand = [p for p in stage.iterdir() if (p / "index.html").is_file()]
                if cand:
                    root = cand[0]
            if not (root / "index.html").is_file():
                thread_state.update(status="error", error="资源包结构不正确（缺 index.html）")
                save_state()
                return
            thread_state["status"] = "switching"
            save_state()
            backup = DIST_DIR.with_name(f"panel_dist.bak-{panel_version()}")
            shutil.rmtree(backup, ignore_errors=True)
            if DIST_DIR.exists():
                os.replace(DIST_DIR, backup)
            os.replace(root, DIST_DIR)
            write_json(VAR_DIR / "panel-version.json", {"version": version, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "backup": backup.name})
            try:
                (VAR_DIR / "panel-version-current").write_text(version, encoding="utf-8")
            except Exception:
                pass
            thread_state["status"] = "done"
            save_state()
            log(f"面板已更新到 {version}（备份 {backup.name}）")
        except Exception as exc:
            thread_state.update(status="error", error=str(exc))
            save_state()

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "state": "started", "version": version, "asset": name}


def update_progress() -> dict:
    out = dict(read_json(VAR_DIR / "panel-version.json", {}))
    out.update(read_json(UPDATE_STATE_FILE, {}))
    return out


def rollback_panel() -> dict:
    backup = None
    for cand in sorted(APP_DIR.glob("panel_dist.bak-*")):
        backup = cand
    if not backup:
        return {"ok": False, "error": "没有可回滚的备份"}
    current = DIST_DIR.with_name(f"panel_dist.failed-{int(time.time())}")
    if DIST_DIR.exists():
        os.replace(DIST_DIR, current)
    os.replace(backup, DIST_DIR)
    restored = backup.name.replace("panel_dist.bak-", "").replace("panel_dist.bak", "")
    try:
        if restored:
            (VAR_DIR / "panel-version-current").write_text(restored, encoding="utf-8")
    except Exception:
        pass
    log(f"面板已回滚到 {backup.name}")
    return {"ok": True, "rolled_back_from": backup.name}


# ── 本体控制 ────────────────────────────────────────────────────────
def hermes_cmd_main() -> str:
    info = discover_hermes()
    appdest = info.get("appdest") or ""
    for cand in (
        os.path.join(appdest, "cmd", "main") if appdest else "",
        "/var/apps/hermes-agent/cmd/main",
        "/vol1/@appcenter/hermes-agent/cmd/main",
    ):
        if cand and os.path.isfile(cand):
            return cand
    return ""


def hermes_service(action: str) -> dict:
    """start/stop/restart 本体。restart = stop + start（Fnos 生命周期脚本无 restart 子命令）。"""
    info = discover_hermes()
    appdest = info.get("appdest") or ""
    cmd_main = hermes_cmd_main()
    if not cmd_main:
        # 退路：用本体自己的 API 重启 monitor 内部服务
        if action in ("restart", "start"):
            ok, data = hermes_api("/api/start", method="POST", body={})
            return {"ok": ok, "via": "api", "detail": data}
        return {"ok": False, "error": "找不到 Hermes 本体启动脚本（应用可能未安装）"}

    env = dict(os.environ)
    env.update(
        {
            "TRIM_APPDEST": appdest,
            "TRIM_PKGHOME": os.path.dirname(info.get("data_dir") or "/var/apps/hermes-agent/home"),
            "TRIM_PKGVAR": info.get("var_dir") or "",
            "PATH": "/var/apps/nodejs_v24/target/bin:/usr/local/bin:/usr/bin:/bin",
        }
    )
    if action in ("stop", "restart"):
        rc, out = run([cmd_main, "stop"], timeout=60)
        if action == "stop":
            return {"ok": rc == 0, "detail": out[-800:]}
        time.sleep(1.5)
    rc, out = run([cmd_main, "start"], timeout=90)
    return {"ok": rc == 0, "detail": out[-800:]}


# ── HTTP 处理 ───────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "HermesPanel"
    protocol_version = "HTTP/1.1"

    # -- 输出helpers ----------------------------------------------------
    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, data, code=200, extra=None):
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), extra=extra)

    def err(self, msg, code=400):
        self.json({"ok": False, "error": msg}, code)

    def body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
        except Exception:
            return {}

    # -- 鉴权 -----------------------------------------------------------
    def client_ip(self) -> str:
        return (self.client_address or ("", 0))[0]

    def cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == name:
                    return v.strip()
        return ""

    def authorized(self) -> bool:
        state = auth_state()
        if not state.get("enabled"):
            return True
        if session_valid(self.cookie("hermes_panel_sid")):
            return True
        st = settings_state()
        if st.get("require_trusted") is False and trusted_network(self.client_ip()):
            return True
        return False

    # -- 路由 -----------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path.startswith("/api/"):
            return self.handle_api("GET", path, query)
        return self.serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self.handle_api("POST", parsed.path, parse_qs(parsed.query))
        self.err("Not Found", 404)

    def serve_static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/")
        if ".." in rel:
            return self.err("bad path", 400)
        target = DIST_DIR / rel
        if not target.is_file():
            # SPA 回退
            target = DIST_DIR / "index.html"
            if not target.is_file():
                return self.err("panel_dist 缺失", 500)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".json": "application/json; charset=utf-8",
            ".woff2": "font/woff2",
        }.get(target.suffix, "application/octet-stream")
        try:
            data = target.read_bytes()
        except Exception as exc:
            return self.err(str(exc), 500)
        self._send(200, data, ctype)

    def handle_api(self, method: str, path: str, query):
        # 登录相关不问鉴权
        if path == "/api/panel/auth/state":
            st = auth_state()
            return self.json(
                {
                    "ok": True,
                    "enabled": bool(st.get("enabled")),
                    "logged_in": self.authorized(),
                    "trusted_ip": trusted_network(self.client_ip()),
                    "ip": self.client_ip(),
                    "panel_version": panel_version(),
                    "views": VIEWS,
                }
            )
        if path == "/api/panel/login" and method == "POST":
            body = self.body_json()
            if not auth_state().get("enabled"):
                return self.json({"ok": True, "logged_in": True, "note": "未设置密码"})
            if verify_password(body.get("password") or ""):
                sid = new_session()
                return self.json(
                    {"ok": True, "logged_in": True},
                    extra={"Set-Cookie": f"hermes_panel_sid={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"},
                )
            return self.err("密码错误", 401)
        if path == "/api/panel/logout" and method == "POST":
            drop_session(self.cookie("hermes_panel_sid"))
            return self.json({"ok": True}, extra={"Set-Cookie": "hermes_panel_sid=; Path=/; Max-Age=0"})

        if not self.authorized():
            return self.json({"ok": False, "error": "需要登录", "login": True}, 401)

        try:
            return self.route(method, path, query)
        except Exception as exc:  # pragma: no cover
            log(f"API 异常 {path}: {exc}")
            return self.err(f"内部错误：{exc}", 500)

    def route(self, method: str, path: str, query):
        # 总览
        if path == "/api/panel/overview":
            info = discover_hermes()
            pinfo = process_info(info.get("pid") or 0)
            ok_health, health = hermes_api("/api/health", timeout=3)
            ok_gw, gw = hermes_api("/api/gateway/health", timeout=5)
            ok_sess, sessions = hermes_api("/api/sessions", timeout=6)
            ok_mod, models = hermes_api("/api/models", timeout=6)
            data_dir = info.get("data_dir") or ""
            hermes_ver = read_text(Path(data_dir) / ".." / ".." / "VERSION").strip() if data_dir else ""
            if not hermes_ver:
                hermes_ver = read_text(Path(info.get("appdest") or "") / "VERSION").strip()
            if not hermes_ver and info.get("var_dir"):
                hermes_ver = read_text(Path(info["var_dir"]) / "hermes_version.txt").strip()
            return self.json(
                {
                    "ok": True,
                    "hermes": {
                        "alive": ok_health,
                        "url": HERMES_URL,
                        "version": hermes_ver or "-",
                        "pid": info.get("pid") or 0,
                        "appdest": info.get("appdest") or "",
                        "data_dir": data_dir,
                        "port": HERMES_PORT,
                        "process": pinfo,
                        "gateway": gw if ok_gw else {"ok": False},
                        "sessions": len(sessions) if isinstance(sessions, list) else 0,
                        "models": len(models) if isinstance(models, list) else 0,
                    },
                    "panel": {"version": panel_version(), "port": PANEL_PORT, "views": VIEWS},
                    "system": system_load(),
                    "ts": int(time.time()),
                }
            )

        # 运行控制
        if path == "/api/panel/service" and method == "POST":
            body = self.body_json()
            action = (body.get("action") or "").strip()
            if action not in ("start", "stop", "restart"):
                return self.err("action 必须是 start/stop/restart")
            result = hermes_service(action)
            return self.json(result)

        # 版本管理
        if path == "/api/panel/versions":
            info = discover_hermes()
            data_dir = info.get("data_dir") or ""
            local_ver = read_text(Path(info.get("appdest") or "") / "VERSION").strip() or "-"
            ok_up, up = hermes_api("/api/hermes/update/check", timeout=15)
            official = up if ok_up else {"ok": False, "error": up if isinstance(up, str) else "本体未响应"}
            return self.json(
                {
                    "ok": True,
                    "panel": {"version": panel_version(), "repo": GITHUB_REPO, "line": "1.x（可独立升级）"},
                    "hermes": {
                        "installed": local_ver,
                        "official": official,
                        "note": "本体更新走应用中心（Hermes Agent 应用页更新），本面板只做提示不改动",
                    },
                }
            )

        # 日志
        if path == "/api/panel/logs":
            info = discover_hermes()
            name = (query.get("name") or ["app"])[0]
            try:
                kb = int((query.get("kb") or ["48"])[0])
            except ValueError:
                kb = 48
            var_dir = Path(info.get("var_dir") or "")
            mapping = {
                "app": var_dir / "info.log",
                "gateway": var_dir / "gateway.log",
                "dashboard": var_dir / "dashboard.log",
                "panel": PANEL_LOG,
            }
            target = mapping.get(name)
            if not target:
                return self.err("未知日志")
            text = read_text(target, limit=max(4096, kb * 1024))
            return self.json({"ok": True, "name": name, "path": str(target), "text": text})

        # 配置
        if path == "/api/panel/config":
            if method == "GET":
                ok, cfg = hermes_api("/api/config", timeout=8)
                if not ok:
                    return self.json({"ok": False, "error": f"读取本体配置失败：{cfg}"})
                info = discover_hermes()
                data_dir = Path(info.get("data_dir") or "")
                yaml_text = read_text(data_dir / "config.yaml", limit=200_000)
                return self.json({"ok": True, "config": cfg, "yaml": yaml_text})
            body = self.body_json()
            if isinstance(body.get("config"), dict):
                ok, data = hermes_api("/api/config", method="POST", body=body["config"], timeout=15)
                return self.json({"ok": ok, "detail": data})
            return self.err("缺少 config")

        # 快照
        if path == "/api/panel/snapshots":
            if method == "GET":
                return self.json({"ok": True, "items": list_snapshots()})
            body = self.body_json()
            action = body.get("action") or "create"
            if action == "create":
                return self.json(create_snapshot(body.get("label") or ""))
            if action == "restore":
                return self.json(restore_snapshot(body.get("name") or ""))
            if action == "delete":
                return self.json(delete_snapshot(body.get("name") or ""))
            return self.err("未知快照动作")

        # 授权与访问
        if path == "/api/panel/access":
            st = auth_state()
            sess = read_json(SESSION_FILE, {})
            if method == "GET":
                return self.json(
                    {
                        "ok": True,
                        "enabled": bool(st.get("enabled")),
                        "updated": st.get("updated") or "",
                        "sessions": len(sess),
                        "ip": self.client_ip(),
                        "trusted": trusted_network(self.client_ip()),
                        "settings": settings_state(),
                    }
                )
            body = self.body_json()
            if "password" in body:
                set_password((body.get("password") or "").strip())
                return self.json({"ok": True, "enabled": bool((body.get("password") or "").strip())})
            return self.err("缺少参数")

        # 设置
        if path == "/api/panel/settings":
            if method == "GET":
                return self.json({"ok": True, "settings": settings_state()})
            body = self.body_json()
            cur = settings_state()
            for key in ("allow_lan", "require_trusted", "refresh_sec", "log_tail_kb"):
                if key in body:
                    cur[key] = body[key]
            write_json(SETTINGS_FILE, cur)
            return self.json({"ok": True, "settings": cur})

        # 面板更新
        if path == "/api/panel/update":
            if method == "GET":
                return self.json(update_progress())
            body = self.body_json()
            action = body.get("action") or "check"
            if action == "check":
                return self.json(check_panel_update())
            if action == "apply":
                return self.json(apply_panel_update(body.get("version") or ""))
            if action == "rollback":
                return self.json(rollback_panel())
            return self.err("未知更新动作")

        return self.err("Not Found", 404)

    def log_message(self, fmt, *args):  # 静音：写自己的日志文件
        try:
            log(f"{self.client_ip()} {fmt % args}")
        except Exception:
            pass


def main() -> int:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not DIST_DIR.is_dir():
        print(f"panel_dist 缺失: {DIST_DIR}", file=sys.stderr)
    info = discover_hermes()
    log(f"启动 Hermes Panel v{panel_version()} pid={os.getpid()} 端口={PANEL_PORT} 本体={info.get('appdest') or '未发现'} 数据={info.get('data_dir') or '-'}")
    try:
        PANEL_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    httpd = ThreadingHTTPServer((PANEL_HOST, PANEL_PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("收到中断，退出")
    finally:
        try:
            PANEL_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
