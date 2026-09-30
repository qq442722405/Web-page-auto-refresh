# -*- coding: utf-8 -*-
"""网页执行通道编排：统一的刷新 / 点击 / 截图入口。

三种递进通道，按可靠性降级：

1. **cdp** —— Chromium 远程调试通道（首选）
   输入事件直接进入网页的输入队列，不需要焦点、不需要窗口在最前面、
   不受其它软件遮挡影响，也不移动系统鼠标。这是真正意义的“后台执行”。
2. **qt**  —— Qt 合成鼠标事件（次选）
   不依赖调试端口，但依赖 Qt 能把事件投到 Chromium 的渲染控件。
3. **native** —— 真实系统点击（兜底）
   短暂把本软件窗口置前点击完成后立刻把焦点还给用户原来的程序，
   并还原鼠标位置。仅在上面两种都失败、且用户允许时使用。

截图同理：优先使用 Page.captureScreenshot 直接从网页取图，
不再依赖屏幕抓取，OCR 因此同样不受遮挡/位置变化影响。
"""
import base64
import json
import time

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QImage, QGuiApplication

from . import cdp as cdp_module
from . import win_input
from .coords import css_clip
from .logging_setup import write_log
from .qt_click import QtClickError, send_background_click


class OperationError(RuntimeError):
    """所有可用通道都执行失败。"""


class ClickOutcome:
    __slots__ = ("channel", "message", "detail")

    def __init__(self, channel, message, detail=""):
        self.channel = channel
        self.message = message
        self.detail = detail


CHANNEL_LABELS = {
    "cdp": "调试通道(CDP)",
    "qt": "Qt事件",
    "native": "真实点击",
}


class PageController(QObject):
    """持有三条通道，按策略顺序尝试，把失败原因如实上报。"""

    diagnosis = Signal(str)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.cdp_enabled = True
        self.cdp_port = 0
        self.cdp_host = "127.0.0.1"
        self.strategy = "auto"
        self.allow_temporary_foreground = True
        self._session = None
        self._session_key = None
        self._next_retry = 0.0
        self._failure_reported = False
        self._last_scale = 1.0

    # ==================================================== 配置/状态 ====
    def configure(self, cdp_enabled=None, cdp_port=None, strategy=None,
                  allow_temporary_foreground=None):
        if cdp_enabled is not None:
            self.cdp_enabled = bool(cdp_enabled)
        if cdp_port:
            self.cdp_port = int(cdp_port)
        if strategy:
            self.strategy = strategy
        if allow_temporary_foreground is not None:
            self.allow_temporary_foreground = bool(allow_temporary_foreground)
        if not self.cdp_enabled:
            self.close_session()

    def describe(self):
        parts = []
        if self.cdp_enabled and self.cdp_port:
            state = "已连接" if self._session is not None else "待连接"
            parts.append("调试通道 %s:%s(%s)" % (self.cdp_host, self.cdp_port, state))
        else:
            parts.append("调试通道：关闭")
        parts.append("Qt 事件通道：可用")
        parts.append("真实点击：%s" % ("允许" if self.allow_temporary_foreground else "禁止"))
        return "，".join(parts)

    # ==================================================== CDP 连接 ====
    def close_session(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
            self._session_key = None

    def _target_url(self):
        try:
            return self.view.url().toString()
        except Exception:
            return ""

    def cdp_session(self, force=False):
        """返回可用的 CDP 会话；不可用返回 None 并做节流，避免每轮都重试。"""
        if not self.cdp_enabled or not self.cdp_port:
            return None
        now = time.monotonic()
        if now < self._next_retry and not force:
            return None
        key = self._target_url()
        if self._session is not None and self._session_key == key and self._session.connected:
            return self._session
        self.close_session()
        try:
            session, target = cdp_module.connect_page(
                port=self.cdp_port, host=self.cdp_host, prefer_url=key,
                timeout=1.5, session_timeout=3.0)
            self._session = session
            self._session_key = (target or {}).get("url", key)
            self._failure_reported = False
            write_log("调试通道已连接: %s" % self._session_key)
            return session
        except Exception as exc:
            self.close_session()
            self._next_retry = time.monotonic() + 15.0
            if not self._failure_reported:
                self._failure_reported = True
                tips = []
                if hasattr(win_input, "supported"):
                    tips.append("请确认是否允许在本机开启 127.0.0.1 调试端口")
                self.diagnosis.emit("调试通道(%s:%s)不可用，已使用 Qt 事件通道；原因: %s %s"
                                    % (self.cdp_host, self.cdp_port, exc, " ".join(tips)))
            return None

    def notify_navigation(self):
        """页面跳转后目标地址变化，重建会话以确保操作的是当前页面。"""
        if self._session is not None:
            self.close_session()

    def css_scale(self, session, view_width):
        """实际 CSS/控件像素比：优先用页面自身报告的可视宽度，避免缩放误判。"""
        try:
            raw = session.evaluate("JSON.stringify([document.documentElement.clientWidth,"
                                   "window.innerWidth].filter(function(v){return v>0;}))")
            widths = json.loads(raw) if raw else []
            if widths:
                scale = float(view_width) / float(max(widths))
                if 0.2 < scale < 8.0:
                    self._last_scale = scale
                    return scale
        except Exception:
            pass
        return self._last_scale or 1.0

    # ==================================================== 点击 ====
    def click(self, point):
        """point 为 QPoint（控件内部坐标）。"""
        x, y = int(point.x()), int(point.y())
        candidates = self._click_channels()
        if not candidates:
            raise OperationError("没有可用通道：请检查【后台执行】设置（是否禁用了全部通道）")
        errors = []
        for name in candidates:
            try:
                message = self._dispatch_click(name, x, y)
                return ClickOutcome(name, message, "")
            except Exception as exc:
                errors.append("%s: %s" % (CHANNEL_LABELS.get(name, name), exc))
                write_log("点击通道 %s 失败: %s" % (name, exc), "WARN")
        detail = "；".join(errors)
        raise OperationError("后台点击全部通道失败 -> " + detail)

    def _click_channels(self):
        if self.strategy in ("cdp", "qt", "native"):
            order = [self.strategy]
            if self.strategy == "cdp" and self.allow_temporary_foreground:
                order.append("native")
        else:
            order = ["cdp", "qt"]
            if self.allow_temporary_foreground:
                order.append("native")
        unique = []
        for name in order:
            if name not in unique and self._channel_allowed(name):
                unique.append(name)
        return unique

    def _channel_allowed(self, name):
        if name == "cdp":
            return self.cdp_enabled and bool(self.cdp_port)
        if name == "native":
            if not win_input.supported():
                return False
            # 用户显式选择“仅真实点击”时，不受兜底开关影响。
            return self.allow_temporary_foreground or self.strategy == "native"
        return True

    def _dispatch_click(self, name, x, y):
        if name == "cdp":
            return self._click_cdp(x, y)
        if name == "qt":
            return self._click_qt(x, y)
        if name == "native":
            return self._click_native(x, y)
        raise OperationError("未知通道: %s" % name)

    def _click_cdp(self, x, y):
        session = self.cdp_session()
        if session is None:
            raise OperationError("调试通道不可用")
        try:
            width = max(1, self.view.rect().width())
            scale = self.css_scale(session, width)
            cx, cy = x / scale, y / scale
            session.dispatch_click(round(cx, 2), round(cy, 2), timeout=3.0)
            return "已通过调试通道发送点击 (%d, %d)" % (x, y)
        except Exception:
            self.close_session()
            raise

    def _click_qt(self, x, y):
        from PySide6.QtCore import QPoint
        try:
            send_background_click(self.view, QPoint(x, y))
        except QtClickError as exc:
            raise OperationError(str(exc))
        return "已通过 Qt 事件发送点击 (%d, %d)" % (x, y)

    def _click_native(self, x, y):
        from PySide6.QtCore import QPoint
        window = self.view.window()
        hwnd = int(window.winId()) if window is not None else 0
        if not hwnd:
            raise OperationError("无法获取窗口句柄")
        with win_input.temporary_foreground(hwnd) as guard:
            if not guard.succeeded:
                write_log("置前窗口失败，仍尝试点击", "WARN")
            global_pos = self.view.mapToGlobal(QPoint(x, y))
            win_input.real_click(int(global_pos.x()), int(global_pos.y()))
        return "已短暂置前完成真实点击 (%d, %d)，焦点已还原" % (x, y)

    # ==================================================== 刷新 ====
    def reload(self, ignore_cache=False):
        session = self.cdp_session() if self.strategy in ("auto", "cdp") else None
        if session is not None:
            try:
                session.reload(ignore_cache=ignore_cache, timeout=3.0)
                return "cdp"
            except Exception as exc:
                self.close_session()
                write_log("调试通道刷新失败，改用 Qt 刷新: %s" % exc, "WARN")
        try:
            self.view.reload()
        except Exception as exc:
            raise OperationError("网页刷新失败: %s" % exc)
        return "qt"

    # ==================================================== JS ====
    def evaluate(self, expression, timeout=2.0):
        session = self.cdp_session()
        if session is not None:
            try:
                return session.evaluate(expression, timeout=timeout)
            except Exception:
                self.close_session()
        return None

    def run_javascript(self, expression):
        """优先走调试通道，失败后回落到 QWebEnginePage.runJavaScript。"""
        try:
            page = self.view.page()
        except Exception:
            return
        if page is None:
            return
        try:
            page.runJavaScript(expression)
        except Exception as exc:
            write_log("JS 注入失败: %s" % exc, "WARN")

    # ==================================================== 截图 ====
    def screenshot(self, rect):
        """抓取 rect（控件内部 QRect）对应的画面，返回 QImage。"""
        image = self._screenshot_cdp(rect) if self.strategy in ("auto", "cdp") else None
        if image is None:
            image = self._screenshot_screen(rect)
        return image

    def _screenshot_cdp(self, rect):
        session = self.cdp_session()
        if session is None:
            return None
        try:
            width = max(1, self.view.rect().width())
            scale = self.css_scale(session, width)
            clip = css_clip(rect.x(), rect.y(), rect.width(), rect.height(), scale)
            data = session.capture_screenshot(clip=clip, timeout=10.0)
            if not data:
                return None
            image = QImage()
            if not image.loadFromData(base64.b64decode(data)):
                return None
            return image
        except Exception as exc:
            self.close_session()
            write_log("调试通道截图失败，改用屏幕抓取: %s" % exc, "WARN")
            return None

    def _screenshot_screen(self, rect):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        from PySide6.QtCore import QPoint
        dpr = screen.devicePixelRatio()
        top_left = self.view.mapToGlobal(QPoint(rect.x(), rect.y()))
        pixmap = screen.grabWindow(
            0,
            int(top_left.x() * dpr), int(top_left.y() * dpr),
            max(1, int(rect.width() * dpr)), max(1, int(rect.height() * dpr)),
        )
        if pixmap.isNull():
            return None
        return pixmap.toImage()

    def shutdown(self):
        self.close_session()

    @staticmethod
    def ensure_rect(values):
        return QRect(values[0], values[1], values[2], values[3])
