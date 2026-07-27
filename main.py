# -*- coding: utf-8 -*-
"""
网页自动登录与刷新工具（V9.2 日志与缩放增强版）
包含功能：
 1. 填入框宽度拉长一倍，新增网页显示比例（缩放）调节
 2. 底部增加实时运行日志控制台，打印 ddddocr 识别数值与系统状态
 3. ddddocr 神经网络 + det 智能切块 + 内存流 (QBuffer/OpenCV) 零磁盘读写
 4. 交互式 ROI 透明遮罩框选监控区域
 5. 基于“状态变化”的报警逻辑（连续 3 行数字相同即触发变化报警）
 6. F11 隐藏控制栏完全全屏与定时刷新、一键贴码、二维码截图
依赖：PySide6, PySide6.QtWebEngineWidgets, ddddocr, opencv-python, numpy
"""

# ==================== 1. 标准库与第三方基础库导入 ====================
import sys
import json
import os
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import cv2

try:
    import ddddocr
    HAS_DDDDOCR = True
except ImportError:
    HAS_DDDDOCR = False

# ==================== 2. PySide6 核心组件导入 ====================
from PySide6.QtCore import QUrl, Qt, QTimer, QDateTime, QRect, QPoint, QBuffer, QIODevice, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLineEdit, QLabel, QSpinBox, QCheckBox,
    QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy, QFileDialog, QDialog,
    QComboBox, QTextEdit
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QTextCursor
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply, 
    QNetworkInterface, QAbstractSocket
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineCertificateError, QWebEngineScript

# ==================== 3. 基础配置与辅助函数 ====================
CONFIG_FILE = "auto_login_config.json"

def load_config():
    default = {
        "url": "https://example.com/login",
        "account": "",
        "password": "",
        "zoom_level": 1.0,
        "auto_refresh": False,
        "auto_interval": 60,
        "panel_collapsed": False,
        "screenshot_path": os.getcwd(),
        "selected_ip": "",
        "reminder_hours": 2, 
        "reminder_mins": 0,
        "reminder_loop": True,
        "reminder_sound_index": 0,
        "reminder_custom_path": "",
        "reminder_sound_count": 3,
        "roi_rect": [100, 100, 300, 200],
        "roi_monitor_enabled": False,
        "roi_interval_sec": 2
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default.update(data)
        except:
            pass
    return default

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置文件失败: {e}")

def get_all_local_ips():
    ip_list = []
    for address in QNetworkInterface.allAddresses():
        if address.protocol() == QAbstractSocket.IPv4Protocol:
            ip_str = address.toString()
            if ip_str != "127.0.0.1" and not ip_str.startswith("169.254"):
                ip_list.append(ip_str)
    if not ip_list:
        ip_list.append("127.0.0.1")
    return sorted(list(set(ip_list)))


# ==================== 4. ROI 交互框选组件 ====================
class ROIOverlay(QWidget):
    roi_selected = Signal(QRect)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.SubWindow | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.start_pos = None
        self.end_pos = None
        self.is_selecting = False

    def start_selection(self, global_geometry):
        self.setGeometry(global_geometry)
        self.start_pos = None
        self.end_pos = None
        self.is_selecting = False
        self.show()
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.is_selecting = False
            self.end_pos = event.pos()
            rect = QRect(self.start_pos, self.end_pos).normalized()
            self.hide()
            if rect.width() > 10 and rect.height() > 10:
                self.roi_selected.emit(rect)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        
        if self.is_selecting and self.start_pos and self.end_pos:
            rect = QRect(self.start_pos, self.end_pos).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#00ff66"), 2, Qt.DashLine))
            painter.drawRect(rect)


# ==================== 5. HTTP 服务器与扫码对话框 ====================
class ScreenshotHTTPHandler(BaseHTTPRequestHandler):
    latest_path = ""
    def log_message(self, format, *args): pass

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        requested_file = os.path.basename(parsed_url.path)
        if ScreenshotHTTPHandler.latest_path and os.path.exists(ScreenshotHTTPHandler.latest_path):
            if requested_file == os.path.basename(ScreenshotHTTPHandler.latest_path):
                try:
                    with open(ScreenshotHTTPHandler.latest_path, 'rb') as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except:
                    pass
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"File not found or expired.")


class QRDialog(QDialog):
    def __init__(self, parent, pixmap, url):
        super().__init__(parent)
        self.setWindowTitle("📸 手机扫码查看最新截图")
        self.setModal(True)
        self.resize(320, 390)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        img_label = QLabel()
        img_label.setPixmap(pixmap)
        img_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(img_label)
        
        tip_label = QLabel("📢 请确保【手机】与【电脑】连接在同一个 Wi-Fi 局域网下。\n扫码即可查看截图。")
        tip_label.setAlignment(Qt.AlignCenter)
        tip_label.setWordWrap(True)
        tip_label.setStyleSheet("color: #a6adc8; font-size: 12px; font-weight: bold;")
        layout.addWidget(tip_label)
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class CustomWebPage(QWebEnginePage):
    def certificateError(self, error: QWebEngineCertificateError) -> bool:
        error.acceptCertificate()
        return True


# ==================== 6. 主窗口核心业务逻辑 ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("网页自动登录与刷新工具 V9.2 (日志与缩放增强版)")
        self.resize(1320, 880)
        self.config = load_config()
        
        self.nam = QNetworkAccessManager(self)
        self.current_file_url = ""
        self.custom_sound_path = self.config.get("reminder_custom_path", "")
        self.is_fullscreen = False

        # 初始化 ddddocr 识别引擎
        self.std_ocr = None
        self.det_ocr = None
        self.init_ddddocr_engines()

        # ROI 监控变量
        roi_cfg = self.config.get("roi_rect", [50, 50, 300, 200])
        self.roi_rect = QRect(roi_cfg[0], roi_cfg[1], roi_cfg[2], roi_cfg[3])
        self.last_alarm_signature = None

        self.start_local_server()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 左侧控制面板 ----------
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(310) # 适当加宽确保美观
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)

        # 1. 页面地址与显示比例
        grp_url = QGroupBox("页面地址与显示比例")
        g_url = QVBoxLayout()
        self.url_input = QLineEdit(self.config["url"])
        g_url.addWidget(self.url_input)

        h_zoom = QHBoxLayout()
        h_zoom.addWidget(QLabel("网页缩放:"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(25, 300)
        self.zoom_spin.setSingleStep(10)
        self.zoom_spin.setSuffix("%")
        self.zoom_spin.setValue(int(self.config.get("zoom_level", 1.0) * 100))
        self.zoom_spin.setFixedWidth(90) # 拉长一倍 (90px)
        self.zoom_spin.valueChanged.connect(self.on_zoom_changed)
        h_zoom.addWidget(self.zoom_spin)
        h_zoom.addStretch()
        g_url.addLayout(h_zoom)

        self.load_btn = QPushButton("加载页面")
        self.load_btn.clicked.connect(self.load_page)
        g_url.addWidget(self.load_btn)
        grp_url.setLayout(g_url)
        left_layout.addWidget(grp_url)

        # 2. 账号密码凭证
        grp_auth = QGroupBox("账号密码")
        g_auth = QVBoxLayout()
        g_auth.addWidget(QLabel("账号:"))
        self.account_input = QLineEdit(self.config["account"])
        g_auth.addWidget(self.account_input)
        g_auth.addWidget(QLabel("密码:"))
        self.password_input = QLineEdit(self.config["password"])
        g_auth.addWidget(self.password_input)

        self.paste_btn = QPushButton("📋 一键粘贴账号密码")
        self.paste_btn.clicked.connect(self.paste_credentials)
        g_auth.addWidget(self.paste_btn)

        self.save_btn = QPushButton("💾 保存全部设置")
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setStyleSheet("font-weight: bold; background-color: #0284c7; color: white; height: 26px;")
        g_auth.addWidget(self.save_btn)
        grp_auth.setLayout(g_auth)
        left_layout.addWidget(grp_auth)

        # 3. 智能 ROI 框选监控 (ddddocr)
        grp_roi = QGroupBox("🎯 ROI 表格数字监控 (ddddocr)")
        g_roi = QVBoxLayout()

        h_roi_btn = QHBoxLayout()
        self.select_roi_btn = QPushButton("📐 框选监控区域")
        self.select_roi_btn.clicked.connect(self.start_roi_selection)
        h_roi_btn.addWidget(self.select_roi_btn)

        self.roi_toggle_btn = QPushButton("▶️ 开启监控")
        self.roi_toggle_btn.clicked.connect(self.toggle_roi_monitor)
        self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        h_roi_btn.addWidget(self.roi_toggle_btn)
        g_roi.addLayout(h_roi_btn)

        h_roi_cfg = QHBoxLayout()
        h_roi_cfg.addWidget(QLabel("检测间隔:"))
        self.roi_interval_spin = QSpinBox()
        self.roi_interval_spin.setRange(1, 60)
        self.roi_interval_spin.setValue(self.config.get("roi_interval_sec", 2))
        self.roi_interval_spin.setFixedWidth(90) # 拉长一倍 (90px)
        h_roi_cfg.addWidget(self.roi_interval_spin)
        h_roi_cfg.addWidget(QLabel("秒"))
        h_roi_cfg.addStretch()
        g_roi.addLayout(h_roi_cfg)

        self.roi_info_label = QLabel(f"坐标: {self.roi_rect.x()},{self.roi_rect.y()} [{self.roi_rect.width()}x{self.roi_rect.height()}]")
        self.roi_info_label.setStyleSheet("color: #38bdf8; font-size: 11px;")
        g_roi.addWidget(self.roi_info_label)

        self.roi_status_label = QLabel("状态: 未开启 (连续3行相同即报警)")
        self.roi_status_label.setStyleSheet("color: #fbbf24; font-size: 11px;")
        self.roi_status_label.setWordWrap(True)
        g_roi.addWidget(self.roi_status_label)

        grp_roi.setLayout(g_roi)
        left_layout.addWidget(grp_roi)

        # 4. 刷新控制
        grp_refresh = QGroupBox("刷新控制")
        g_refresh = QVBoxLayout()
        h_refresh = QHBoxLayout()
        self.refresh_btn = QPushButton("🔄 手动刷新")
        self.refresh_btn.clicked.connect(self.refresh_page)
        h_refresh.addWidget(self.refresh_btn)
        g_refresh.addLayout(h_refresh)

        h_auto = QHBoxLayout()
        h_auto.addWidget(QLabel("刷新时间间隔:"))
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 3600)
        self.auto_interval.setValue(self.config.get("auto_interval", 60))
        self.auto_interval.setFixedWidth(90) # 拉长一倍 (90px)
        self.auto_interval.valueChanged.connect(self.update_auto_interval)
        h_auto.addWidget(self.auto_interval)
        
        self.auto_refresh_cb = QCheckBox("自动刷新")
        self.auto_refresh_cb.stateChanged.connect(self.on_auto_refresh_changed)
        h_auto.addWidget(self.auto_refresh_cb)
        g_refresh.addLayout(h_auto)

        self.countdown_label = QLabel("")
        self.countdown_label.setStyleSheet("color: #0ea5e9; font-weight: bold;")
        g_refresh.addWidget(self.countdown_label)
        grp_refresh.setLayout(g_refresh)
        left_layout.addWidget(grp_refresh)

        # 5. 声音与提醒控制
        grp_reminder = QGroupBox("定时与声音控制")
        g_reminder = QVBoxLayout()
        
        h_rem_sound = QHBoxLayout()
        h_rem_sound.addWidget(QLabel("声音:"))
        self.sound_combo = QComboBox()
        self.sound_combo.addItems([
            "系统默认蜂鸣 (Beep)",
            "系统提示音 (Ding)",
            "系统通知音 (Notify)",
            "自定义 .wav 文件..."
        ])
        saved_sound_idx = self.config.get("reminder_sound_index", 0)
        self.sound_combo.setCurrentIndex(saved_sound_idx if saved_sound_idx < self.sound_combo.count() else 0)
        self.sound_combo.currentIndexChanged.connect(self.on_sound_selection_changed)
        h_rem_sound.addWidget(self.sound_combo, stretch=1)
        
        self.listen_btn = QPushButton("🎵")
        self.listen_btn.setFixedWidth(30)
        self.listen_btn.clicked.connect(lambda: self.trigger_alarm_sound(preview=True)) 
        h_rem_sound.addWidget(self.listen_btn)
        g_reminder.addLayout(h_rem_sound)
        
        h_count_loop = QHBoxLayout()
        h_count_loop.addWidget(QLabel("响铃次数:"))
        self.rem_count = QSpinBox()
        self.rem_count.setRange(1, 99)
        self.rem_count.setValue(self.config.get("reminder_sound_count", 3))
        self.rem_count.setFixedWidth(90) # 拉长一倍 (90px)
        h_count_loop.addWidget(self.rem_count)
        h_count_loop.addStretch()
        g_reminder.addLayout(h_count_loop)
        grp_reminder.setLayout(g_reminder)
        left_layout.addWidget(grp_reminder)

        # 6. 截图与扫码
        grp_snap = QGroupBox("截图与扫码")
        g_snap = QVBoxLayout()
        h_ip = QHBoxLayout()
        h_ip.addWidget(QLabel("本机IP:"))
        self.ip_combo = QComboBox()
        h_ip.addWidget(self.ip_combo, stretch=1)
        g_snap.addLayout(h_ip)
        
        self.snap_btn = QPushButton("📸 一键截图 + 二维码")
        self.snap_btn.clicked.connect(self.take_screenshot)
        self.snap_btn.setStyleSheet("font-weight: bold; background-color: #10b981; color: white;")
        g_snap.addWidget(self.snap_btn)
        grp_snap.setLayout(g_snap)
        left_layout.addWidget(grp_snap)

        # 7. 最下方运行日志输出面板
        grp_log = QGroupBox("📋 运行日志与识别数值")
        g_log = QVBoxLayout()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(150)
        self.log_box.setStyleSheet("font-size: 11px; background-color: #11111b; color: #a6adc8; border: 1px solid #313244;")
        g_log.addWidget(self.log_box)
        grp_log.setLayout(g_log)
        left_layout.addWidget(grp_log)

        # 状态栏
        self.status_label = QLabel("系统就绪")
        left_layout.addWidget(self.status_label)

        # ---------- 中间折叠栏 ----------
        self.toggle_bar = QWidget()
        self.toggle_bar.setFixedWidth(14)
        toggle_layout = QVBoxLayout(self.toggle_bar)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        self.toggle_btn = QPushButton("<")
        self.toggle_btn.clicked.connect(self.toggle_left_panel)
        self.toggle_btn.setStyleSheet("""
            QPushButton { background-color: #334155; color: #ffffff; border: none; font-weight: bold; }
            QPushButton:hover { background-color: #475569; }
        """)
        self.toggle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        toggle_layout.addWidget(self.toggle_btn)

        # ---------- 右侧：浏览器 ----------
        self.webview = QWebEngineView()
        self.custom_page = CustomWebPage(self.webview)
        self.webview.setPage(self.custom_page)
        self.webview.setZoomFactor(self.config.get("zoom_level", 1.0))
        self.setup_user_script()
        self.webview.loadFinished.connect(self.on_load_finished)

        main_layout.addWidget(self.left_panel)
        main_layout.addWidget(self.toggle_bar)
        main_layout.addWidget(self.webview, stretch=1)

        # ---------- 定时器与 Overlay ----------
        self.refresh_clock = QTimer()
        self.refresh_clock.timeout.connect(self.on_refresh_clock_tick)
        self.remaining_seconds = 0

        self.alarm_loop_timer = QTimer()
        self.alarm_loop_timer.timeout.connect(self.execute_single_alarm_tick)
        self.alarm_remaining_counts = 0

        # ROI 监控定时器
        self.roi_monitor_timer = QTimer()
        self.roi_monitor_timer.timeout.connect(self.perform_roi_ocr_check)

        # 框选 Overlay
        self.roi_overlay = ROIOverlay()
        self.roi_overlay.roi_selected.connect(self.on_roi_selected)

        # ---------- 系统托盘 ----------
        self.tray = QSystemTrayIcon(QIcon.fromTheme("face-smile"), self)
        tray_menu = QMenu()
        tray_menu.addAction("显示窗口", self.show)
        tray_menu.addAction("完全退出程序", self.quit_app)
        self.tray.setContextMenu(tray_menu)
        self.tray.show()

        # ---------- 恢复设置 ----------
        self.auto_refresh_cb.setChecked(self.config.get("auto_refresh", False))
        self.refresh_ip_list()
        if self.config.get("panel_collapsed", False):
            self.left_panel.setVisible(False)
            self.toggle_btn.setText(">")
        
        self.log("🚀 系统初始化完成，软件已就绪")
        if self.config["url"]:
            self.load_page()

    def log(self, text):
        """将输出打印到最下方的日志窗口"""
        time_str = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.log_box.append(f"[{time_str}] {text}")
        self.log_box.moveCursor(QTextCursor.End)

    def init_ddddocr_engines(self):
        """初始化 ddddocr (神经网络分类与智能切块引擎)"""
        if HAS_DDDDOCR:
            try:
                self.std_ocr = ddddocr.DdddOcr(show_ad=False)
                self.det_ocr = ddddocr.DdddOcr(det=True, show_ad=False)
                print("✅ ddddocr 神经网络及 Detection 切块引擎加载成功！")
            except Exception as e:
                print(f"⚠️ ddddocr 初始化失败: {e}")
        else:
            print("⚠️ 未检测到 ddddocr 模块")

    def start_local_server(self):
        self.server_port = 8999
        for port in range(8999, 9100):
            try:
                self.httpd = HTTPServer(('0.0.0.0', port), ScreenshotHTTPHandler)
                self.server_port = port
                threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
                break
            except:
                continue

    def refresh_ip_list(self):
        self.ip_combo.clear()
        ips = get_all_local_ips()
        self.ip_combo.addItems(ips)

    def setup_user_script(self):
        script = QWebEngineScript()
        script.setSourceCode(INJECT_SCRIPT)
        script.setName("AutoFillCascaderV7")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation) 
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        self.webview.page().profile().scripts().insert(script)

    def toggle_left_panel(self):
        if self.is_fullscreen: return
        is_visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not is_visible)
        self.toggle_btn.setText(">" if is_visible else "<")

    def on_zoom_changed(self, value):
        factor = value / 100.0
        self.webview.setZoomFactor(factor)
        self.config["zoom_level"] = factor

    def load_page(self):
        url = self.url_input.text().strip()
        if not url: return
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        self.log(f"🌐 正在加载页面: {url}")
        self.webview.load(QUrl(url))

    def on_load_finished(self, ok):
        if ok:
            self.log("✅ 页面加载完毕")
            self.webview.page().runJavaScript(INJECT_SCRIPT)
            QTimer.singleShot(1500, self.paste_credentials)
        else:
            self.log("❌ 页面加载失败，请检查网络")
            self.status_label.setText("页面加载失败")

    # ---------- ROI 框选与 ddddocr 识别核心 ----------
    def start_roi_selection(self):
        global_geom = self.webview.mapToGlobal(QPoint(0, 0))
        rect = QRect(global_geom, self.webview.size())
        self.roi_overlay.start_selection(rect)

    def on_roi_selected(self, rect):
        self.roi_rect = rect
        self.roi_info_label.setText(f"坐标: {rect.x()},{rect.y()} [{rect.width()}x{rect.height()}]")
        self.log(f"📐 重新设置 ROI 监控区域: [{rect.x()},{rect.y()} - {rect.width()}x{rect.height()}]")
        self.status_label.setText("✅ ROI 框选区域设置成功！")
        self.perform_roi_ocr_check()

    def toggle_roi_monitor(self):
        if self.roi_monitor_timer.isActive():
            self.roi_monitor_timer.stop()
            self.roi_toggle_btn.setText("▶️ 开启监控")
            self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
            self.roi_status_label.setText("状态: 监控已停止")
            self.log("⏹️ ROI 数字监控已停止")
        else:
            if not HAS_DDDDOCR or self.std_ocr is None:
                self.roi_status_label.setText("❌ 缺少 ddddocr 依赖！无法运行")
                self.log("❌ 缺失 ddddocr 模块，开启失败")
                return
            sec = self.roi_interval_spin.value()
            self.roi_monitor_timer.start(sec * 1000)
            self.roi_toggle_btn.setText("⏸️ 停止监控")
            self.roi_toggle_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
            self.roi_status_label.setText("状态: 正在监控中...")
            self.log(f"▶️ ROI 数字监控开启，间隔: {sec}秒")
            self.perform_roi_ocr_check()

    def perform_roi_ocr_check(self):
        if self.roi_rect.width() <= 0 or self.roi_rect.height() <= 0:
            return

        # 1. 抓取 ROI 图像并写入内存流 QBuffer
        pixmap = self.webview.grab(self.roi_rect)
        if pixmap.isNull():
            return

        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        img_bytes = buffer.data().tobytes()
        buffer.close()

        # 2. 内存流直接解码为 OpenCV 图像
        np_arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            return

        # 3. ddddocr Detection 智能文本区域切块识别
        row_digit_list = []
        try:
            bboxes = self.det_ocr.detection(img_bytes)
        except Exception:
            bboxes = []

        if bboxes:
            bboxes_sorted = sorted(bboxes, key=lambda b: (b[1], b[0]))
            grouped_rows = []
            current_row = []
            last_y = None

            for box in bboxes_sorted:
                x1, y1, x2, y2 = box
                if last_y is None or abs(y1 - last_y) < 12:
                    current_row.append(box)
                else:
                    grouped_rows.append(current_row)
                    current_row = [box]
                last_y = y1
            if current_row:
                grouped_rows.append(current_row)

            for row in grouped_rows:
                row_sorted = sorted(row, key=lambda b: b[0])
                row_text = ""
                for box in row_sorted:
                    x1, y1, x2, y2 = box
                    h, w, _ = img.shape
                    x1_c, y1_c = max(0, x1 - 2), max(0, y1 - 2)
                    x2_c, y2_c = min(w, x2 + 2), min(h, y2 + 2)
                    
                    crop_img = img[y1_c:y2_c, x1_c:x2_c]
                    if crop_img.size == 0: continue

                    _, crop_bytes = cv2.imencode(".png", crop_img)
                    char_res = self.std_ocr.classification(crop_bytes.tobytes())
                    digits = "".join(filter(str.isdigit, char_res))
                    row_text += digits
                if row_text:
                    row_digit_list.append(row_text)
        else:
            res_str = self.std_ocr.classification(img_bytes)
            for line in res_str.splitlines():
                d = "".join(filter(str.isdigit, line))
                if d: row_digit_list.append(d)

        # 打印数值到底部日志框
        if row_digit_list:
            self.log(f"🎯 识别到行数值: {row_digit_list}")
        else:
            self.log("🔍 监控区域内未识别到有效数字")

        # 4. 判断逻辑：“有3行靠在一起的相同”
        matched_value = None
        has_3_consecutive_same = False
        match_start_idx = -1

        for i in range(len(row_digit_list) - 2):
            val1 = row_digit_list[i]
            val2 = row_digit_list[i+1]
            val3 = row_digit_list[i+2]
            if val1 and val1 == val2 and val2 == val3:
                has_3_consecutive_same = True
                matched_value = val1
                match_start_idx = i
                break

        # 5. 基于“变化”的报警逻辑
        current_signature = f"{match_start_idx}:{matched_value}" if has_3_consecutive_same else None

        if has_3_consecutive_same:
            if current_signature != self.last_alarm_signature:
                msg = f"🚨 发现 3 行相同数字 [{matched_value}] (状态更新报警！)"
                self.roi_status_label.setText(f"状态: {msg}")
                self.status_label.setText(msg)
                self.log(f"⚠️ 【触发报警】连续3行数值均为 [{matched_value}]！")
                self.trigger_alarm_sound()
                self.last_alarm_signature = current_signature
            else:
                self.roi_status_label.setText(f"状态: 连续3行相同 [{matched_value}] (已报警，等待新变化...)")
        else:
            self.last_alarm_signature = None
            display_str = " -> ".join(row_digit_list[-3:]) if row_digit_list else "无有效数字"
            self.roi_status_label.setText(f"状态: 监控中 | 最新行数据: [{display_str}]")

    # ---------- 音频控制与辅助函数 ----------
    def on_sound_selection_changed(self, index):
        if index == 3: 
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择自定义报警铃声", self.custom_sound_path or os.getcwd(), "音频文件 (*.wav)"
            )
            if file_path:
                self.custom_sound_path = file_path
                self.log(f"🎵 已设定自定义铃声路径")
            else:
                self.sound_combo.setCurrentIndex(0)

    def trigger_alarm_sound(self, preview=False):
        self.alarm_loop_timer.stop() 
        self.alarm_remaining_counts = 1 if preview else self.rem_count.value()
        if self.alarm_remaining_counts <= 0: return
        self.execute_single_alarm_tick()
        if self.alarm_remaining_counts > 0:
            self.alarm_loop_timer.start(1200)

    def execute_single_alarm_tick(self):
        if self.alarm_remaining_counts <= 0:
            self.alarm_loop_timer.stop()
            return
            
        self.alarm_remaining_counts -= 1
        index = self.sound_combo.currentIndex()
        
        if index == 0:
            QApplication.beep()
        else:
            target_wav = ""
            if index == 1: target_wav = r"C:\Windows\Media\ding.wav"
            elif index == 2: target_wav = r"C:\Windows\Media\notify.wav"
            elif index == 3: target_wav = self.custom_sound_path

            if target_wav and os.path.exists(target_wav):
                if sys.platform == "win32":
                    import winsound
                    winsound.PlaySound(target_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
                else:
                    QApplication.beep()
            else:
                QApplication.beep()
                
        if self.alarm_remaining_counts <= 0:
            self.alarm_loop_timer.stop()

    def paste_credentials(self):
        account = self.account_input.text().strip()
        password = self.password_input.text().strip()
        if not account or not password: return
        safe_account = json.dumps(account)
        safe_password = json.dumps(password)
        js_cmd = f"if (typeof window.__fillV7 === 'function') {{ window.__fillV7({safe_account}, {safe_password}); }}"
        self.webview.page().runJavaScript(js_cmd)

    def take_screenshot(self):
        target_dir = self.config.get("screenshot_path", os.getcwd())
        filename = f"screenshot_{QDateTime.currentDateTime().toString('yyyyMMdd_hhmmss')}.png"
        full_save_path = os.path.join(target_dir, filename)

        pixmap = self.webview.grab()
        if pixmap.save(full_save_path, "PNG"):
            ScreenshotHTTPHandler.latest_path = full_save_path
            selected_ip = self.ip_combo.currentText()
            self.current_file_url = f"http://{selected_ip}:{self.server_port}/{filename}"
            qr_api = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(self.current_file_url)}"
            self.reply = self.nam.get(QNetworkRequest(QUrl(qr_api)))
            self.reply.finished.connect(self.on_qr_downloaded)
            self.log(f"📸 截图已保存并生成二维码，本地链接: {self.current_file_url}")

    def on_qr_downloaded(self):
        if self.reply.error() == QNetworkReply.NoError:
            qr_pixmap = QPixmap()
            qr_pixmap.loadFromData(self.reply.readAll())
            QRDialog(self, qr_pixmap, self.current_file_url).exec()
        self.reply.deleteLater()

    def save_settings(self):
        self.config["url"] = self.url_input.text().strip()
        self.config["account"] = self.account_input.text().strip()
        self.config["password"] = self.password_input.text().strip()
        self.config["zoom_level"] = self.zoom_spin.value() / 100.0
        self.config["auto_refresh"] = self.auto_refresh_cb.isChecked()
        self.config["auto_interval"] = self.auto_interval.value()
        self.config["selected_ip"] = self.ip_combo.currentText() 
        self.config["reminder_sound_index"] = self.sound_combo.currentIndex()
        self.config["reminder_custom_path"] = self.custom_sound_path
        self.config["reminder_sound_count"] = self.rem_count.value()
        self.config["roi_rect"] = [self.roi_rect.x(), self.roi_rect.y(), self.roi_rect.width(), self.roi_rect.height()]
        self.config["roi_interval_sec"] = self.roi_interval_spin.value()
        
        save_config(self.config)
        self.log("💾 配置文件保存成功！")
        self.status_label.setText("所有设置已保存固化")

    def refresh_page(self):
        self.log("🔄 触发网页刷新...")
        self.webview.reload()

    def on_auto_refresh_changed(self, state):
        if self.auto_refresh_cb.isChecked(): self.start_auto_timer()
        else: self.stop_auto_timer()

    def update_auto_interval(self):
        if self.auto_refresh_cb.isChecked(): self.start_auto_timer()

    def start_auto_timer(self):
        self.remaining_seconds = self.auto_interval.value()
        self.refresh_clock.start(1000)
        self.log(f"⏱️ 自动刷新开启，时间间隔: {self.remaining_seconds}秒")

    def stop_auto_timer(self):
        self.refresh_clock.stop()
        self.countdown_label.setText("")
        self.log("⏱️ 自动刷新已停止")

    def on_refresh_clock_tick(self):
        if self.remaining_seconds > 1:
            self.remaining_seconds -= 1
            self.countdown_label.setText(f"下次刷新: {self.remaining_seconds}秒")
        else:
            self.refresh_page()
            self.remaining_seconds = self.auto_interval.value()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
        else:
            super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if self.is_fullscreen:
            self.showNormal()
            self.left_panel.show()
            self.toggle_bar.show()
            self.is_fullscreen = False
        else:
            self.left_panel.hide()
            self.toggle_bar.hide()
            self.showFullScreen()
            self.is_fullscreen = True

    def closeEvent(self, event):
        event.accept()
        self.quit_app()

    def quit_app(self):
        if hasattr(self, 'refresh_clock'): self.refresh_clock.stop()
        if hasattr(self, 'roi_monitor_timer'): self.roi_monitor_timer.stop()
        if hasattr(self, 'alarm_loop_timer'): self.alarm_loop_timer.stop()
        self.tray.hide()
        QApplication.quit()
        try:
            current_pid = os.getpid()
            if sys.platform == "win32":
                subprocess.Popen(f"taskkill /F /T /PID {current_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(os.getpgrp(), signal.SIGKILL)
        except: pass
        os._exit(0)


# ==================== 7. 脚本注入定义与启动主入口 ====================
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
    function findAndFill(account, password) {
        let pwdInputs = document.querySelectorAll('input[type="password"]');
        if (pwdInputs.length > 0) {
            for (let pwd of pwdInputs) {
                if (pwd.offsetParent !== null || pwd.offsetWidth > 0 || pwd.offsetHeight > 0) {
                    simulateInput(pwd, password);
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
        return true;
    }
    window.__fillV7 = function(account, password) {
        findAndFill(account, password);
    };
})();
"""

if __name__ == "__main__":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--ignore-certificate-errors"
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True) 
    app.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    
    app.setStyleSheet("""
        QMainWindow, QWidget, QDialog { background-color: #1a1a24; color: #cdd6f4; }
        QGroupBox { font-weight: bold; border: 1px solid #3b3b4f; border-radius: 6px; margin-top: 10px; padding-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #38bdf8; }
        QLineEdit, QSpinBox, QComboBox { background-color: #262636; color: #ffffff; border: 1px solid #3b3b4f; border-radius: 4px; padding: 3px; }
        QPushButton { background-color: #2d2d3f; color: #ffffff; border: 1px solid #474765; border-radius: 4px; padding: 4px; }
        QPushButton:hover { background-color: #3b3b54; }
        QLabel { color: #94a3b8; }
    """)
    
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
