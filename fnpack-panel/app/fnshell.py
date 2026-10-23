#!/usr/bin/env python3
"""hermes-panel 壳（fngateway 适配层）

职责只有四件事：
  1. 监听飞牛网关给的 Unix socket（$TRIM_APPDEST/hermes-panel.sock），
     同时保留一个 TCP 端口（默认 127.0.0.1:2281）供排障直连。
  2. 剥掉网关前缀（默认 /app/hermes-panel），把请求转发到内部面板 127.0.0.1:12281。
  3. 补前缀语义：无尾斜杠的入口 301 归位、Location 头补前缀、Set-Cookie 的 Path 改写。
  4. 流式转发不做缓冲（SSE / 大响应边收边发），并明确告诉中间层别缓冲。

面板业务逻辑一律留在 panel_server.py，这里不掺业务，方便分别升级。
"""
import argparse
import http.client
import os
import socket  # noqa: F401  (UnixStreamServer 需要 AF_UNIX)
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

VERSION = "1.0.3"
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
BODYLESS = {"HEAD", "OPTIONS"}


def log(msg: str) -> None:
    print(f"[shell] {msg}", flush=True)


def strip_prefix(path: str, prefix: str) -> str:
    """去掉网关前缀；剥完为空串时交给调用方补成 "/"。"""
    if not prefix:
        return path
    if path == prefix:
        return ""
    if path.startswith(prefix + "/"):
        return path[len(prefix):]
    if path.startswith(prefix + "?"):
        return path[len(prefix):]
    return path


class ShellHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"hermes-panel-shell/{VERSION}"
    sys_version = ""

    # ── 生命周期 ────────────────────────────────
    def log_message(self, fmt, *args):  # 静默：请求日志由面板自己记
        pass

    def do_GET(self):
        self.handle_all()

    def do_HEAD(self):
        self.handle_all()

    def do_POST(self):
        self.handle_all()

    def do_PUT(self):
        self.handle_all()

    def do_PATCH(self):
        self.handle_all()

    def do_DELETE(self):
        self.handle_all()

    def do_OPTIONS(self):
        self.handle_all()

    # ── 请求处理 ────────────────────────────────
    def handle_all(self):
        prefix = getattr(self.server, "prefix", "") or ""
        target_host = getattr(self.server, "target_host", "127.0.0.1")
        target_port = getattr(self.server, "target_port", 12281)

        raw = self.path or "/"
        parts = urlsplit(raw)
        req_path = strip_prefix(parts.path, prefix)

        # 入口缺尾斜杠：/app/hermes-panel → 301 /app/hermes-panel/
        # 不归位的话，页面里的相对资源会少一层目录（qwenpaw 当年第二个坑）。
        if prefix and parts.path == prefix:
            loc = prefix + "/" + (("?" + parts.query) if parts.query else "")
            self.send_response(301)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not req_path:
            req_path = "/"
        dest = urlunsplit(("", "", req_path, parts.query, ""))

        try:
            self._proxy(target_host, target_port, dest)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # 面板没起来 / 端口没通
            self._fail(f"面板后端不可用（{target_host}:{target_port}）：{exc}")

    def _fail(self, message: str):
        body = (
            "<!doctype html><meta charset='utf-8'><title>Hermes 面板</title>"
            "<body style='font:15px/1.7 system-ui;padding:48px;color:#c9d1d9;background:#0d1117'>"
            f"<h2>Hermes 面板暂时不可用</h2><p>{message}</p>"
            "<p style='color:#8b949e'>面板进程可能还在启动，稍后刷新即可；"
            "若持续如此，请查看应用日志 info.log。</p></body>"
        ).encode("utf-8")
        try:
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "3")
            self.end_headers()
            if self.command not in BODYLESS:
                self.wfile.write(body)
        except Exception:
            pass

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return None
        return self.rfile.read(length)

    def _proxy(self, host: str, port: int, dest: str):
        prefix = getattr(self.server, "prefix", "") or ""
        body = self._read_body()

        headers = {}
        for key, value in self.headers.items():
            low = key.lower()
            if low in HOP_BY_HOP or low in ("host", "content-length", "accept-encoding"):
                continue
            headers[key] = value
        headers["Host"] = f"127.0.0.1:{port}"
        headers["X-Forwarded-Prefix"] = prefix or "/"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        # 真实来源 IP：飞牛网关已经带 X-Real-IP / X-Forwarded-For 时必须透传，
        # 否则面板会把所有外部访问都当成本机，从而跳过访问密码。
        peer = (self.client_address[0] if self.client_address else "") or ""
        prior = (self.headers.get("X-Real-IP") or "").strip()
        prior_xff = (self.headers.get("X-Forwarded-For") or "").strip()
        if prior:
            real_ip = prior
        elif prior_xff:
            real_ip = prior_xff.split(",")[0].strip()
        else:
            real_ip = peer
        if real_ip:
            headers["X-Real-IP"] = real_ip
        chain = [p for p in (prior_xff, peer) if p]
        if chain:
            headers["X-Forwarded-For"] = ", ".join(chain)

        conn = http.client.HTTPConnection(host, port, timeout=180)
        try:
            conn.request(self.command, dest, body=body, headers=headers)
            resp = conn.getresponse()
            self._relay(resp, prefix)
        finally:
            conn.close()

    def _relay(self, resp, prefix: str):
        status = resp.status
        length_hdr = resp.getheader("Content-Length")
        ctype = (resp.getheader("Content-Type") or "").lower()
        is_stream = ("text/event-stream" in ctype) or (length_hdr is None)

        self.send_response(status)
        for key, value in resp.getheaders():
            low = key.lower()
            if low in HOP_BY_HOP or low == "content-length":
                continue
            if low == "location" and prefix and value.startswith("/") and not value.startswith(prefix + "/"):
                value = prefix + value
            elif low == "set-cookie" and prefix:
                value = value.replace("Path=/;", f"Path={prefix}/;").replace("Path=/", f"Path={prefix}/")
            self.send_header(key, value)
        if not is_stream and length_hdr is not None:
            self.send_header("Content-Length", length_hdr)
        else:
            self.send_header("Transfer-Encoding", "chunked")
        # 明确禁止中间层缓冲（网关 / nginx 系）
        self.send_header("X-Accel-Buffering", "no")
        if is_stream:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        if self.command in BODYLESS:
            return

        if not is_stream and length_hdr is not None:
            remaining = int(length_hdr)
            while remaining > 0:
                block = resp.read(min(65536, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)
            self.wfile.flush()
            return

        # chunked / 无长度：边收边发，SSE 才能实时到达
        while True:
            block = resp.read(4096)
            if not block:
                break
            self.wfile.write(b"%x\r\n" % len(block) + block + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if hasattr(socketserver, "UnixStreamServer"):
    BaseUnixServer = socketserver.UnixStreamServer
else:  # 极老 Python 的兜底
    class BaseUnixServer(socketserver.TCPServer):
        address_family = getattr(socket, "AF_UNIX", socket.AF_INET)


class ThreadingUnixHTTP(socketserver.ThreadingMixIn, BaseUnixServer):
    request_queue_size = 128
    daemon_threads = True

    def __init__(self, socket_path: str, prefix: str, target_host: str, target_port: int):
        self.prefix = prefix.rstrip("/")
        self.target_host = target_host
        self.target_port = target_port
        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
            except OSError:
                pass
        super().__init__(socket_path, ShellHandler)
        try:
            os.chmod(socket_path, 0o666)  # 网关进程要能以其它用户连进来
        except OSError:
            pass


class ThreadingTCPHTTP(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    ap = argparse.ArgumentParser(description="hermes-panel 壳")
    ap.add_argument("--socket", default="", help="飞牛网关 Unix socket 路径")
    ap.add_argument("--prefix", default="/app/hermes-panel", help="飞牛网关前缀")
    ap.add_argument("--backend", default="127.0.0.1:12281", help="内部面板地址")
    ap.add_argument("--tcp", type=int, default=2281, help="直连排障端口（0 表示不监听）")
    ap.add_argument("--tcp-host", default="0.0.0.0", help="直连端口绑定地址")
    args = ap.parse_args()

    if ":" in args.backend:
        host, port = args.backend.rsplit(":", 1)
        port = int(port)
    else:
        host, port = "127.0.0.1", int(args.backend)

    servers = []

    if args.socket:
        uds = ThreadingUnixHTTP(args.socket, args.prefix, host, port)
        servers.append(("unix", args.socket, uds))
        log(f"网关 socket 已监听 {args.socket}（前缀 {args.prefix or '/'}）")
    else:
        log("未指定 --socket，仅走 TCP 直连模式")

    if args.tcp:
        tcp = ThreadingTCPHTTP((args.tcp_host, args.tcp), ShellHandler)
        tcp.prefix = args.prefix.rstrip("/")
        tcp.target_host = host
        tcp.target_port = port
        servers.append(("tcp", f"{args.tcp_host}:{args.tcp}", tcp))
        log(f"直连端口已监听 {args.tcp_host}:{args.tcp}")

    if not servers:
        log("没有任何监听目标，退出")
        return 1

    log(f"转发目标 127.0.0.1:{port}（内部面板）")

    threads = []
    for kind, addr, srv in servers[1:]:
        t = threading.Thread(target=srv.serve_forever, name=f"{kind}-{addr}", daemon=True)
        t.start()
        threads.append(t)

    try:
        servers[0][2].serve_forever()
    except KeyboardInterrupt:
        log("收到中断")
    finally:
        for _, _, srv in servers:
            try:
                srv.server_close()
            except Exception:
                pass
        if args.socket and os.path.exists(args.socket):
            try:
                os.unlink(args.socket)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
