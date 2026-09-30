# -*- coding: utf-8 -*-
"""手机扫码看图的本地 HTTP 服务。

只在 127.0.0.1/局域网按需提供最新一张截图，链接带有随机令牌，
避免旧请求拿到过期图片，也避免任意文件被读取。
"""
import os
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logging_setup import log_exception, write_log


def _free_port(start, tries=100):
    for port in range(start, start + tries):
        candidate = socket.socket()
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
        finally:
            candidate.close()
    return start


class _Handler(BaseHTTPRequestHandler):
    latest_path = ""
    latest_token = ""

    def log_message(self, format, *args):  # 静音，避免污染日志
        pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        wanted = "/%s.png" % _Handler.latest_token
        if path != wanted or not _Handler.latest_path:
            self.send_error(404, "File not found or expired.")
            return
        if not os.path.exists(_Handler.latest_path):
            self.send_error(404, "File not found or expired.")
            return
        try:
            with open(_Handler.latest_path, "rb") as handle:
                payload = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            log_exception("发送截图失败", exc)
            try:
                self.send_error(500, "Internal error")
            except Exception:
                pass


class ScreenshotService:
    def __init__(self, start_port=8999):
        self.port = _free_port(start_port)
        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="ScreenshotHTTP", daemon=True)
        self.thread.start()
        write_log("截图服务已启动，端口=%s" % self.port)

    def publish(self, path):
        """发布一张最新截图，返回本次的随机访问令牌。"""
        import secrets
        _Handler.latest_path = path
        _Handler.latest_token = secrets.token_hex(8)
        return _Handler.latest_token

    def url_for(self, host, path=None):
        """返回可供手机访问的地址；传入 path 时顺带发布新图。"""
        if path is not None:
            self.publish(path)
        elif not _Handler.latest_token:
            return ""
        return "http://%s:%d/%s.png" % (host or "127.0.0.1", self.port, _Handler.latest_token)

    def stop(self):
        try:
            self.server.shutdown()
        except Exception as exc:
            log_exception("关闭截图服务失败", exc)
        try:
            self.server.server_close()
        except Exception:
            pass
