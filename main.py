# -*- coding: utf-8 -*-
"""
网页刷新数字监控 (V24.2 - 操作间隔/网页元素点击版)
更新日志：
 1. 取消各监视窗口单框齿轮，界面保持简洁。
 2. 屏幕右上角常驻全局控制栏：
    - ⏱️ 实时显示【操作倒计时】与【OCR检测倒计时】
    - 👁️ 一键【隐藏/显示所有识别窗口】
    - ⚙️ 【设置/收起】打开或关闭设置浮层
    - 控制栏位于最上层，可拖拽移动，按钮始终可点击
 3. 继承 V10.8 增量防重复报警逻辑，解决消除报警后表格新增行导致误报的问题。
依赖：PySide6, PySide6.QtWebEngineWidgets, ddddocr, opencv-python, numpy
"""

import sys
import json
import time
import traceback
import faulthandler
import os
import re
import glob
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import cv2

# ==================== 1. ddddocr 兼容性导入检查 ====================
HAS_DDDDOCR = False
DDDDOCR_ERR_MSG = ""
try:
    import ddddocr
    HAS_DDDDOCR = True
except Exception as e:
    HAS_DDDDOCR = False
    DDDDOCR_ERR_MSG = "".join(traceback.format_exception_only(type(e), e)).strip()

# ==================== 2. PySide6 核心组件导入 ====================
from PySide6.QtCore import QUrl, Qt, QTimer, QDateTime, QRect, QPoint, Signal, QEvent, QObject, QThread, Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLineEdit, QLabel, QSpinBox, QCheckBox,
    QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy, QFileDialog, QDialog,
    QComboBox, QTextEdit, QAbstractSpinBox
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QTextCursor, QGuiApplication, QImage

# ==================== 3. 基础配置与辅助函数 ====================
# ==================== 3A. 持久化运行日志 / 崩溃诊断 ====================
# 日志放在 EXE / main.py 所在目录，程序异常退出后仍可直接查看。
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
RUNTIME_LOG_FILE = os.path.join(BASE_DIR, "日志.TXT")

_log_lock = threading.RLock()
_log_file_handle = None

def _write_runtime_log(text, level="INFO"):
    global _log_file_handle
    try:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {text}"
        with _log_lock:
            if _log_file_handle is None or _log_file_handle.closed:
                try:
                    _log_file_handle = open(RUNTIME_LOG_FILE, "a", encoding="utf-8", buffering=1)
                except Exception:
                    # EXE 若被放在无写权限目录，退回当前工作目录。
                    fallback = os.path.join(os.getcwd(), "日志.TXT")
                    _log_file_handle = open(fallback, "a", encoding="utf-8", buffering=1)
            _log_file_handle.write(line + "\n")
            _log_file_handle.flush()
    except Exception:
        pass

def _log_exception(prefix, exc=None):
    try:
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            detail = traceback.format_exc()
        _write_runtime_log(f"{prefix}\n{detail}", "ERROR")
    except Exception:
        pass

def _get_process_memory_mb():
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            psapi = ctypes.WinDLL("Psapi.dll")
            kernel32 = ctypes.WinDLL("kernel32.dll")
            handle = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / 1024 / 1024
        else:
            import resource
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return value / 1024 if value > 1024 * 1024 else value / 1024
    except Exception:
        return None

# Python 未处理异常、线程未处理异常、Qt 消息、底层致命错误全部尽量写入日志。
def _global_excepthook(exc_type, exc_value, exc_tb):
    try:
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write_runtime_log("未处理的主线程异常:\n" + detail, "FATAL")
    except Exception:
        pass
    try:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass

sys.excepthook = _global_excepthook
if hasattr(threading, "excepthook"):
    def _thread_excepthook(args):
        try:
            detail = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            _write_runtime_log(f"线程异常 ({getattr(args.thread, 'name', 'unknown')}):\n{detail}", "FATAL")
        except Exception:
            pass
    threading.excepthook = _thread_excepthook

try:
    _faulthandler_file = open(RUNTIME_LOG_FILE, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(_faulthandler_file, all_threads=True)
except Exception as e:
    _faulthandler_file = None
    _write_runtime_log(f"faulthandler 启用失败: {e}", "WARN")

_write_runtime_log(f"程序启动，PID={os.getpid()}，日志文件={RUNTIME_LOG_FILE}")

CONFIG_FILE = os.path.join(BASE_DIR, "auto_login_config.json")

def load_config():
    default = {
        "url": "https://example.com/login",
        "account": "",
        "password": "",
        "zoom_level": 1.0,
        "auto_refresh": False,
        "operation_interval": 60,
        "operation_action": "refresh",
        "click_point": [],
        "panel_collapsed": False,
        "screenshot_path": os.getcwd(),
        "selected_ip": "",
        "reminder_sound_index": 0,
        "reminder_custom_path": "",
        "reminder_sound_count": 3,
        "roi_list": [[100, 100, 300, 200]],
        "roi_multiplier": 1,
        "target_same_count": 3,
        "target_value": "",
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "roi_rect" in data and "roi_list" not in data:
                    data["roi_list"] = [data["roi_rect"]]
                default.update(data)
        except:
            pass
    return default

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log_exception("保存配置文件失败", e)

def get_all_local_ips():
    from PySide6.QtNetwork import QNetworkInterface, QAbstractSocket
    ip_list = []
    for address in QNetworkInterface.allAddresses():
        if address.protocol() == QAbstractSocket.IPv4Protocol:
            ip_str = address.toString()
            if ip_str != "127.0.0.1" and not ip_str.startswith("169.254"):
                ip_list.append(ip_str)
    if not ip_list:
        ip_list.append("127.0.0.1")
    return sorted(list(set(ip_list)))


# ==================== 4. 折叠面板组件 ====================
class CombinedCollapsiblePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        self.toggle_btn = QPushButton("▲ 基础配置与设置 (点击收起面板)")
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #262636;
                color: #38bdf8;
                font-weight: bold;
                text-align: left;
                padding: 6px 10px;
                border: 1px solid #3b3b4f;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #313147; color: #7dd3fc; }
        """)
        self.toggle_btn.clicked.connect(self.toggle)
        main_layout.addWidget(self.toggle_btn)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(6)
        main_layout.addWidget(self.container)

        self.is_collapsed = False

    def toggle(self):
        self.is_collapsed = not self.is_collapsed
        self.container.setVisible(not self.is_collapsed)
        if self.is_collapsed:
            self.toggle_btn.setText("▼ 基础配置与设置 (点击展开面板)")
        else:
            self.toggle_btn.setText("▲ 基础配置与设置 (点击收起面板)")


# ==================== 5. ROI 覆盖层与右上角控制栏 ====================
class PersistentROIOverlay(QWidget):
    """网页内识别框 Overlay。

    识别框是 QWebEngineView 的子控件，只覆盖本软件里的网页区域，
    不再创建全屏置顶窗口，因此打开其它软件时不会遮挡其它软件。
    OCR 仍使用真实屏幕坐标抓取，ROI 坐标保持兼容旧配置。
    """
    roi_list_selected = Signal(list)
    clear_alarm_requested = Signal(int)
    point_selected = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rects = []
        self.is_editing = False
        self.is_picking_point = False
        # 非编辑状态下，Overlay 本身不拦截网页鼠标事件，保证网页可以正常点击、滚动、输入。
        # 右上角按钮等子控件仍可正常接收点击。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.boxes_visible = True
        self.alarm_states = {}
        self.drag_idx = None
        self.drag_action = None
        self.drag_start_screen = None
        self.orig_rect = None
        self.current_draw_rect = None
        self.alarm_buttons = {}
        self.countdown_text = "⏱️ 操作: -- | OCR检测: --"

        self.bar = QWidget(self)
        self.bar.setStyleSheet("""
            QWidget { background:#181825; border:1px solid #45475a; border-radius:6px; }
            QLabel { color:#a6e3a1; font-weight:bold; font-size:11px; }
            QPushButton { background:#313244; color:#fff; border:1px solid #45475a;
                          border-radius:4px; padding:4px 8px; font-size:11px; font-weight:bold; }
            QPushButton:hover { background:#45475a; }
        """)
        bl=QHBoxLayout(self.bar); bl.setContentsMargins(8,4,8,4); bl.setSpacing(5)
        self.tip_label=QLabel("网页内调整：拖动框体移动 | 拖四角缩放 | 空白处划框")
        self.btn_done=QPushButton("✅ 完成")
        self.btn_clear=QPushButton("🗑️ 清空")
        self.btn_cancel=QPushButton("取消")
        self.btn_done.clicked.connect(self.finish_editing)
        self.btn_clear.clicked.connect(self.clear_rects)
        self.btn_cancel.clicked.connect(self.cancel_editing)
        bl.addWidget(self.tip_label,1); bl.addWidget(self.btn_done); bl.addWidget(self.btn_clear); bl.addWidget(self.btn_cancel)
        self.bar.hide()

        # 顶部控制栏由 MainWindow 的 FloatingControlBar 独立承载，避免 Overlay 透明鼠标导致按钮无法点击。

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_bars()

    def reposition_bars(self):
        self.bar.adjustSize()
        self.bar.move(max(8,(self.width()-self.bar.width())//2), 10)

    def update_countdown_text(self, text):
        self.countdown_text=text

    def toggle_boxes_visibility(self):
        self.boxes_visible=not self.boxes_visible
        self.update()

    def start_point_picker(self):
        if not hasattr(self, "roi_overlay"):
            return
        self.cancel_element_picker()
        self.roi_overlay.start_point_picker()
        self.pick_point_btn.setText("🎯 请在网页上点击...")
        self.log("📍 已进入点位拾取模式，请在网页上点击需要自动操作的位置")

    def on_point_selected(self, point):
        self.click_point = [point.x(), point.y()]
        self.config["click_point_space"] = "webview_local"
        self.click_point_label.setText(self.format_click_point())
        self.pick_point_btn.setText("📍 重新拾取点位")
        self.operation_action_combo.setCurrentIndex(max(0, self.operation_action_combo.findData("click")))
        self.log(f"📍 已拾取网页点位: ({point.x()}, {point.y()})")
        self.status_label.setText(f"已设置网页点击点位 ({point.x()}, {point.y()})")
        self.save_settings()

    def format_element_click(self):
        d = getattr(self, "element_click", {}) or {}
        if d.get("selector"):
            text = d.get("text", "")
            tag = d.get("tag", "element")
            if text:
                text = text[:28] + ("..." if len(text) > 28 else "")
                return f"🎯 当前元素: <{tag}> {text}"
            return f"🎯 当前元素: <{tag}> 已设置"
        return "🎯 当前元素: 未设置"

    def start_element_picker(self):
        try:
            if hasattr(self, "roi_overlay"):
                self.roi_overlay.finish_point_picker()
            self.element_picker_active = True
            self.pick_element_btn.setText("🎯 请在网页上点击元素...")
            self.status_label.setText("请在网页上移动鼠标预览，点击要自动操作的网页元素；按 Esc 取消")
            self.log("🎯 已进入网页元素拾取模式")
            self.webview.setFocus()
            self.webview.page().runJavaScript(ELEMENT_PICKER_SCRIPT)
            if self.element_picker_timer is None:
                self.element_picker_timer = QTimer(self)
                self.element_picker_timer.timeout.connect(self.poll_element_picker)
            self.element_picker_timer.start(200)
        except Exception as e:
            self.element_picker_active = False
            _log_exception("网页元素拾取启动异常", e)
            self.log(f"❌ 网页元素拾取启动失败: {e}")

    def poll_element_picker(self):
        if not self.element_picker_active:
            return
        self.webview.page().runJavaScript("window.__elementPickerResult || null", self.on_element_picker_result)

    def on_element_picker_result(self, result):
        if not self.element_picker_active or not result:
            return
        self.cancel_element_picker()
        if result.get("cancelled"):
            self.pick_element_btn.setText("🎯 拾取网页元素")
            self.status_label.setText("已取消网页元素拾取")
            self.log("↩️ 已取消网页元素拾取")
            return
        if not result.get("selector") and not result.get("xpath"):
            self.pick_element_btn.setText("🎯 拾取网页元素")
            self.status_label.setText("未获取到有效网页元素")
            return
        self.element_click = {
            "selector": result.get("selector", ""),
            "xpath": result.get("xpath", ""),
            "tag": result.get("tag", ""),
            "text": result.get("text", ""),
            "id": result.get("id", ""),
            "className": result.get("className", "")
        }
        self.element_click_label.setText(self.format_element_click())
        self.pick_element_btn.setText("🎯 重新拾取元素")
        self.operation_action_combo.setCurrentIndex(max(0, self.operation_action_combo.findData("element_click")))
        self.log(f"🎯 已拾取网页元素: <{self.element_click.get('tag','')}> {self.element_click.get('text','')[:50]}")
        self.log(f"   CSS: {self.element_click.get('selector','')}")
        self.status_label.setText("网页元素已设置")
        self.save_settings()

    def cancel_element_picker(self):
        self.element_picker_active = False
        if self.element_picker_timer is not None:
            self.element_picker_timer.stop()
        try:
            self.webview.page().runJavaScript("if(window.__vmElementPickerCleanup){window.__vmElementPickerCleanup();} true;")
        except Exception:
            pass
        if hasattr(self, "pick_element_btn"):
            self.pick_element_btn.setText("🎯 拾取网页元素")

    def _native_click_screen(self, x, y):
        """在 Windows 上对真实屏幕坐标执行鼠标左键点击。
        QTest.mouseClick(QWebEngineView, ...) 对 Chromium 内容区域并不总能产生真实网页输入事件，
        因此这里使用 Windows SendInput/mouse_event 走系统级鼠标输入，可靠性更高。
        """
        if sys.platform.startswith("win"):
            import ctypes
            user32 = ctypes.windll.user32
            # 移动到拾取位置并发送真实左键按下/释放。
            if not user32.SetCursorPos(int(x), int(y)):
                raise RuntimeError("SetCursorPos 失败")
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
            return
        # 非 Windows 仅作为开发环境回退。
        from PySide6.QtTest import QTest
        local = self.webview.mapFromGlobal(QPoint(int(x), int(y)))
        QTest.mouseClick(self.webview, Qt.LeftButton, Qt.NoModifier, local)

    def perform_scheduled_operation(self):
        action = self.operation_action_combo.currentData()
        if action == "click":
            if len(getattr(self, "click_point", [])) < 2:
                self.log("⚠️ 到时操作选择为点击拾取点位，但尚未拾取点位")
                self.status_label.setText("⚠️ 请先拾取点击点位")
                return
            try:
                local = QPoint(int(self.click_point[0]), int(self.click_point[1]))
                global_pos = self.webview.mapToGlobal(local)
                self.activateWindow()
                self.webview.setFocus()
                self._native_click_screen(global_pos.x(), global_pos.y())
                self.log(f"🖱️ 到时执行真实点击: 网页坐标({local.x()}, {local.y()}) → 屏幕坐标({global_pos.x()}, {global_pos.y()})")
                self.status_label.setText(f"🖱️ 已点击拾取点位 ({local.x()}, {local.y()})")
            except Exception as e:
                _log_exception("自动点击异常", e)
                self.log(f"❌ 自动点击异常: {e}")
        elif action == "element_click":
            data = getattr(self, "element_click", {}) or {}
            if not data.get("selector") and not data.get("xpath"):
                self.log("⚠️ 到时操作选择为网页元素点击，但尚未拾取元素")
                self.status_label.setText("⚠️ 请先拾取网页元素")
                return
            try:
                payload = json.dumps(data, ensure_ascii=False)
                script = ELEMENT_CLICK_SCRIPT % payload
                self.webview.page().runJavaScript(script, self.on_element_click_finished)
                self.log(f"🎯 到时执行网页元素点击: {data.get('tag','element')} {data.get('text','')[:40]}")
            except Exception as e:
                _log_exception("网页元素点击异常", e)
                self.log(f"❌ 网页元素点击异常: {e}")
        else:
            self.refresh_page()

    def on_element_click_finished(self, result):
        if result and result.get("ok"):
            self.status_label.setText("🎯 网页元素点击成功")
            self.log(f"✅ 网页元素点击成功: {result.get('text','')[:60]}")
        else:
            reason = (result or {}).get("reason", "元素不存在")
            self.status_label.setText(f"⚠️ 网页元素点击失败: {reason}")
            self.log(f"⚠️ 网页元素点击失败: {reason}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
        else:
            super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if self.is_fullscreen:
            self.showNormal()
            self.control_bar.show()
            self.is_fullscreen = False
        else:
            self.left_panel.hide()
            self.settings_open = False
            self.control_bar.hide()
            self.showFullScreen()
            self.is_fullscreen = True

    def closeEvent(self, event):
        event.accept()
        self.quit_app()

    def quit_app(self):
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        _write_runtime_log("程序开始执行正常退出流程")
        try:
            if hasattr(self, 'refresh_clock'): self.refresh_clock.stop()
            if hasattr(self, 'ocr_thread') and self.ocr_thread.isRunning():
                self.ocr_thread.quit()
                if not self.ocr_thread.wait(3000):
                    _write_runtime_log("OCR线程未在3秒内结束，交给Qt继续退出", "WARN")
            if hasattr(self, 'roi_clock_timer'): self.roi_clock_timer.stop()
            if hasattr(self, 'alarm_loop_timer'): self.alarm_loop_timer.stop()
            if hasattr(self, 'diagnostic_timer'): self.diagnostic_timer.stop()
            if hasattr(self, 'element_picker_timer') and self.element_picker_timer:
                self.element_picker_timer.stop()
            self.stop_alarm_audio()
            if hasattr(self, 'tray'): self.tray.hide()
            try:
                if _faulthandler_file:
                    _faulthandler_file.flush()
            except Exception:
                pass
            _write_runtime_log("程序正常执行退出流程完成")
            QApplication.quit()
        except Exception as e:
            _log_exception("程序退出流程异常", e)
            QApplication.quit()


# ==================== 8A. 网页元素拾取与自动点击 ====================
ELEMENT_PICKER_SCRIPT = r"""
(function() {
    try {
        if (window.__vmElementPickerCleanup) window.__vmElementPickerCleanup();
    } catch(e) {}

    const oldOutline = new WeakMap();
    let current = null;

    function cssEscapeSafe(v) {
        try { return CSS.escape(String(v)); } catch(e) { return String(v).replace(/[^a-zA-Z0-9_-]/g, '_'); }
    }
    function cssPath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + cssEscapeSafe(el.id);
        const parts = [];
        let node = el;
        while (node && node.nodeType === 1 && node !== document.body) {
            let part = node.tagName.toLowerCase();
            if (node.classList && node.classList.length) {
                const cls = Array.from(node.classList).filter(Boolean).slice(0, 2);
                if (cls.length) part += '.' + cls.map(cssEscapeSafe).join('.');
            }
            let sib = node, index = 1;
            while ((sib = sib.previousElementSibling)) {
                if (sib.tagName === node.tagName) index++;
            }
            part += ':nth-of-type(' + index + ')';
            parts.unshift(part);
            node = node.parentElement;
        }
        return parts.join(' > ');
    }
    function xpath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '//*[@id="' + String(el.id).replace(/"/g, '&quot;') + '"]';
        const parts = [];
        let node = el;
        while (node && node.nodeType === 1) {
            let index = 1;
            let sib = node.previousElementSibling;
            while (sib) { if (sib.tagName === node.tagName) index++; sib = sib.previousElementSibling; }
            parts.unshift(node.tagName.toLowerCase() + '[' + index + ']');
            node = node.parentElement;
        }
        return '/' + parts.join('/');
    }
    function describe(el) {
        if (!el || el.nodeType !== 1) return null;
        const text = ((el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ')).slice(0, 120);
        return {
            selector: cssPath(el),
            xpath: xpath(el),
            tag: el.tagName.toLowerCase(),
            text: text,
            id: el.id || '',
            className: typeof el.className === 'string' ? el.className.slice(0, 200) : ''
        };
    }
    function highlight(el) {
        if (current && current !== el) {
            const old = oldOutline.get(current);
            current.style.outline = old || '';
        }
        current = el;
        if (el && !oldOutline.has(el)) oldOutline.set(el, el.style.outline || '');
        if (el) el.style.outline = '3px solid #00e5ff';
    }
    function clearHighlight() {
        if (current) current.style.outline = oldOutline.get(current) || '';
        current = null;
    }
    function onOver(e) {
        e.stopPropagation();
        highlight(e.target && e.target.closest ? e.target.closest('*') : e.target);
    }
    function onClick(e) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        const el = e.target && e.target.closest ? e.target.closest('*') : e.target;
        window.__elementPickerResult = describe(el);
        cleanup();
    }
    function onKey(e) {
        if (e.key === 'Escape') {
            e.preventDefault(); e.stopPropagation();
            window.__elementPickerResult = {cancelled:true};
            cleanup();
        }
    }
    function cleanup() {
        document.removeEventListener('mouseover', onOver, true);
        document.removeEventListener('click', onClick, true);
        document.removeEventListener('keydown', onKey, true);
        clearHighlight();
        window.__vmElementPickerCleanup = null;
    }
    window.__elementPickerResult = null;
    window.__vmElementPickerCleanup = cleanup;
    document.addEventListener('mouseover', onOver, true);
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKey, true);
    return true;
})();
"""

ELEMENT_CLICK_SCRIPT = r"""
(function(data) {
    function findByXPath(path) {
        if (!path) return null;
        try { return document.evaluate(path, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; } catch(e) { return null; }
    }
    let el = null;
    try { if (data.selector) el = document.querySelector(data.selector); } catch(e) {}
    if (!el) el = findByXPath(data.xpath || '');
    if (!el) return {ok:false, reason:'元素不存在'};
    try { el.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
    try { el.focus(); } catch(e) {}
    try {
        if (typeof el.click === 'function') el.click();
        else el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}));
        return {ok:true, text:((el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ')).slice(0,80)};
    } catch(e) { return {ok:false, reason:String(e)}; }
})(%s)
"""

# ==================== 8. JS 自动填表与即时回车脚本 ====================
INJECT_SCRIPT = r"""
(function() {
    function simulateInput(target, value) {
        if (!target) return false;
        try { target.focus(); } catch(e){}
        try {
            let valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            valueSetter.call(target, value);
        } catch(e) { target.value = value; }
        target.dispatchEvent(new Event('input', { bubbles: true }));
        target.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }

    function triggerEnter(target) {
        if (!target) return;
        ['keydown', 'keypress', 'keyup'].forEach(function(eventType) {
            let ev = new KeyboardEvent(eventType, {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13
            });
            target.dispatchEvent(ev);
        });
    }

    function findAndFill(account, password) {
        let activeTarget = null;
        let pwdInputs = document.querySelectorAll('input[type="password"]');
        if (pwdInputs.length > 0) {
            for (let pwd of pwdInputs) {
                if (pwd.offsetParent !== null || pwd.offsetWidth > 0 || pwd.offsetHeight > 0) {
                    simulateInput(pwd, password);
                    activeTarget = pwd;
                    let form = pwd.form;
                    if (form) {
                        let formInputs = form.querySelectorAll('input:not([type="hidden"]):not([type="password"]):not([type="submit"]):not([type="button"])');
                        if (formInputs.length > 0) {
                            simulateInput(formInputs[formInputs.length - 1], account);
                            continue;
                        }
                    }
                    let txtInputs = document.querySelectorAll('input:not([type="hidden"]):not([type="password"])');
                    let bestMatch = null;
                    for (let txt of txtInputs) {
                        if (txt.offsetParent !== null || txt.offsetWidth > 0 || txt.offsetHeight > 0) {
                            let str = (txt.id + txt.className + txt.placeholder + txt.name).toLowerCase();
                            if (str.includes('user') || str.includes('name') || str.includes('acc') || str.includes('号')) {
                                bestMatch = txt; break;
                            }
                            if (!bestMatch) bestMatch = txt;
                        }
                    }
                    if (bestMatch) simulateInput(bestMatch, account);
                }
            }
        }

        triggerEnter(activeTarget || document.activeElement);
        return true;
    }

    window.__fillV7 = function(account, password) {
        findAndFill(account, password);
    };
})();
"""

if __name__ == "__main__":
    try:
        _write_runtime_log("开始创建 QApplication")
        # 高性能 Chromium：启用 GPU 与硬件合成，避免网页全部退回 CPU 渲染。
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        stable_flags = "--ignore-certificate-errors --enable-gpu --enable-gpu-compositing --disable-features=RendererCodeIntegrity"
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " " + stable_flags).strip()
    except Exception as e:
        _log_exception("启动环境初始化异常", e)
    app = QApplication(sys.argv)
    try:
        from PySide6.QtCore import qInstallMessageHandler
        def _qt_message_handler(mode, context, message):
            try:
                _write_runtime_log(f"Qt消息[{mode}]: {message} | {context.file}:{context.line}", "QT")
            except Exception:
                pass
        qInstallMessageHandler(_qt_message_handler)
    except Exception as e:
        _log_exception("Qt日志钩子安装失败", e)
    app.setQuitOnLastWindowClosed(True) 
    app.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    
    app.setStyleSheet("""
        QMainWindow, QWidget, QDialog { background-color: #1a1a24; color: #cdd6f4; }
        QGroupBox { font-weight: bold; border: 1px solid #3b3b4f; border-radius: 6px; margin-top: 8px; padding-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #38bdf8; }
        QLineEdit, QSpinBox, QComboBox { background-color: #262636; color: #ffffff; border: 1px solid #3b3b4f; border-radius: 4px; padding: 3px; }
        
        QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; border: none; }
        QSpinBox::up-arrow, QSpinBox::down-arrow { image: none; }

        QPushButton { background-color: #2d2d3f; color: #ffffff; border: 1px solid #474765; border-radius: 4px; padding: 4px; }
        QPushButton:hover { background-color: #3b3b54; }
        QLabel { color: #94a3b8; }
    """)
    
    try:
        _write_runtime_log("开始创建 MainWindow")
        win = MainWindow()
        win.show()
        _write_runtime_log("主窗口已显示，进入 Qt 事件循环")
        exit_code = app.exec()
        _write_runtime_log(f"Qt 事件循环结束，exit_code={exit_code}")
        sys.exit(exit_code)
    except Exception as e:
        _log_exception("主程序启动/事件循环异常", e)
        raise