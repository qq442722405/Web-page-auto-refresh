# -*- coding: utf-8 -*-
"""最小 CDP（Chrome DevTools Protocol）客户端，仅依赖标准库。

Qt WebEngine 通过设置 QTWEBENGINE_REMOTE_DEBUGGING 打开 Chromium 的远程调试端口后，
本模块可以直接向网页的分发器投递鼠标事件、触发刷新、抓取页面截图。
输入事件由 Chromium 自身的输入管线处理，**不依赖窗口是否为前台、是否被遮挡**，
这是“真正后台执行”的关键通道。
"""
import base64
import json
import os
import socket
import ssl
import struct
import time
import urllib.error
import urllib.request

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_CACHE_EVENTS = 32


class CDPError(Exception):
    """CDP 调用失败（连接失败、超时、协议错误）。"""


# ============================================================ WebSocket ====
class WebSocket:
    """RFC 6455 客户端的最小实现：文本帧、掩码发送、分段重组、ping/pong。"""

    def __init__(self, url, timeout=3.0):
        self.url = url
        self.timeout = timeout
        self.sock = None
        self._buffer = b""
        scheme, rest = url.split("://", 1) if "://" in url else ("ws", url)
        self.secure = scheme == "wss"
        hostport, _, path = rest.partition("/")
        self.host, _, port = hostport.partition(":")
        self.port = int(port or (443 if self.secure else 80))
        self.path = "/" + path if path else "/"

    # ---------- 生命周期 ----------
    def connect(self, timeout=None):
        timeout = timeout or self.timeout
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(timeout)
        if self.secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (self.path, self.host, self.port, key)
        )
        sock.sendall(handshake.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = sock.recv(4096)
            if not chunk:
                raise CDPError("调试通道握手失败：连接被关闭")
            header += chunk
        if b"101" not in header.split(b"\r\n", 1)[0]:
            raise CDPError("调试通道握手失败：%s" % header.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.sock = sock
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self._buffer = b""

    @property
    def connected(self):
        return self.sock is not None

    # ---------- 帧处理 ----------
    @staticmethod
    def _mask(payload):
        mask = struct.pack("!I", int(time.time() * 1000) & 0xFFFFFFFF)
        body = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return mask + body

    def send_text(self, text):
        if self.sock is None:
            raise CDPError("调试通道未连接")
        payload = text.encode("utf-8")
        header = bytearray([0x80 | OP_TEXT])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        header += self._mask(payload)
        self.sock.sendall(bytes(header))

    def _recv_exact(self, count):
        while len(self._buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise CDPError("调试通道已关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _recv_frame(self):
        first, second = self._recv_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length) if length else b""
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def recv_text(self, timeout=None):
        """读取一条完整文本消息（自动处理分段、ping、pong）。"""
        if self.sock is None:
            raise CDPError("调试通道未连接")
        if timeout is not None:
            self.sock.settimeout(timeout)
        chunks = []
        opcode_text = None
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode in (OP_PING, OP_PONG):
                continue
            if opcode == OP_CLOSE:
                raise CDPError("调试通道被对端关闭")
            if opcode == OP_CONTINUATION or opcode_text == OP_TEXT:
                chunks.append(payload)
            elif opcode == OP_TEXT:
                opcode_text = opcode
                chunks.append(payload)
            if fin:
                break
        return b"".join(chunks).decode("utf-8", errors="replace")


# ================================================================= CDP ====
class CdpSession:
    """面向单个页面的同步 CDP 会话；每次调用都自带超时，绝不长期卡住界面。"""

    def __init__(self, ws_url, timeout=3.0):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self.timeout = timeout
        self._id = 0
        self.events = []

    def connect(self, timeout=None):
        self.ws.connect(timeout or self.timeout)
        return self

    def close(self):
        self.ws.close()

    @property
    def connected(self):
        return self.ws.connected

    def call(self, method, params=None, timeout=None):
        if not self.ws.connected:
            raise CDPError("调试通道未连接")
        self._id += 1
        message_id = self._id
        payload = {"id": message_id, "method": method}
        if params:
            payload["params"] = params
        deadline = time.monotonic() + (timeout or self.timeout)
        self.ws.send_text(json.dumps(payload, ensure_ascii=False))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError("%s 超时，通道已重连等待下一次尝试" % method)
            try:
                raw = self.ws.recv_text(timeout=remaining)
            except socket.timeout:
                raise CDPError("%s 超时（%.1fs）" % (method, timeout or self.timeout))
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    info = message["error"]
                    raise CDPError("%s 失败: %s" % (method, info.get("message", info)))
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)
                if len(self.events) > MAX_CACHE_EVENTS:
                    self.events = self.events[-MAX_CACHE_EVENTS:]

    # ---------- 常用动作 ----------
    def dispatch_click(self, x, y, timeout=None):
        """投递一次完整鼠标左键点击（CSS 像素坐标，相对可视区域）。"""
        common = {"x": x, "y": y, "pointerType": "mouse", "clickCount": 1}
        for kind, button, buttons in (
            ("mouseMoved", "none", 0),
            ("mousePressed", "left", 1),
            ("mouseReleased", "left", 0),
        ):
            self.call("Input.dispatchMouseEvent", {
                "type": kind, "button": button, "buttons": buttons, **common
            }, timeout=timeout)

    def reload(self, ignore_cache=False, timeout=None):
        self.call("Page.reload", {"ignoreCache": bool(ignore_cache)}, timeout=timeout)

    def evaluate(self, expression, timeout=None):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": False
        }, timeout=timeout)
        payload = result.get("result", {})
        detail = result.get("exceptionDetails")
        if detail is None:
            return payload.get("value")
        info = detail.get("exception", {})
        raise CDPError("JS 执行失败: %s" % (info.get("description") or info.get("value") or detail))

    def capture_screenshot(self, clip=None, fmt="png", timeout=None):
        params = {"format": fmt, "optimizeForSpeed": True}
        if clip:
            params["clip"] = clip
            params["captureBeyondViewport"] = False
        result = self.call("Page.captureScreenshot", params, timeout=max(timeout or 0, 8.0))
        return result.get("data", "")


# ============================================================ 端口发现 ====
def http_json(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset, errors="replace"))
    except urllib.error.HTTPError as exc:
        raise CDPError("HTTP %s" % exc.code)
    except Exception as exc:
        raise CDPError(str(exc) or exc.__class__.__name__)


def page_targets(host="127.0.0.1", port=9222, timeout=1.5):
    """返回调试端口上的页面目标列表。"""
    data = http_json("http://%s:%d/json/list" % (host, port), timeout=timeout)
    if not isinstance(data, list):
        raise CDPError("调试端口返回了非预期内容")
    return [item for item in data if isinstance(item, dict) and item.get("type") == "page"]


def pick_target(targets, prefer_url=None):
    if not targets:
        return None
    if prefer_url:
        wanted = prefer_url.rstrip("/")
        for item in targets:
            if (item.get("url") or "").rstrip("/") == wanted:
                return item
    # 排除 about:blank / devtools 页，优先有实际地址的页面。
    for item in targets:
        if not (item.get("url") or "").startswith(("devtools:", "about:")):
            return item
    return targets[0]


def connect_page(port, host="127.0.0.1", prefer_url=None, timeout=1.5, session_timeout=3.0):
    """连接调试端口上的目标页面；返回 (CdpSession, target)。"""
    targets = page_targets(host, port, timeout=timeout)
    target = pick_target(targets, prefer_url)
    if target is None:
        raise CDPError("调试端口 %s:%s 上没有可用页面" % (host, port))
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CDPError("目标页面没有提供 WebSocket 地址")
    return CdpSession(ws_url, timeout=session_timeout).connect(), target
