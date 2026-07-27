# -*- coding: utf-8 -*-
"""
网页刷新数字监控 (V10.3 - 选框常驻/报警变红/循环响铃/数值变化报警版)
更新日志：
 1. 选框常驻显示：划定区域后选框在屏幕上实时可见，正常为绿框，报警时变为红框。
 2. 交互式消除报警：报警选框右上角悬浮【🔕 消除报警】按钮，点击即可消除该区域报警并停止响铃。
 3. 智能变化报警机制：记录已消除的数值快照，只有当表格下方增加新数据/数值发生变化且满足条件时才再次报警。
 4. 循环播放报警音：触发报警后持续响铃，直到用户点击消除报警。
 5. 界面精简：移除控制面板上的数值文本显示，识别结果全部集中在运行日志中输出。
依赖：PySide6, PySide6.QtWebEngineWidgets, ddddocr, opencv-python, numpy
"""

import sys
import json
import os
import re
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
    DDDDOCR_ERR_MSG = str(e)

# ==================== 2. PySide6 核心组件导入 ====================
from PySide6.QtCore import QUrl, Qt, QTimer, QDateTime, QRect, QPoint, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLineEdit, QLabel, QSpinBox, QCheckBox,
    QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy, QFileDialog, QDialog,
    QComboBox, QTextEdit
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QTextCursor, QGuiApplication, QImage
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
        "reminder_sound_index": 0,
        "reminder_custom_path": "",
        "reminder_sound_count": 3,
        "roi_list": [[100, 100, 300, 200]],
        "roi_interval_sec": 2,
        "target_same_count": 3,
        "target_value": ""
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


# ==================== 4. 联动折叠组件 ====================
class CombinedCollapsiblePanel(QWidget):
    """ 将【页面设置与刷新】和【账号密码】合并管理，支持上下箭头折叠 """
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        self.toggle_btn = QPushButton("▲ 页面设置与账号密码 (点击收起)")
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
            self.toggle_btn.setText("▼ 页面设置与账号密码 (点击展开)")
        else:
            self.toggle_btn.setText("▲ 页面设置与账号密码 (点击收起)")


# ==================== 5. 常驻屏幕 ROI 覆盖层 (需求四：选框常驻/变红/消除按钮) ====================
class PersistentROIOverlay(QWidget):
    roi_list_selected = Signal(list)
    clear_alarm_requested = Signal(int) # 传递要消除报警的选框编号 (1-based)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.rects = []
        self.is_editing = False
        self.start_pos = None
        self.end_pos = None
        
        # 记录每个选框的状态: {box_idx: True/False}
        self.alarm_states = {}
        self.clear_btn_rects = {} # 记录消除按钮的绘制点击区域

        # 框选编辑时的顶部浮条
        self.bar = QWidget(self)
        self.bar.setStyleSheet("background-color: #1e1e2e; border: 1px solid #45475a; border-radius: 6px;")
        bar_layout = QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(10, 5, 10, 5)

        self.tip_label = QLabel("鼠标拖拽绘制框选 | 已画: 0 个", self.bar)
        self.tip_label.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 12px;")

        self.btn_done = QPushButton("✅ 完成框选", self.bar)
        self.btn_done.setStyleSheet("background-color: #10b981; color: white; font-weight: bold; padding: 3px 10px;")
        self.btn_done.clicked.connect(self.finish_editing)

        self.btn_clear = QPushButton("🗑️ 清空", self.bar)
        self.btn_clear.setStyleSheet("background-color: #ef4444; color: white; padding: 3px 10px;")
        self.btn_clear.clicked.connect(self.clear_rects)

        self.btn_cancel = QPushButton("取消", self.bar)
        self.btn_cancel.setStyleSheet("background-color: #4b5563; color: white; padding: 3px 10px;")
        self.btn_cancel.clicked.connect(self.cancel_editing)

        bar_layout.addWidget(self.tip_label)
        bar_layout.addSpacing(10)
        bar_layout.addWidget(self.btn_done)
        bar_layout.addWidget(self.btn_clear)
        bar_layout.addWidget(self.btn_cancel)

        self.show_fullscreen_overlay()

    def show_fullscreen_overlay(self):
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        self.setGeometry(geo)
        self.show()

    def start_editing(self, existing_rects):
        self.is_editing = True
        self.rects = list(existing_rects)
        self.setCursor(Qt.CrossCursor)
        self.bar.show()
        self.update_tip()
        
        geo = self.geometry()
        self.bar.adjustSize()
        self.bar.move((geo.width() - self.bar.width()) // 2, 20)
        
        self.raise_()
        self.activateWindow()
        self.update()

    def finish_editing(self):
        self.is_editing = False
        self.setCursor(Qt.ArrowCursor)
        self.bar.hide()
        self.roi_list_selected.emit(self.rects)
        self.update()

    def cancel_editing(self):
        self.is_editing = False
        self.setCursor(Qt.ArrowCursor)
        self.bar.hide()
        self.update()

    def clear_rects(self):
        self.rects.clear()
        self.update_tip()
        self.update()

    def update_tip(self):
        self.tip_label.setText(f"鼠标拖拽绘制框选 | 已画: {len(self.rects)} 个")

    def set_alarm_states(self, states):
        self.alarm_states = states
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.globalPosition().toPoint()

            # 非编辑状态下，响应“消除报警”按钮的点击
            if not self.is_editing:
                for box_idx, btn_r in self.clear_btn_rects.items():
                    if btn_r.contains(pos):
                        self.clear_alarm_requested.emit(box_idx)
                        return
                return

            # 编辑状态下，拖拽框选
            if self.bar.geometry().contains(self.mapFromGlobal(pos)):
                return
            self.start_pos = pos
            self.end_pos = pos
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_editing and self.start_pos:
            self.end_pos = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_editing and self.start_pos:
            self.end_pos = event.globalPosition().toPoint()
            rect = QRect(self.start_pos, self.end_pos).normalized()
            if rect.width() > 10 and rect.height() > 10:
                self.rects.append(rect)
                self.update_tip()
            self.start_pos = None
            self.end_pos = None
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        self.clear_btn_rects.clear()

        # 编辑状态下绘制遮罩背景
        if self.is_editing:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        # 绘制所有选框
        for i, r in enumerate(self.rects, 1):
            local_r = QRect(self.mapFromGlobal(r.topLeft()), r.size())
            is_alarm = self.alarm_states.get(i, False)

            if self.is_editing:
                painter.setCompositionMode(QPainter.CompositionMode_Clear)
                painter.fillRect(local_r, Qt.transparent)
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # 需求四：报警变红，正常变绿
            if is_alarm:
                border_color = QColor("#ef4444") # 红色
                pen_width = 3
            else:
                border_color = QColor("#00ff66") # 绿色
                pen_width = 2

            painter.setPen(QPen(border_color, pen_width, Qt.SolidLine))
            painter.drawRect(local_r)

            # 选框编号角标
            badge_rect = QRect(local_r.x(), max(0, local_r.y() - 22), 42, 22)
            painter.fillRect(badge_rect, border_color)
            painter.setPen(QPen(QColor("#000000")))
            painter.drawText(badge_rect, Qt.AlignCenter, f"#{i}")

            # 需求四：如果该选框报警，在旁边绘制【🔕 消除报警】浮动按钮
            if is_alarm:
                btn_w, btn_h = 90, 22
                btn_x = local_r.x() + local_r.width() - btn_w
                btn_y = max(0, local_r.y() - 25)
                
                # 记录全局坐标，用于点击事件响应
                global_btn_r = QRect(r.x() + r.width() - btn_w, max(0, r.y() - 25), btn_w, btn_h)
                self.clear_btn_rects[i] = global_btn_r

                local_btn_r = QRect(btn_x, btn_y, btn_w, btn_h)
                painter.fillRect(local_btn_r, QColor("#ef4444"))
                painter.setPen(QPen(QColor("#ffffff")))
                painter.drawText(local_btn_r, Qt.AlignCenter, "🔕 消除报警")

        # 正在拖拽中的虚线框
        if self.is_editing and self.start_pos and self.end_pos:
            local_start = self.mapFromGlobal(self.start_pos)
            local_end = self.mapFromGlobal(self.end_pos)
            rect = QRect(local_start, local_end).normalized()

            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            painter.setPen(QPen(QColor("#38bdf8"), 2, Qt.DashLine))
            painter.drawRect(rect)


# ==================== 6. HTTP 服务器与扫码对话框 ====================
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
        
        tip_label = QLabel("📢 请确保【手机】与【电脑】连接在同一个 Wi-Fi 局域网下。")
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


# ==================== 7. 主窗口核心业务逻辑 ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("网页刷新数字监控 (V10.3 - 常驻选框/循环报警/数据变化响应版)")
        self.resize(1380, 880)
        self.config = load_config()

        if os.path.exists("1.ico"):
            self.setWindowIcon(QIcon("1.ico"))

        self.nam = QNetworkAccessManager(self)
        self.current_file_url = ""
        self.custom_sound_path = self.config.get("reminder_custom_path", "")
        self.is_fullscreen = False

        self.ocr = None
        self.init_ddddocr_engines()

        # 加载多 ROI 区域列表
        saved_roi_list = self.config.get("roi_list", [[100, 100, 300, 200]])
        self.roi_list = [QRect(r[0], r[1], r[2], r[3]) for r in saved_roi_list]

        # 需求二：报警状态与数据变化跟踪字典
        self.box_latest_digits = {}  # {box_idx: ['0.193', ...]} 最新提取数据
        self.box_ack_digits = {}     # {box_idx: ['0.193', ...]} 已消除报警的数据快照
        self.box_is_alarming = {}    # {box_idx: True/False} 当前报警状态

        self.start_local_server()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 左侧控制面板 ----------
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(350)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)

        # 【一与二：联动折叠区域】
        self.combined_panel = CombinedCollapsiblePanel()
        
        # 1.1 页面设置与刷新 GroupBox
        grp_url = QGroupBox("一. 页面设置与刷新")
        g_url = QVBoxLayout(grp_url)
        
        h_url = QHBoxLayout()
        self.url_input = QLineEdit(self.config["url"])
        self.url_input.setPlaceholderText("请输入网页地址...")
        self.url_input.returnPressed.connect(self.load_page)
        self.load_btn = QPushButton("🌐")
        self.load_btn.setToolTip("加载页面")
        self.load_btn.setFixedWidth(36)
        self.load_btn.clicked.connect(self.load_page)
        h_url.addWidget(self.url_input)
        h_url.addWidget(self.load_btn)
        g_url.addLayout(h_url)

        h_opts = QHBoxLayout()
        h_opts.addWidget(QLabel("缩放:"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(25, 300)
        self.zoom_spin.setSingleStep(10)
        self.zoom_spin.setSuffix("%")
        self.zoom_spin.setValue(int(self.config.get("zoom_level", 1.0) * 100))
        self.zoom_spin.setFixedWidth(65)
        self.zoom_spin.valueChanged.connect(self.on_zoom_changed)
        h_opts.addWidget(self.zoom_spin)

        h_opts.addWidget(QLabel("刷新间隔:"))
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 3600)
        self.auto_interval.setValue(self.config.get("auto_interval", 60))
        self.auto_interval.setFixedWidth(65)
        self.auto_interval.valueChanged.connect(self.update_auto_interval)
        h_opts.addWidget(self.auto_interval)
        
        self.auto_refresh_cb = QCheckBox("自动")
        self.auto_refresh_cb.stateChanged.connect(self.on_auto_refresh_changed)
        h_opts.addWidget(self.auto_refresh_cb)

        g_url.addLayout(h_opts)

        self.countdown_label = QLabel("")
        self.countdown_label.setStyleSheet("color: #0ea5e9; font-weight: bold; font-size: 11px;")
        g_url.addWidget(self.countdown_label)

        self.combined_panel.container_layout.addWidget(grp_url)

        # 1.2 账号密码 GroupBox
        grp_auth = QGroupBox("二. 账号密码与配置保存")
        g_auth = QVBoxLayout(grp_auth)
        
        h_creds = QHBoxLayout()
        self.account_input = QLineEdit(self.config["account"])
        self.account_input.setPlaceholderText("账号")
        self.password_input = QLineEdit(self.config["password"])
        self.password_input.setPlaceholderText("密码")
        h_creds.addWidget(self.account_input)
        h_creds.addWidget(self.password_input)
        g_auth.addLayout(h_creds)

        h_btns = QHBoxLayout()
        self.paste_btn = QPushButton("📋 一键粘贴")
        self.paste_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.paste_btn.clicked.connect(self.paste_credentials)
        
        self.save_btn = QPushButton("💾 保存配置")
        self.save_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setStyleSheet("font-weight: bold; background-color: #0284c7; color: white;")
        
        h_btns.addWidget(self.paste_btn)
        h_btns.addWidget(self.save_btn)
        g_auth.addLayout(h_btns)

        self.combined_panel.container_layout.addWidget(grp_auth)
        left_layout.addWidget(self.combined_panel)

        # 3. 🎯 数字监控面板 (需求三：精简删除 roi_result_label)
        grp_roi = QGroupBox("🎯 数字监控 (常驻选框/变化报警)")
        g_roi = QVBoxLayout()

        h_roi_top = QHBoxLayout()
        self.select_roi_btn = QPushButton("📐 框选数字区域")
        self.select_roi_btn.clicked.connect(self.start_roi_selection)
        h_roi_top.addWidget(self.select_roi_btn)

        self.clear_roi_btn = QPushButton("🗑️ 清空选框")
        self.clear_roi_btn.clicked.connect(self.clear_all_rois)
        h_roi_top.addWidget(self.clear_roi_btn)

        self.manual_trigger_btn = QPushButton("🔍 手动检测")
        self.manual_trigger_btn.setStyleSheet("background-color: #3b82f6; color: white; font-weight: bold;")
        self.manual_trigger_btn.clicked.connect(self.on_manual_detect_clicked)
        h_roi_top.addWidget(self.manual_trigger_btn)

        g_roi.addLayout(h_roi_top)

        h_rule1 = QHBoxLayout()
        h_rule1.addWidget(QLabel("相同行数:"))
        self.target_same_count_spin = QSpinBox()
        self.target_same_count_spin.setRange(1, 10)
        self.target_same_count_spin.setValue(self.config.get("target_same_count", 3))
        self.target_same_count_spin.setFixedWidth(80)
        h_rule1.addWidget(self.target_same_count_spin)

        h_rule1.addWidget(QLabel("目标数值:"))
        self.target_value_input = QLineEdit(self.config.get("target_value", ""))
        self.target_value_input.setPlaceholderText("例如: 0.193")
        h_rule1.addWidget(self.target_value_input)
        g_roi.addLayout(h_rule1)

        h_roi_cfg = QHBoxLayout()
        self.roi_toggle_btn = QPushButton("▶️ 定时检测")
        self.roi_toggle_btn.clicked.connect(self.toggle_roi_monitor)
        self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        h_roi_cfg.addWidget(self.roi_toggle_btn)

        h_roi_cfg.addWidget(QLabel("检测间隔:"))
        self.roi_interval_spin = QSpinBox()
        self.roi_interval_spin.setRange(1, 60)
        self.roi_interval_spin.setValue(self.config.get("roi_interval_sec", 2))
        self.roi_interval_spin.setFixedWidth(80)
        h_roi_cfg.addWidget(self.roi_interval_spin)
        h_roi_cfg.addWidget(QLabel("秒"))
        g_roi.addLayout(h_roi_cfg)

        # 需求二：控制面板全局“消除报警”按钮
        self.clear_alarm_panel_btn = QPushButton("🚨 消除所有报警")
        self.clear_alarm_panel_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold; padding: 5px;")
        self.clear_alarm_panel_btn.clicked.connect(self.clear_all_alarms)
        g_roi.addWidget(self.clear_alarm_panel_btn)

        self.roi_info_label = QLabel(f"选中区域: {len(self.roi_list)} 个选框")
        self.roi_info_label.setStyleSheet("color: #38bdf8; font-size: 11px;")
        g_roi.addWidget(self.roi_info_label)

        self.roi_status_label = QLabel("状态: 待检测")
        self.roi_status_label.setStyleSheet("color: #fbbf24; font-size: 11px;")
        self.roi_status_label.setWordWrap(True)
        g_roi.addWidget(self.roi_status_label)

        grp_roi.setLayout(g_roi)
        left_layout.addWidget(grp_roi)

        # 4. 声音与提醒控制
        grp_reminder = QGroupBox("声音与提醒控制")
        g_reminder = QVBoxLayout()
        
        h_rem_sound = QHBoxLayout()
        h_rem_sound.addWidget(QLabel("声音:"))
        self.sound_combo = QComboBox()
        self.sound_combo.addItems([
            "默认蜂鸣 (Beep)",
            "系统提示音 (Ding)",
            "系统通知音 (Notify)",
            "自定义 .wav..."
        ])
        saved_sound_idx = self.config.get("reminder_sound_index", 0)
        self.sound_combo.setCurrentIndex(saved_sound_idx if saved_sound_idx < self.sound_combo.count() else 0)
        self.sound_combo.currentIndexChanged.connect(self.on_sound_selection_changed)
        h_rem_sound.addWidget(self.sound_combo, stretch=1)
        
        self.listen_btn = QPushButton("🎵 试听")
        self.listen_btn.clicked.connect(lambda: self.trigger_single_sound()) 
        h_rem_sound.addWidget(self.listen_btn)

        g_reminder.addLayout(h_rem_sound)
        grp_reminder.setLayout(g_reminder)
        left_layout.addWidget(grp_reminder)

        # 5. 截图与扫码
        grp_snap = QGroupBox("截图与扫码")
        g_snap = QHBoxLayout()
        
        g_snap.addWidget(QLabel("本机IP:"))
        self.ip_combo = QComboBox()
        g_snap.addWidget(self.ip_combo, stretch=1)
        
        self.snap_btn = QPushButton("📸 一键截图+二维码")
        self.snap_btn.clicked.connect(self.take_screenshot)
        self.snap_btn.setStyleSheet("font-weight: bold; background-color: #10b981; color: white;")
        g_snap.addWidget(self.snap_btn)
        
        grp_snap.setLayout(g_snap)
        left_layout.addWidget(grp_snap)

        # 6. 📋 运行日志面板
        grp_log = QGroupBox("📋 运行日志")
        g_log = QVBoxLayout()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(150)
        self.log_box.setStyleSheet("font-size: 11px; background-color: #11111b; color: #a6adc8; border: 1px solid #313244;")
        g_log.addWidget(self.log_box)
        grp_log.setLayout(g_log)
        left_layout.addWidget(grp_log, stretch=1)

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

        # 需求二：无线循环播放响铃定时器 (间隔 1.2 秒)
        self.alarm_loop_timer = QTimer()
        self.alarm_loop_timer.timeout.connect(self.execute_single_alarm_tick)

        self.roi_monitor_timer = QTimer()
        self.roi_monitor_timer.timeout.connect(self.perform_roi_ocr_check)

        # 需求四：常驻选框 Overlay
        self.roi_overlay = PersistentROIOverlay()
        self.roi_overlay.roi_list_selected.connect(self.on_roi_list_selected)
        self.roi_overlay.clear_alarm_requested.connect(self.clear_alarm_for_box)
        self.roi_overlay.rects = list(self.roi_list)

        # ---------- 系统托盘 ----------
        tray_icon = QIcon("1.ico") if os.path.exists("1.ico") else QIcon.fromTheme("face-smile")
        self.tray = QSystemTrayIcon(tray_icon, self)
        tray_menu = QMenu()
        tray_menu.addAction("显示窗口", self.show)
        tray_menu.addAction("完全退出程序", self.quit_app)
        self.tray.setContextMenu(tray_menu)
        self.tray.show()

        self.auto_refresh_cb.setChecked(self.config.get("auto_refresh", False))
        self.refresh_ip_list()
        
        self.log("🚀 系统初始化完成 (V10.3 - 常驻选框/循环报警/数据变化响应版)")
        if self.config["url"]:
            self.load_page()

    def log(self, text):
        time_str = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.log_box.append(f"[{time_str}] {text}")
        self.log_box.moveCursor(QTextCursor.End)

    def init_ddddocr_engines(self):
        if HAS_DDDDOCR:
            try:
                self.ocr = ddddocr.DdddOcr(show_ad=False)
                print("✅ ddddocr 识别引擎初始化成功！")
            except Exception as e:
                self.ocr = None
                print(f"⚠️ ddddocr 内部错误: {e}")
        else:
            print(f"⚠️ 未能加载 ddddocr 模块: {DDDDOCR_ERR_MSG}")

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

    # ---------- 选框相关操作 ----------
    def start_roi_selection(self):
        self.log("请在屏幕上拖拽划定【数字区域】...")
        self.roi_overlay.start_editing(self.roi_list)

    def clear_all_rois(self):
        self.roi_list.clear()
        self.box_latest_digits.clear()
        self.box_ack_digits.clear()
        self.box_is_alarming.clear()
        
        self.roi_overlay.rects = []
        self.roi_overlay.set_alarm_states({})
        self.roi_info_label.setText("选中区域: 0 个选框")
        self.roi_status_label.setText("状态: 选框已清空")
        self.stop_alarm_audio()
        self.log("🗑️ 已清空所有框选区域")

    def on_roi_list_selected(self, rects):
        self.roi_list = rects
        self.roi_overlay.rects = list(self.roi_list)
        self.roi_info_label.setText(f"选中区域: {len(self.roi_list)} 个选框")
        self.log(f"📐 完成框选，共设定 {len(self.roi_list)} 个监控区域")
        self.status_label.setText(f"✅ 已设定 {len(self.roi_list)} 个选框")
        self.perform_roi_ocr_check()

    def on_manual_detect_clicked(self):
        self.log("👆 触发【手动检测】...")
        if not HAS_DDDDOCR or self.ocr is None:
            msg = f"❌ 缺失 ddddocr 模块 (原因: {DDDDOCR_ERR_MSG or '未初始化'})"
            self.roi_status_label.setText(f"状态: {msg}")
            self.log(msg)
            return

        if not self.roi_list:
            self.roi_status_label.setText("状态: ⚠️ 请先点击【📐 框选数字区域】划定范围！")
            self.log("⚠️ 区域未划定，无法执行检测！")
            return

        self.perform_roi_ocr_check()

    def toggle_roi_monitor(self):
        if self.roi_monitor_timer.isActive():
            self.roi_monitor_timer.stop()
            self.roi_toggle_btn.setText("▶️ 定时检测")
            self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
            self.roi_status_label.setText("状态: 定时检测已停止")
            self.log("⏹️ 定时检测已停止")
        else:
            if not HAS_DDDDOCR or self.ocr is None:
                err = DDDDOCR_ERR_MSG or "未安装 ddddocr 依赖"
                self.roi_status_label.setText(f"❌ 缺失 ddddocr 模块 ({err})")
                self.log(f"❌ 开启失败: {err}")
                return

            if not self.roi_list:
                self.roi_status_label.setText("状态: ⚠️ 请先框选区域！")
                self.log("⚠️ 无法开启定时检测：尚未划定任何 ROI 区域")
                return

            sec = self.roi_interval_spin.value()
            self.roi_monitor_timer.start(sec * 1000)
            self.roi_toggle_btn.setText("⏸️ 停止定时")
            self.roi_toggle_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
            self.roi_status_label.setText("状态: 正在定时检测中...")
            self.log(f"▶️ 定时检测开启，间隔: {sec}秒")
            self.perform_roi_ocr_check()

    def segment_rows(self, img_bgr):
        """ Otsu 自适应二值化水平投影分割 """
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        if np.mean(gray) > 127:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        proj = np.sum(binary, axis=1)
        rows = []
        in_row = False
        start_y = 0
        min_row_height = 4

        for y, val in enumerate(proj):
            if val > 0 and not in_row:
                in_row = True
                start_y = y
            elif val == 0 and in_row:
                in_row = False
                if (y - start_y) >= min_row_height:
                    rows.append((start_y, y))

        if in_row and (len(proj) - start_y) >= min_row_height:
            rows.append((start_y, len(proj)))

        merged_rows = []
        for r in rows:
            if not merged_rows:
                merged_rows.append(r)
            else:
                prev_s, prev_e = merged_rows[-1]
                if r[0] - prev_e <= 5:
                    merged_rows[-1] = (prev_s, r[1])
                else:
                    merged_rows.append(r)

        return merged_rows

    # ==================== 核心 OCR 识别与独立匹配算法 ====================
    def perform_roi_ocr_check(self):
        if not self.roi_list:
            return

        if not HAS_DDDDOCR or self.ocr is None:
            return

        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio()

        target_same_n = self.target_same_count_spin.value()
        user_target_val = self.target_value_input.text().strip()

        log_lines = []

        try:
            for box_idx, rect in enumerate(self.roi_list, 1):
                if rect.width() <= 0 or rect.height() <= 0: continue

                phys_x = int(rect.x() * dpr)
                phys_y = int(rect.y() * dpr)
                phys_w = int(rect.width() * dpr)
                phys_h = int(rect.height() * dpr)

                pixmap = screen.grabWindow(0, phys_x, phys_y, phys_w, phys_h)
                if pixmap.isNull() or pixmap.width() == 0: continue

                qimg = pixmap.toImage().convertToFormat(QImage.Format_RGB888)
                h, w = qimg.height(), qimg.width()
                bpl = qimg.bytesPerLine()

                arr = np.array(qimg.bits()).reshape((h, bpl))
                arr = arr[:, :w * 3].reshape((h, w, 3))
                img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

                row_rects = self.segment_rows(img_bgr)
                if not row_rects:
                    row_rects = [(0, img_bgr.shape[0])]

                box_digits = []

                for start_y, end_y in row_rects:
                    row_img = img_bgr[start_y:end_y, :]
                    rh, rw = row_img.shape[:2]
                    if rh < 3 or rw < 3: continue

                    row_resized = cv2.resize(row_img, (rw * 3, rh * 3), interpolation=cv2.INTER_CUBIC)
                    row_padded = cv2.copyMakeBorder(row_resized, 15, 15, 20, 20, cv2.BORDER_CONSTANT, value=[255, 255, 255])

                    _, buf = cv2.imencode(".png", row_padded)
                    raw_text = self.ocr.classification(buf.tobytes())

                    raw_text_clean = raw_text.replace(',', '.').replace(':', '.')
                    found_numbers = re.findall(r'\d+\.?\d*', raw_text_clean)
                    if found_numbers:
                        box_digits.append(found_numbers[0])

                # 记录最新识别数据
                self.box_latest_digits[box_idx] = box_digits
                val_str = " | ".join(box_digits) if box_digits else "无"
                log_lines.append(f"【区域#{box_idx}】: [{val_str}]")

                # 判断规则匹配
                rule_matched = False
                matched_val = None

                if user_target_val:
                    if box_digits.count(user_target_val) >= target_same_n:
                        rule_matched = True
                        matched_val = user_target_val
                else:
                    for i in range(len(box_digits) - target_same_n + 1):
                        sub_group = box_digits[i : i + target_same_n]
                        if sub_group and all(x == sub_group[0] for x in sub_group):
                            rule_matched = True
                            matched_val = sub_group[0]
                            break

                # 需求二：智能报警判定 (比较当前数据与已消除的快照)
                ack_digits = self.box_ack_digits.get(box_idx, None)

                if rule_matched:
                    # 如果数据与已消除的快照不相同，说明有新数据产生，触发报警！
                    if box_digits != ack_digits:
                        self.box_is_alarming[box_idx] = True
                else:
                    # 不满足报警规则时，复位该框报警状态与快照
                    self.box_is_alarming[box_idx] = False
                    self.box_ack_digits[box_idx] = None

            # 需求三：运行日志输出识别数值
            self.log(f"🎯 方式一识别结果:\n" + "\n".join(log_lines))

            # 需求四：更新选框颜色 (变红/变绿)
            self.roi_overlay.set_alarm_states(self.box_is_alarming)

            # 判断是否有任意区域在报警
            alarming_boxes = [idx for idx, is_al in self.box_is_alarming.items() if is_al]
            if alarming_boxes:
                msg = f"🚨 区域 {alarming_boxes} 触发报警！"
                self.roi_status_label.setText(f"状态: {msg}")
                self.status_label.setText(msg)
                
                # 需求二：无限循环播放声音
                if not self.alarm_loop_timer.isActive():
                    self.alarm_loop_timer.start(1200)
                    self.execute_single_alarm_tick()
            else:
                self.stop_alarm_audio()
                rule_tip = f"目标值 '{user_target_val}'" if user_target_val else f"单区域连续 {target_same_n} 行相同"
                self.roi_status_label.setText(f"状态: 监控中 | 规则: {rule_tip}")

        except Exception as e:
            self.log(f"❌ OCR 运行异常: {e}")

    # ---------- 需求二 & 需求四：消除报警逻辑 ----------
    def clear_alarm_for_box(self, box_idx):
        """ 消除指定选框的报警，并锁定当前数值快照 """
        self.box_is_alarming[box_idx] = False
        self.box_ack_digits[box_idx] = list(self.box_latest_digits.get(box_idx, []))
        
        self.log(f"🔕 已消除【区域 #{box_idx}】的报警 (已锁数据，新数据产生前不再提醒)")
        
        # 刷新 Overlay 框颜色
        self.roi_overlay.set_alarm_states(self.box_is_alarming)
        
        # 如果没有其他框在报警，停止响铃
        if not any(self.box_is_alarming.values()):
            self.stop_alarm_audio()
            self.roi_status_label.setText("状态: 报警已消除，监控中...")

    def clear_all_alarms(self):
        """ 消除所有选框的报警 """
        for box_idx in list(self.box_is_alarming.keys()):
            self.box_is_alarming[box_idx] = False
            self.box_ack_digits[box_idx] = list(self.box_latest_digits.get(box_idx, []))

        self.roi_overlay.set_alarm_states(self.box_is_alarming)
        self.stop_alarm_audio()
        self.roi_status_label.setText("状态: 已消除所有报警")
        self.log("🔕 已消除所有区域的报警")

    def stop_alarm_audio(self):
        if self.alarm_loop_timer.isActive():
            self.alarm_loop_timer.stop()

    def execute_single_alarm_tick(self):
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

    def trigger_single_sound(self):
        self.execute_single_alarm_tick()

    def on_sound_selection_changed(self, index):
        if index == 3: 
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择自定义报警铃声", self.custom_sound_path or os.getcwd(), "音频文件 (*.wav)"
            )
            if file_path:
                self.custom_sound_path = file_path
                self.log("🎵 已设定自定义铃声路径")
            else:
                self.sound_combo.setCurrentIndex(0)

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
            self.log(f"📸 截图已保存，生成链接: {self.current_file_url}")

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
        
        self.config["roi_list"] = [[r.x(), r.y(), r.width(), r.height()] for r in self.roi_list]
        self.config["roi_interval_sec"] = self.roi_interval_spin.value()
        self.config["target_same_count"] = self.target_same_count_spin.value()
        self.config["target_value"] = self.target_value_input.text().strip()
        
        save_config(self.config)
        self.log("💾 配置文件保存成功！")
        self.status_label.setText("设置已保存")

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
        self.log(f"⏱️ 自动刷新开启，间隔: {self.remaining_seconds}秒")

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


# ==================== 8. JS 自动填表与启动程序入口 ====================
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
        QGroupBox { font-weight: bold; border: 1px solid #3b3b4f; border-radius: 6px; margin-top: 8px; padding-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #38bdf8; }
        QLineEdit, QSpinBox, QComboBox { background-color: #262636; color: #ffffff; border: 1px solid #3b3b4f; border-radius: 4px; padding: 3px; }
        QPushButton { background-color: #2d2d3f; color: #ffffff; border: 1px solid #474765; border-radius: 4px; padding: 4px; }
        QPushButton:hover { background-color: #3b3b54; }
        QLabel { color: #94a3b8; }
    """)
    
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
