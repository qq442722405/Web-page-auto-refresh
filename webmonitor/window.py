# -*- coding: utf-8 -*-
"""主窗口：界面装配 + 业务流程编排。

界面部分只负责“显示与交互”，网页操作一律通过 PageController，
OCR 通过 OCRWorker，报警规则通过 AlarmTracker —— 三者各自独立、可单独测试。
"""
import glob
import json
import os
import sys
import time
import urllib.parse

from PySide6.QtCore import QDateTime, QEvent, QPoint, QRect, Qt, QTimer, QUrl, Signal, Slot, QThread
from PySide6.QtGui import QIcon, QPixmap, QTextCursor, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QPushButton, QScrollArea,
    QSpinBox, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .alarm import AlarmTracker, build_rule
from .autofill import INJECT_SCRIPT
from .config import save as save_config
from .gate import OperationGate
from .logging_setup import log_exception, process_memory_mb, write_log
from .ocr_worker import HAS_DDDDOCR, DDDDOCR_ERROR, OCRWorker
from .page_controller import CHANNEL_LABELS, OperationError, PageController
from .paths import ICON_FILE
from .web_page import MonitorWebPage
from . import win_input

ACTION_REFRESH = "refresh"
ACTION_CLICK = "click"
ACTION_BACKGROUND = "background_click"


class MainWindow(QMainWindow):
    # OCR worker -> 主线程
    ocr_requested = Signal(object)

    def __init__(self, cfg, cdp_port=0):
        super().__init__()
        self.config = cfg
        self.cdp_port = cdp_port
        self.setWindowTitle("网页刷新数字监控 (V25 - 后台执行优化版)")
        self.resize(1380, 880)

        self._quitting = False
        self.settings_open = False
        self.control_bar_user_moved = False
        self.is_fullscreen = False
        self.web_loading = False
        self.last_reload_time = 0.0
        self.picker_action = None
        self.custom_sound_path = self.config.get("reminder_custom_path", "")
        self.sound_files_map = {}
        self.current_file_url = ""

        self.roi_list = []
        self.click_point = list(self.config.get("click_point", []))
        self.tracker = AlarmTracker()
        self.operation_gate = OperationGate()
        self.ocr_busy = False

        if os.path.exists(ICON_FILE):
            self.setWindowIcon(QIcon(ICON_FILE))

        self._build_ui()
        self._build_web()
        self._build_timers()
        self._build_overlay()
        self._build_tray()
        self._build_ocr()
        self._restore_state()

        self.auto_operation_cb.setChecked(bool(self.config.get("auto_refresh", False)))
        self.refresh_ip_list()
        self.update_countdown_bar()

        self.log("🚀 系统初始化完成：后台执行优先使用 Chromium 调试通道")
        write_log("通道状态：%s" % self.controller.describe())
        if self.config.get("url"):
            self.load_page()

    # ================================================================ 界面 ==
    def _build_ui(self):
        from .ui_overlay import FloatingControlBar
        from PySide6.QtNetwork import QNetworkAccessManager

        self.nam = QNetworkAccessManager(self)
        from .screenshot_service import ScreenshotService
        self.screenshot_service = ScreenshotService()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 右侧浮层设置面板 ----------
        self.left_panel = QScrollArea()
        self.left_panel.setWidgetResizable(True)
        self.left_panel.setFixedWidth(430)
        self.left_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel_content = QWidget()
        self.left_panel.setWidget(panel_content)
        panel_layout = QVBoxLayout(panel_content)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)
        self.left_panel.setStyleSheet("""
            QScrollArea { background: #181825; border: 1px solid #45475a; }
            QGroupBox { font-size: 12px; font-weight: bold; padding-top: 16px; margin-top: 8px; }
            QLabel, QCheckBox, QComboBox, QLineEdit, QSpinBox { font-size: 12px; }
            QPushButton { min-height: 24px; padding: 4px; }
            QPushButton:disabled, QComboBox:disabled { color: #7f849c; background: #313244; }
        """)

        panel_layout.addWidget(self._build_page_group())
        panel_layout.addWidget(self._build_background_group())
        panel_layout.addWidget(self._build_account_group())
        panel_layout.addWidget(self._build_sound_group())
        panel_layout.addWidget(self._build_snapshot_group())
        panel_layout.addWidget(self._build_monitor_group())
        panel_layout.addWidget(self._build_log_group())
        panel_layout.addWidget(self._build_save_button())
        self.status_label = QLabel("系统就绪")
        panel_layout.addWidget(self.status_label)

        # ---------- 右上角控制栏 ----------
        self.control_bar = FloatingControlBar(central, self.toggle_overlay_visibility,
                                              self.toggle_settings_panel)
        self.control_bar.moved.connect(self._remember_control_bar_moved)
        self.control_bar.show()
        self.control_bar.raise_()

    def _group_box(self, title):
        group = QGroupBox(title)
        group.setLayout(QVBoxLayout())
        group.layout().setContentsMargins(8, 6, 8, 6)
        group.layout().setSpacing(6)
        return group

    def _build_page_group(self):
        group = self._group_box("一. 页面设置")

        row = QHBoxLayout()
        self.url_input = QLineEdit(self.config.get("url", ""))
        self.url_input.setPlaceholderText("请输入网页地址...")
        self.url_input.returnPressed.connect(self.load_page)
        self.load_btn = QPushButton("🌐")
        self.load_btn.setToolTip("加载页面")
        self.load_btn.setFixedWidth(36)
        self.load_btn.clicked.connect(self.load_page)
        row.addWidget(self.url_input)
        row.addWidget(self.load_btn)
        group.layout().addLayout(row)

        options = QHBoxLayout()
        options.addWidget(QLabel("缩放:"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.zoom_spin.setRange(25, 300)
        self.zoom_spin.setValue(int(self.config.get("zoom_level", 1.0) * 100))
        self.zoom_spin.setFixedWidth(45)
        self.zoom_spin.valueChanged.connect(self.on_zoom_changed)
        options.addWidget(self.zoom_spin)
        options.addWidget(QLabel("%"))

        options.addWidget(QLabel("操作间隔:"))
        self.operation_interval = QSpinBox()
        self.operation_interval.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.operation_interval.setRange(1, 3600)
        self.operation_interval.valueChanged.connect(self.on_operation_interval_changed)
        options.addWidget(self.operation_interval)
        options.addWidget(QLabel("s"))

        self.auto_operation_cb = QCheckBox("自动操作")
        self.auto_operation_cb.stateChanged.connect(self.on_auto_operation_changed)
        options.addWidget(self.auto_operation_cb)
        group.layout().addLayout(options)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("到时操作:"))
        self.operation_action_combo = QComboBox()
        self.operation_action_combo.addItem("🔄 刷新网页", ACTION_REFRESH)
        self.operation_action_combo.addItem("🖱️ 前台点击（移动鼠标）", ACTION_CLICK)
        self.operation_action_combo.addItem("🕶️ 后台点击（不打扰操作）", ACTION_BACKGROUND)
        self.operation_action_combo.setCurrentIndex(
            max(0, self.operation_action_combo.findData(self.config.get("operation_action"))))
        action_row.addWidget(self.operation_action_combo, 1)
        self.pick_point_btn = QPushButton("📍 拾取点击点位")
        self.pick_point_btn.clicked.connect(self.start_point_picker)
        action_row.addWidget(self.pick_point_btn)
        group.layout().addLayout(action_row)

        self.click_point_label = QLabel()
        self.click_point_label.setStyleSheet("color:#38bdf8;font-size:12px;")
        self.click_point_label.setWordWrap(True)
        group.layout().addWidget(self.click_point_label)

        self.test_operation_btn = QPushButton("🧪 测试一次当前操作")
        self.test_operation_btn.clicked.connect(self.perform_scheduled_operation)
        group.layout().addWidget(self.test_operation_btn)

        self.operation_result_label = QLabel("就绪")
        self.operation_result_label.setWordWrap(True)
        group.layout().addWidget(self.operation_result_label)

        self.countdown_label = QLabel("")
        self.countdown_label.setStyleSheet("color: #0ea5e9; font-weight: bold; font-size: 11px;")
        group.layout().addWidget(self.countdown_label)

        self.operation_action_combo.currentIndexChanged.connect(self.on_operation_mode_changed)
        self.operation_interval.setValue(int(self.config.get("operation_interval", 60)))
        self.update_operation_ui()
        return group

    def _build_background_group(self):
        group = self._group_box("二. 后台执行（前置也能刷新）")
        row = QHBoxLayout()
        row.addWidget(QLabel("执行通道:"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem("🤖 智能（调试通道→Qt事件→置前点击）", "auto")
        self.strategy_combo.addItem("⚡ 仅调试通道（CDP，最稳定）", "cdp")
        self.strategy_combo.addItem("🧩 仅 Qt 事件", "qt")
        self.strategy_combo.addItem("🖱️ 仅真实置前点击", "native")
        self.strategy_combo.setCurrentIndex(
            max(0, self.strategy_combo.findData(self.config.get("background_strategy", "auto"))))
        self.strategy_combo.currentIndexChanged.connect(self.on_strategy_changed)
        row.addWidget(self.strategy_combo, 1)
        group.layout().addLayout(row)

        row2 = QHBoxLayout()
        self.temp_foreground_cb = QCheckBox("通道失败时允许短暂置前点击（自动还原焦点）")
        self.temp_foreground_cb.setToolTip(
            "仅在调试通道与 Qt 事件都失败时使用：把窗口推到最前点击一下，"
            "随后立刻把焦点和鼠标位置还原给用户原来的程序")
        self.temp_foreground_cb.setChecked(bool(self.config.get("allow_temporary_foreground", True)))
        self.temp_foreground_cb.stateChanged.connect(self.on_background_option_changed)
        row2.addWidget(self.temp_foreground_cb, 1)
        group.layout().addLayout(row2)

        row3 = QHBoxLayout()
        self.keep_active_cb = QCheckBox("保持网页后台活跃（禁止 Chromium 降频）")
        self.keep_active_cb.setToolTip("随启动参数生效，修改后请重启本软件")
        self.keep_active_cb.setChecked(bool(self.config.get("keep_page_active", True)))
        self.keep_active_cb.stateChanged.connect(self.on_background_option_changed)
        row3.addWidget(self.keep_active_cb, 1)
        group.layout().addLayout(row3)

        row4 = QHBoxLayout()
        self.channel_state_btn = QPushButton("🔎 自检")
        self.channel_state_btn.setFixedWidth(64)
        self.channel_state_btn.clicked.connect(self.run_channel_self_test)
        row4.addWidget(self.channel_state_btn)
        self.channel_state_label = QLabel("尚未自检")
        self.channel_state_label.setWordWrap(True)
        self.channel_state_label.setStyleSheet("color:#94a3b8;font-size:11px;")
        row4.addWidget(self.channel_state_label, 1)
        group.layout().addLayout(row4)
        return group

    def _build_account_group(self):
        group = self._group_box("三. 账号密码")
        row = QHBoxLayout()
        self.account_btn = QPushButton("👤 账号")
        self.account_btn.setToolTip("设置账号和密码")
        self.account_btn.clicked.connect(self.open_account_dialog)
        row.addWidget(self.account_btn, 1)
        self.paste_btn = QPushButton("📋 一键粘贴 (自动按下回车)")
        self.paste_btn.setStyleSheet("background-color: #3b82f6; color: white; font-weight: bold; padding: 5px;")
        self.paste_btn.clicked.connect(self.paste_credentials)
        row.addWidget(self.paste_btn, 2)
        group.layout().addLayout(row)
        self._refresh_account_text()
        return group

    def _build_sound_group(self):
        group = self._group_box("四. 声音与报警提醒")
        row = QHBoxLayout()
        row.addWidget(QLabel("声音选择:"))
        self.sound_combo = QComboBox()
        self.populate_sound_options()
        self.sound_combo.currentIndexChanged.connect(self.on_sound_selection_changed)
        row.addWidget(self.sound_combo, 1)
        self.listen_btn = QPushButton("🎵 试听")
        self.listen_btn.clicked.connect(lambda: self.play_reminder_sound())
        row.addWidget(self.listen_btn)
        group.layout().addLayout(row)
        return group

    def _build_snapshot_group(self):
        group = self._group_box("五. 截图与扫码")
        row = QHBoxLayout()
        row.addWidget(QLabel("IP:"))
        self.ip_combo = QComboBox()
        row.addWidget(self.ip_combo, 1)
        self.snap_btn = QPushButton("📸 截图与扫码")
        self.snap_btn.clicked.connect(self.take_screenshot)
        self.snap_btn.setStyleSheet("font-weight: bold; background-color: #10b981; color: white; padding: 5px;")
        row.addWidget(self.snap_btn)
        group.layout().addLayout(row)
        return group

    def _build_monitor_group(self):
        group = self._group_box("🎯 数字监控（网页右上角 ⚙️ 打开设置）")
        layout = group.layout()

        top = QHBoxLayout()
        self.select_roi_btn = QPushButton("📐 框选调整选框")
        self.select_roi_btn.clicked.connect(self.start_roi_selection)
        top.addWidget(self.select_roi_btn)
        self.clear_roi_btn = QPushButton("🗑️ 清空选框")
        self.clear_roi_btn.clicked.connect(self.clear_all_rois)
        top.addWidget(self.clear_roi_btn)
        self.manual_trigger_btn = QPushButton("🔍 手动检测")
        self.manual_trigger_btn.setStyleSheet("background-color: #3b82f6; color: white; font-weight: bold;")
        self.manual_trigger_btn.clicked.connect(self.on_manual_detect_clicked)
        top.addWidget(self.manual_trigger_btn)
        layout.addLayout(top)

        rule = QHBoxLayout()
        rule.addWidget(QLabel("相同行数:"))
        self.target_same_count_spin = QSpinBox()
        self.target_same_count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.target_same_count_spin.setRange(1, 10)
        self.target_same_count_spin.setValue(int(self.config.get("target_same_count", 3)))
        self.target_same_count_spin.setFixedWidth(35)
        rule.addWidget(self.target_same_count_spin)
        rule.addWidget(QLabel("目标数值:"))
        self.target_value_input = QLineEdit(self.config.get("target_value", ""))
        self.target_value_input.setPlaceholderText("例如: 0.193")
        rule.addWidget(self.target_value_input)
        layout.addLayout(rule)

        detect = QHBoxLayout()
        self.roi_toggle_btn = QPushButton("▶️ 定时检测")
        self.roi_toggle_btn.clicked.connect(self.toggle_roi_monitor)
        self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        detect.addWidget(self.roi_toggle_btn)
        detect.addWidget(QLabel("检测周期跟随操作间隔"))
        layout.addLayout(detect)

        self.roi_countdown_label = QLabel("")
        self.roi_countdown_label.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px;")
        layout.addWidget(self.roi_countdown_label)

        self.clear_alarm_panel_btn = QPushButton("🚨 消除所有报警")
        self.clear_alarm_panel_btn.setStyleSheet(
            "background-color: #ef4444; color: white; font-weight: bold; padding: 5px;")
        self.clear_alarm_panel_btn.clicked.connect(self.clear_all_alarms)
        layout.addWidget(self.clear_alarm_panel_btn)

        self.roi_info_label = QLabel("选中区域: 0 个选框")
        self.roi_info_label.setStyleSheet("color: #38bdf8; font-size: 11px;")
        layout.addWidget(self.roi_info_label)

        self.roi_status_label = QLabel("状态: 待检测")
        self.roi_status_label.setStyleSheet("color: #fbbf24; font-size: 11px;")
        self.roi_status_label.setWordWrap(True)
        layout.addWidget(self.roi_status_label)
        return group

    def _build_log_group(self):
        group = self._group_box("📋 运行日志")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(120)
        self.log_box.setStyleSheet(
            "font-size: 11px; background-color: #11111b; color: #a6adc8; border: 1px solid #313244;")
        group.layout().addWidget(self.log_box)
        return group

    def _build_save_button(self):
        self.save_btn = QPushButton("💾 保存基础配置")
        self.save_btn.setStyleSheet(
            "font-weight: bold; background-color: #0284c7; color: white; padding: 6px; "
            "font-size: 12px; border-radius: 4px;")
        self.save_btn.clicked.connect(self.save_settings)
        return self.save_btn

    # ================================================================ 网页 ==
    def _build_web(self):
        try:
            profile = QWebEngineProfile.defaultProfile()
            profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
            profile.setPersistentCookiesPolicy(QWebEngineProfile.AllowPersistentCookies)
            write_log("WebEngine：Chromium 内核，磁盘缓存 + 持久化 Cookie")
        except Exception as exc:
            log_exception("WebEngine 初始化失败", exc)

        self.webview = QWebEngineView()
        self.reload_in_progress_count = 0
        page = MonitorWebPage(self.webview)
        self.webview.setPage(page)
        self.webview.setZoomFactor(float(self.config.get("zoom_level", 1.0)))
        try:
            settings = self.webview.settings()
            settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, False)
            settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, True)
        except Exception as exc:
            log_exception("WebEngine 设置失败", exc)

        self.controller = PageController(self.webview, self)
        self.controller.cdp_port = self.cdp_port
        self.controller.configure(
            cdp_enabled=bool(self.config.get("cdp_enabled", True)),
            cdp_port=self.cdp_port,
            strategy=self.config.get("background_strategy", "auto"),
            allow_temporary_foreground=bool(self.config.get("allow_temporary_foreground", True)),
        )
        self.controller.diagnosis.connect(self.log)

        self._install_user_script()
        self._attach_controller_session()
        self.webview.loadStarted.connect(self.on_load_started)
        self.webview.loadFinished.connect(self.on_load_finished)
        self.web_load_watchdog = QTimer(self)
        self.web_load_watchdog.setSingleShot(True)
        self.web_load_watchdog.timeout.connect(self.on_web_load_timeout)

        self.centralWidget().layout().addWidget(self.webview, 1)
        self.left_panel.setParent(self.centralWidget())
        self.left_panel.hide()
        self.control_bar.raise_()

    def _attach_controller_session(self):
        # 延迟预热一次调试通道，避免第一轮定时操作才开始握手，让首次点击更快。
        QTimer.singleShot(1200, self._warm_up_cdp)

    def _warm_up_cdp(self):
        if getattr(self, "_quitting", False):
            return
        try:
            self.controller.cdp_session(force=True)
        except Exception as exc:
            write_log("调试通道预热失败: %s" % exc, "WARN")

    def _install_user_script(self):
        script = QWebEngineScript()
        script.setSourceCode(INJECT_SCRIPT)
        script.setName("AutoFillCascaderV7")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        self.webview.page().profile().scripts().insert(script)

    def _build_timers(self):
        self.remaining_seconds = 0
        self.refresh_clock = QTimer(self)
        self.refresh_clock.timeout.connect(self.on_operation_clock_tick)

        self.roi_remaining_seconds = 0
        self.roi_clock_timer = QTimer(self)
        self.roi_clock_timer.timeout.connect(self.on_roi_clock_tick)

        self.alarm_loop_timer = QTimer(self)
        self.alarm_loop_timer.timeout.connect(self.play_reminder_sound)

        self.diagnostic_timer = QTimer(self)
        self.diagnostic_timer.timeout.connect(self.write_diagnostic_log)
        self.diagnostic_timer.start(60000)

        self.cancel_picker_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.cancel_picker_shortcut.activated.connect(self.cancel_point_picker)

    def _build_overlay(self):
        from .ui_overlay import PersistentROIOverlay

        self.roi_overlay = PersistentROIOverlay(self.webview)
        self.roi_overlay.roi_list_selected.connect(self.on_roi_list_selected)
        self.roi_overlay.clear_alarm_requested.connect(self.clear_alarm_for_box)
        self.roi_overlay.point_selected.connect(self.on_point_selected)
        self.roi_overlay.setGeometry(self.webview.rect())
        self.roi_overlay.raise_()
        self.webview.installEventFilter(self)

    def _build_tray(self):
        icon = QIcon(ICON_FILE) if os.path.exists(ICON_FILE) else QIcon.fromTheme("face-smile")
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        menu.addAction("显示窗口", self.showNormal)
        menu.addAction("完全退出程序", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _build_ocr(self):
        self.ocr_thread = QThread(self)
        self.ocr_thread.setObjectName("OCRWorker")
        self.ocr_worker = OCRWorker()
        self.ocr_worker.moveToThread(self.ocr_thread)
        self.ocr_worker.finished.connect(self.on_ocr_finished)
        self.ocr_worker.status.connect(self._on_ocr_status)
        self.ocr_requested.connect(self.ocr_worker.process)
        # started 由新线程内部发出，连接必须先于 start()，确保引擎在工作线程内初始化。
        self.ocr_thread.started.connect(self.ocr_worker.initialize)
        self.ocr_thread.start()

    # ============================================================ 配置迁移 ==
    def _restore_state(self):
        self.roi_list = [QRect(*values) for values in self.config.get("roi_list", [])]
        if self.roi_list and self.config.get("roi_space") != "webview_local":
            self._migrate_roi_to_local()
        self._migrate_click_point()
        self.roi_overlay.set_rects(self.roi_list)
        self._update_roi_info()
        self.update_operation_ui()

    def _migrate_roi_to_local(self):
        """旧版本保存的是屏幕绝对坐标，这里一次性换算为网页内坐标。"""
        migrated = []
        for rect in self.roi_list:
            point = self.webview.mapFromGlobal(QPoint(rect.x(), rect.y()))
            migrated.append(QRect(point.x(), point.y(), rect.width(), rect.height()))
        self.roi_list = migrated
        self.config["roi_list"] = [[r.x(), r.y(), r.width(), r.height()] for r in migrated]
        self.config["roi_space"] = "webview_local"
        save_config(self.config)
        self.log("♻️ 旧版屏幕坐标选框已转换为网页内坐标")

    # ================================================================ 日志 ==
    def log(self, text):
        try:
            text = str(text)
            write_log(text.replace("\n", " | "))
            box = getattr(self, "log_box", None)
            if box is None:
                return
            box.append("[%s] %s" % (QDateTime.currentDateTime().toString("hh:mm:ss"), text))
            # 限制日志文档规模，长期运行不会越跑越卡。
            if box.document().blockCount() > 500:
                cursor = box.textCursor()
                cursor.movePosition(QTextCursor.Start)
                for _ in range(100):
                    cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
            box.moveCursor(QTextCursor.End)
        except Exception as exc:
            log_exception("界面日志写入异常", exc)

    def write_diagnostic_log(self):
        try:
            memory = process_memory_mb()
            write_log(
                "心跳诊断 | 内存=%s | OCR忙碌=%s | 定时OCR=%s | 定时操作=%s | 加载中=%s | ROI=%d | URL=%s"
                % ("%.1f MB" % memory if memory is not None else "未知",
                   self.ocr_busy, self.roi_clock_timer.isActive(), self.refresh_clock.isActive(),
                   self.web_loading, len(self.roi_list), self.webview.url().toString()))
        except Exception as exc:
            log_exception("心跳诊断异常", exc)

    # ============================================================ 窗口行为 ==
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_floating()

    def _relayout_floating(self):
        central = self.centralWidget()
        if central is None:
            return
        if hasattr(self, "left_panel"):
            width = self.left_panel.width()
            self.left_panel.setGeometry(max(0, central.width() - width), 48, width,
                                        max(0, central.height() - 48))
        if hasattr(self, "roi_overlay") and hasattr(self, "webview"):
            self.roi_overlay.setGeometry(self.webview.rect())
            self.roi_overlay.raise_()
        if hasattr(self, "control_bar"):
            if not getattr(self, "control_bar_user_moved", False):
                self.control_bar.adjustSize()
                self.control_bar.move(
                    max(0, central.width() - self.control_bar.width() - 10), 8)
            self.control_bar.raise_()

    def eventFilter(self, obj, event):
        if getattr(self, "webview", None) is not None and obj is self.webview:
            if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
                if getattr(self, "roi_overlay", None) is not None:
                    self.roi_overlay.setGeometry(self.webview.rect())
                    self.roi_overlay.raise_()
        return super().eventFilter(obj, event)

    def _remember_control_bar_moved(self):
        self.control_bar_user_moved = True

    def toggle_overlay_visibility(self):
        visible = self.roi_overlay.toggle_boxes_visibility()
        self.control_bar.set_visibility_text(visible)
        self.control_bar.raise_()

    def toggle_settings_panel(self):
        if self.is_fullscreen:
            return
        self.settings_open = not self.settings_open
        self.left_panel.setVisible(self.settings_open)
        self.control_bar.set_settings_text(self.settings_open)
        if self.settings_open:
            self.left_panel.raise_()
        self.roi_overlay.raise_()
        self.control_bar.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.picker_action is not None:
            self.cancel_operation("已取消拾取")
            self.operation_feedback("已取消拾取")
            event.accept()
        elif event.key() == Qt.Key_F11:
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

    # ================================================================ 页面 ==
    def load_page(self):
        url = self.url_input.text().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.log("🌐 正在加载页面: %s" % url)
        self.webview.load(QUrl(url))

    def on_load_started(self):
        self.cancel_operation("页面开始导航")
        if hasattr(self, "refresh_clock"):
            self.refresh_clock.stop()
        self.web_loading = True
        self.reload_in_progress_count += 1
        self.web_load_watchdog.start(45000)
        self.controller.notify_navigation()
        write_log("WebEngine 开始加载 | 连续加载计数=%d" % self.reload_in_progress_count)

    def on_web_load_timeout(self):
        if not self.web_loading:
            return
        # 页面卡住时不能让整个软件一直“假加载”，否则后续所有操作都会被永久跳过。
        self.web_loading = False
        self.reload_in_progress_count = 0
        warning = "WebEngine 加载超过 45 秒仍未完成，已恢复自动操作（页面可能加载失败）"
        write_log(warning, "WARN")
        self.log("⚠️ %s" % warning)

    def on_load_finished(self, ok):
        self.web_loading = False
        self.web_load_watchdog.stop()
        self.reload_in_progress_count = 0
        self.controller.notify_navigation()
        if self.auto_operation_cb.isChecked():
            self.start_operation_timer()  # 重新计时，不沿用导航前的剩余秒数
        if ok:
            self.log("✅ 页面加载完毕")
            self.controller.run_javascript(INJECT_SCRIPT)
            QTimer.singleShot(1500, self.paste_credentials)
        else:
            self.log("⚠️ 页面加载失败，等待下一次刷新")
            write_log("WebEngine loadFinished=False", "WARN")

    def on_zoom_changed(self, value):
        self.cancel_operation("网页缩放改变")
        factor = value / 100.0
        self.webview.setZoomFactor(factor)
        self.config["zoom_level"] = factor

    # ============================================================ ROI 区域 ==
    def start_roi_selection(self):
        self.log("提示：进入网页内选框调整模式，完成后点【✅ 完成】")
        self.roi_overlay.start_editing(self.roi_list)

    def clear_all_rois(self):
        self.roi_list = []
        self.tracker.reset()
        self.roi_overlay.clear_rects()
        self._update_roi_info()
        self.roi_status_label.setText("状态: 选框已清空")
        self.stop_alarm_audio()
        self.log("🗑️ 已清空所有框选区域")

    def on_roi_list_selected(self, rects):
        self.roi_list = [QRect(rect) for rect in rects]
        self.roi_overlay.set_rects(self.roi_list)
        self._update_roi_info()
        self.log("📐 框选更新完成，当前共有 %d 个监控区域" % len(self.roi_list))
        self.status_label.setText("✅ 已更新 %d 个选框" % len(self.roi_list))
        if self.roi_list:
            self.perform_roi_ocr_check()

    def _update_roi_info(self):
        self.roi_info_label.setText("选中区域: %d 个选框" % len(self.roi_list))

    def _migrate_click_point(self):
        """旧版本保存的是屏幕绝对坐标，这里一次性换算为网页内坐标。"""
        if len(self.click_point) < 2 or self.config.get("click_point_space") == "webview_local":
            return
        try:
            local = self.webview.mapFromGlobal(QPoint(int(self.click_point[0]),
                                                      int(self.click_point[1])))
            self.click_point = [local.x(), local.y()]
            self.config["click_point"] = list(self.click_point)
            self.config["click_point_space"] = "webview_local"
            save_config(self.config)
            self.log("♻️ 旧版屏幕坐标点位已转换为网页内坐标")
        except Exception as exc:
            log_exception("点位坐标转换失败", exc)

    def current_rule(self):
        return build_rule(self.target_value_input.text(), self.target_same_count_spin.value())

    # ============================================================ OCR 检测 ==
    def on_manual_detect_clicked(self):
        self.log("👆 触发【手动检测】...")
        if not HAS_DDDDOCR or self.ocr_worker is None or not self.ocr_worker.ready:
            message = "❌ ddddocr 不可用 (%s)" % (DDDDOCR_ERROR or "引擎未初始化")
            self.roi_status_label.setText("状态: %s" % message)
            self.log(message)
            return
        if not self.roi_list:
            self.roi_status_label.setText("状态: ⚠️ 请先划定框选区域！")
            self.log("⚠️ 区域未划定，无法执行检测！")
            return
        self.perform_roi_ocr_check()

    def toggle_roi_monitor(self):
        if self.roi_clock_timer.isActive():
            self.roi_clock_timer.stop()
            self.roi_toggle_btn.setText("▶️ 定时检测")
            self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
            self.roi_countdown_label.setText("")
            self.roi_status_label.setText("状态: 定时检测已停止")
            self.log("⏹️ 定时检测已停止")
        else:
            if not HAS_DDDDOCR or self.ocr_worker is None or not self.ocr_worker.ready:
                message = DDDDOCR_ERROR or "ddddocr 导入失败或模型缺失"
                self.roi_status_label.setText("❌ 缺失 ddddocr 模块 (%s)" % message)
                self.log("❌ 开启失败: %s" % message)
                return
            if not self.roi_list:
                self.roi_status_label.setText("状态: ⚠️ 请先框选区域！")
                self.log("⚠️ 无法开启定时检测：尚未划定任何 ROI 区域")
                return
            seconds = self.operation_interval.value()
            self.roi_remaining_seconds = seconds
            self.roi_countdown_label.setText("⏱️ 下次检测倒计时: %ds" % seconds)
            self.roi_clock_timer.start(1000)
            self.roi_toggle_btn.setText("⏸️ 停止定时")
            self.roi_toggle_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
            self.roi_status_label.setText("状态: 正在定时检测中...")
            self.log("▶️ 定时检测开启，周期: %d秒" % seconds)
            self.perform_roi_ocr_check()
        self.update_countdown_bar()

    def on_roi_clock_tick(self):
        if self.roi_remaining_seconds > 1:
            self.roi_remaining_seconds -= 1
            self.roi_countdown_label.setText("⏱️ 下次检测倒计时: %ds" % self.roi_remaining_seconds)
        else:
            self.perform_roi_ocr_check()
            self.roi_remaining_seconds = self.operation_interval.value()
            self.roi_countdown_label.setText("⏱️ 下次检测倒计时: %ds" % self.roi_remaining_seconds)
        self.update_countdown_bar()

    def perform_roi_ocr_check(self):
        """取图在主线程，识别在工作线程，避免长时间 OCR 阻塞界面。"""
        if self.ocr_busy or not self.roi_list or self._quitting:
            return
        if not HAS_DDDDOCR or self.ocr_worker is None or not self.ocr_worker.ready:
            return
        try:
            # 惰性导入：没有安装 opencv/numpy 时，程序仍能正常打开与刷新网页。
            from .image_tools import encode_row, qimage_to_bgr, segment_rows
        except Exception as exc:
            self.log("❌ 图像处理依赖缺失: %s" % exc)
            return

        jobs = []
        try:
            for box_index, rect in enumerate(self.roi_list, 1):
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                if not self.webview.rect().intersects(rect):
                    write_log("区域 #%d 超出当前网页可视范围，已跳过" % box_index, "WARN")
                    continue
                image = self.controller.screenshot(QRect(rect))
                bgr = qimage_to_bgr(image)
                if bgr is None:
                    continue
                row_pngs = []
                for start_y, end_y in segment_rows(bgr):
                    encoded = encode_row(bgr[start_y:end_y, :])
                    if encoded:
                        row_pngs.append(encoded)
                del bgr
                if row_pngs:
                    jobs.append((box_index, row_pngs))
            if not jobs:
                return
            self.ocr_busy = True
            self.ocr_requested.emit(jobs)
        except Exception as exc:
            self.ocr_busy = False
            log_exception("OCR 取图准备异常", exc)
            self.log("❌ OCR 取图准备异常: %s" % exc)

    @Slot(str, bool)
    def _on_ocr_status(self, message, is_error):
        if is_error:
            self.log("⚠️ %s" % message)
            write_log(message, "WARN")
        else:
            write_log(message)

    @Slot(object, object)
    def on_ocr_finished(self, results, error):
        self.ocr_busy = False
        if error:
            self.log("❌ OCR 运行异常: %s" % error)
            return
        if not results:
            return

        rule = self.current_rule()
        lines = []
        try:
            for box_index, digits in results:
                lines.append("【区域#%d】(共%d行数字): [%s]"
                             % (box_index, len(digits), " | ".join(digits) if digits else "无"))
                self.tracker.evaluate(box_index, digits, rule)
            self.log("🎯 监控检测结果:\n%s" % "\n".join(lines))
            self.roi_overlay.set_alarm_states(self.tracker.alarming)
            alarming = self.tracker.active_boxes()
            if alarming:
                message = "🚨 区域 %s 触发增量新报警！" % alarming
                self.roi_status_label.setText("状态: %s" % message)
                self.status_label.setText(message)
                if not self.alarm_loop_timer.isActive():
                    self.alarm_loop_timer.start(1200)
                    self.play_reminder_sound()
            else:
                self.stop_alarm_audio()
                tip = ("目标值 '%s'" % rule["target_value"]) if rule["target_value"] \
                    else ("单区域连续 %d 行相同" % rule["same_count"])
                self.roi_status_label.setText("状态: 监控中 | 规则: %s" % tip)
        except Exception as exc:
            log_exception("OCR 结果处理异常", exc)

    def clear_alarm_for_box(self, box_index):
        self.tracker.acknowledge(box_index, self.current_rule())
        self.roi_overlay.set_alarm_states(self.tracker.alarming)
        self.log("🔕 已消除【区域 #%d】的报警，追加新行时不再重复报警" % box_index)
        if not self.tracker.active_boxes():
            self.stop_alarm_audio()
            self.roi_status_label.setText("状态: 报警已消除，监控中...")

    def clear_all_alarms(self):
        self.tracker.acknowledge_all(len(self.roi_list), self.current_rule())
        self.roi_overlay.set_alarm_states(self.tracker.alarming)
        self.stop_alarm_audio()
        self.roi_status_label.setText("状态: 报警已消除，监控中...")
        self.log("🚨 已消除全部报警")

    def stop_alarm_audio(self):
        if self.alarm_loop_timer.isActive():
            self.alarm_loop_timer.stop()

    def play_reminder_sound(self):
        target = self.sound_combo.currentData() if hasattr(self, "sound_combo") else ""
        target = self.custom_sound_path if target == "CUSTOM" else target
        if target and os.path.exists(target):
            if sys.platform == "win32":
                try:
                    import winsound
                    winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
                except Exception:
                    pass
        QApplication.beep()

    def populate_sound_options(self):
        self.sound_combo.clear()
        self.sound_files_map.clear()
        self.sound_combo.addItem("默认蜂鸣 (Beep)", "")
        media_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media")
        if os.path.exists(media_dir):
            for wav in sorted(glob.glob(os.path.join(media_dir, "*.wav"))):
                name = os.path.splitext(os.path.basename(wav))[0]
                display = "🔔 %s" % name
                self.sound_files_map[display] = wav
                self.sound_combo.addItem(display, wav)
        self.sound_combo.addItem("📂 自定义 .wav 文件...", "CUSTOM")
        self.sound_combo.setCurrentIndex(self.config.get("reminder_sound_index", 0)
                                         if self.config.get("reminder_sound_index", 0) < self.sound_combo.count()
                                         else 0)

    def on_sound_selection_changed(self, index):
        if self.sound_combo.currentData() == "CUSTOM":
            path, _ = QFileDialog.getOpenFileName(
                self, "选择自定义报警铃声", self.custom_sound_path or os.getcwd(), "音频文件 (*.wav)")
            if path:
                self.custom_sound_path = path
                self.log("🎵 已设定自定义铃声路径")
            else:
                self.sound_combo.setCurrentIndex(0)

    # ============================================================ 账号填写 ==
    def _refresh_account_text(self):
        filled = bool(self.config.get("account")) and bool(self.config.get("password"))
        self.account_btn.setText("👤 账号 ✓" if filled else "👤 账号")

    def open_account_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("账号设置")
        dialog.setModal(True)
        dialog.resize(360, 170)
        layout = QVBoxLayout(dialog)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("账号"))
        account = QLineEdit(self.config.get("account", ""))
        account.setPlaceholderText("请输入账号")
        row1.addWidget(account, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("密码"))
        password = QLineEdit(self.config.get("password", ""))
        password.setEchoMode(QLineEdit.Password)
        password.setPlaceholderText("请输入密码")
        row2.addWidget(password, 1)
        layout.addLayout(row2)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        save_button = QPushButton("保存")
        buttons.addWidget(cancel)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        cancel.clicked.connect(dialog.reject)

        def do_save():
            self.config["account"] = account.text().strip()
            self.config["password"] = password.text()
            save_config(self.config)
            self._refresh_account_text()
            self.log("👤 账号密码已保存")
            dialog.accept()

        save_button.clicked.connect(do_save)
        dialog.exec()

    def paste_credentials(self):
        account = self.config.get("account", "").strip()
        password = self.config.get("password", "").strip()
        if not account or not password:
            return
        script = ("if (typeof window.__fillV7 === 'function') { window.__fillV7(%s, %s); }"
                  % (json.dumps(account), json.dumps(password)))
        self.controller.run_javascript(script)
        self.log("📋 已一键粘贴账号密码并触发回车，如需验证码请手动输入")

    # ============================================================ 截图扫码 ==
    def take_screenshot(self):
        target_dir = self.config.get("screenshot_path") or os.getcwd()
        filename = "screenshot_%s.png" % QDateTime.currentDateTime().toString("yyyyMMdd_hhmmss")
        full_path = os.path.join(target_dir, filename)
        pixmap = None
        try:
            # 优先从网页本身取图，即使窗口被遮挡/不在前台也能拿到完整画面。
            image = self.controller.screenshot(QRect(self.webview.rect()))
            if image is not None and not image.isNull():
                pixmap = QPixmap.fromImage(image)
            else:
                pixmap = self.webview.grab()
        except Exception as exc:
            log_exception("截图失败", exc)
        if pixmap is None or pixmap.isNull() or not pixmap.save(full_path, "PNG"):
            self.log("❌ 截图保存失败: %s" % full_path)
            return
        self.current_file_url = self.screenshot_service.url_for(self.ip_combo.currentText(), full_path)
        self.log("📸 截图已保存: %s" % full_path)
        self._show_qr_code(self.current_file_url)

    def _show_qr_code(self, url):
        from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

        def on_finished():
            try:
                reply_obj = getattr(self, "_qr_reply", None)
                if reply_obj is None or reply_obj.error() != QNetworkReply.NoError:
                    self.log("⚠️ 二维码生成失败，链接: %s" % url)
                    return
                pixmap = QPixmap()
                pixmap.loadFromData(reply_obj.readAll())
                if pixmap.isNull():
                    self.log("⚠️ 二维码为空，链接: %s" % url)
                    return
                self._qr_dialog(url, pixmap)
            finally:
                if getattr(self, "_qr_reply", None) is not None:
                    self._qr_reply.deleteLater()
                    self._qr_reply = None

        api = ("https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=%s"
               % urllib.parse.quote(url))
        self._qr_reply = self.nam.get(QNetworkRequest(QUrl(api)))
        self._qr_reply.finished.connect(on_finished)

    def _qr_dialog(self, url, pixmap):
        dialog = QDialog(self)
        dialog.setWindowTitle("📸 手机扫码查看最新截图")
        dialog.setModal(True)
        dialog.resize(340, 420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(15, 15, 15, 15)
        image_label = QLabel()
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(image_label)
        tip = QLabel("📢 请确认【手机】与【电脑】处于同一局域网")
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #a6adc8; font-size: 12px; font-weight: bold;")
        layout.addWidget(tip)
        link = QLineEdit(url)
        link.setReadOnly(True)
        layout.addWidget(link)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    # ============================================================ 自动操作 ==
    def format_click_point(self):
        if len(self.click_point) >= 2:
            return "📍 当前点位: (%d, %d)" % (self.click_point[0], self.click_point[1])
        return "📍 当前点位: 未设置"

    def update_operation_ui(self):
        action = self.operation_action_combo.currentData()
        busy = self.operation_gate.busy
        self.pick_point_btn.setEnabled(action != ACTION_REFRESH and not busy)
        self.test_operation_btn.setEnabled(not busy)
        if action in (ACTION_CLICK, ACTION_BACKGROUND):
            hint = "（后台执行，不移动系统鼠标）" if action == ACTION_BACKGROUND else "（执行时移动系统鼠标）"
            self.click_point_label.setText(self.format_click_point() + hint)
        else:
            self.click_point_label.setText("刷新网页；不需要点击目标")

    def operation_feedback(self, message):
        self.operation_result_label.setText(message)
        self.status_label.setText(message)
        self.log(message)

    def cancel_operation(self, reason):
        was_pending = self.operation_gate.busy
        self.operation_gate.cancel()
        self.picker_action = None
        if getattr(self.roi_overlay, "is_picking_point", False):
            self.roi_overlay.finish_point_picker()
        self.update_operation_ui()
        if was_pending:
            self.operation_feedback("%s；已发出的点击不可撤销，结果可能未知" % reason)

    def cancel_point_picker(self):
        if self.picker_action is not None:
            self.cancel_operation("已取消拾取")
            self.operation_feedback("已取消拾取")

    def start_point_picker(self):
        if self.operation_action_combo.currentData() == ACTION_REFRESH:
            self.operation_feedback("刷新模式无需取点，请切换到点击模式")
            return
        if self.web_loading:
            self.operation_feedback("页面加载中，请稍后拾取")
            return
        self.auto_operation_cb.setChecked(False)
        self.cancel_operation("重新拾取")
        self.picker_action = self.operation_action_combo.currentData()
        self.update_operation_ui()
        if self.settings_open:
            self.toggle_settings_panel()
        self.roi_overlay.start_point_picker()
        self.operation_feedback("请在网页上拾取点位；Esc 取消，不会点击网页")

    def on_point_selected(self, point):
        action = self.picker_action
        self.picker_action = None
        if action is None or action != self.operation_action_combo.currentData():
            return
        if not self.webview.rect().contains(point):
            self.operation_feedback("点位不在网页区域内，请重新拾取")
            return
        self.click_point = [point.x(), point.y()]
        self.update_operation_ui()
        self.save_settings()
        self.operation_feedback("已设置点击点位 (%d, %d)；前后台共用同一坐标" % (point.x(), point.y()))

    def on_operation_mode_changed(self, *_):
        self.cancel_operation("操作模式已切换")
        # 切换模式必须显式重启定时器，避免继承即将到期的倒计时。
        if self.auto_operation_cb.isChecked():
            self.auto_operation_cb.setChecked(False)
        self.update_operation_ui()

    def on_strategy_changed(self, *_):
        strategy = self.strategy_combo.currentData()
        self.controller.configure(strategy=strategy)
        self.config["background_strategy"] = strategy
        self.channel_state_label.setText("通道已切换为：%s" % strategy)
        write_log("后台执行通道切换为 %s" % strategy)
        self.update_operation_ui()

    def on_background_option_changed(self, *_):
        if hasattr(self, "temp_foreground_cb") and hasattr(self, "keep_active_cb"):
            self.controller.configure(
                allow_temporary_foreground=self.temp_foreground_cb.isChecked())
            self.config["allow_temporary_foreground"] = self.temp_foreground_cb.isChecked()
            self.config["keep_page_active"] = self.keep_active_cb.isChecked()

    def run_channel_self_test(self):
        """把三条通道的实际可用性显示出来，便于现场排查。"""
        lines = [self.controller.describe()]
        session = self.controller.cdp_session(force=True)
        if session is None:
            lines.append("❌ 调试通道未连通，操作将回落到 Qt 事件/置前点击")
        else:
            try:
                width = session.evaluate("String(document.documentElement.clientWidth||0)", timeout=2.0)
                lines.append("✅ 调试通道可用，页面可视宽度=%s" % width)
            except Exception as exc:
                lines.append("❌ 调试通道调用失败: %s" % exc)
        try:
            from .qt_click import renderer_ready
            lines.append("Qt 事件通道：%s" % ("可用" if renderer_ready(self.webview) else "不可用"))
        except Exception as exc:
            lines.append("Qt 事件通道检测失败: %s" % exc)
        lines.append("真实系统点击：%s" % ("支持" if win_input.supported() else "当前平台不支持"))
        text = " | ".join(lines)
        self.channel_state_label.setText(text)
        self.log("🔎 通道自检: %s" % text)

    def on_operation_interval_changed(self, *_):
        """间隔变化时如果两个定时器在跑，立刻按新间隔重新计时。"""
        if hasattr(self, "refresh_clock") and self.refresh_clock.isActive():
            self.start_operation_timer()
        if hasattr(self, "roi_clock_timer") and self.roi_clock_timer.isActive():
            self.roi_remaining_seconds = self.operation_interval.value()
            self.roi_countdown_label.setText("⏱️ 下次检测倒计时: %ds" % self.roi_remaining_seconds)
        self.update_countdown_bar()

    def on_auto_operation_changed(self, *_):
        if self.auto_operation_cb.isChecked():
            self.start_operation_timer()
        else:
            self.stop_operation_timer()

    def start_operation_timer(self):
        self.remaining_seconds = self.operation_interval.value()
        self.refresh_clock.start(1000)
        self.log("⏱️ 自动操作开启，间隔: %d秒" % self.remaining_seconds)
        self.update_countdown_bar()

    def stop_operation_timer(self):
        self.cancel_operation("自动操作已停止")
        self.refresh_clock.stop()
        self.countdown_label.setText("")
        self.log("⏹️ 自动操作已停止")
        self.update_countdown_bar()

    def on_operation_clock_tick(self):
        if self.remaining_seconds > 1:
            self.remaining_seconds -= 1
            self.countdown_label.setText("下次操作: %ds" % self.remaining_seconds)
        else:
            self.perform_scheduled_operation()
            self.remaining_seconds = self.operation_interval.value()
        self.update_countdown_bar()

    def update_countdown_bar(self):
        operation = "%ds" % self.remaining_seconds if self.refresh_clock.isActive() else "已停止"
        detection = "%ds" % self.roi_remaining_seconds if self.roi_clock_timer.isActive() else "已停止"
        self.control_bar.set_countdown("⏱️ 操作: %s | OCR检测: %s" % (operation, detection))

    def refresh_page(self):
        if self.web_loading:
            self.log("⏭️ 跳过网页刷新：上一轮页面仍在加载")
            return False
        if time.monotonic() - self.last_reload_time < 5.0:
            self.log("⏭️ 跳过网页刷新：刷新过于频繁")
            return False
        try:
            self.last_reload_time = time.monotonic()
            self.web_loading = True
            channel = self.controller.reload(ignore_cache=True)
            self.log("🔄 触发网页刷新（通道: %s）" % CHANNEL_LABELS.get(channel, channel))
            return True
        except Exception as exc:
            self.web_loading = False
            log_exception("网页刷新异常", exc)
            self.log("❌ 网页刷新异常: %s" % exc)
            return False

    def perform_scheduled_operation(self):
        if self.operation_gate.busy or self.picker_action is not None:
            self.operation_feedback("正在执行或拾取，跳过重复操作")
            return
        if self.web_loading:
            self.operation_feedback("页面加载中，跳过操作")
            return
        action = self.operation_action_combo.currentData()
        if action == ACTION_CLICK:
            self.perform_foreground_point_click()
        elif action == ACTION_BACKGROUND:
            self.perform_background_point_click()
        elif action == ACTION_REFRESH:
            self.operation_feedback("已请求刷新" if self.refresh_page() else "本次刷新已跳过")

    def perform_background_point_click(self):
        if self.web_loading or self._quitting:
            self.operation_feedback("页面加载中或程序退出中，未执行")
            return
        if len(self.click_point) < 2:
            self.operation_feedback("请先拾取点击点位")
            return
        ticket = self.operation_gate.begin(ACTION_BACKGROUND)
        if ticket is None:
            self.operation_feedback("上一项操作尚未完成，跳过重复操作")
            return
        try:
            point = QPoint(int(self.click_point[0]), int(self.click_point[1]))
            outcome = self.controller.click(point)
            self.operation_feedback("%s（%s），真后台执行" % (outcome.message, CHANNEL_LABELS.get(outcome.channel, outcome.channel)))
        except OperationError as exc:
            self.auto_operation_cb.setChecked(False)
            self.operation_feedback("后台点击失败，已停止自动操作：%s" % exc)
        except Exception as exc:
            log_exception("后台点击异常", exc)
            self.operation_feedback("后台点击异常: %s" % exc)
        finally:
            self.operation_gate.finish(ticket)
            self.update_operation_ui()

    def perform_foreground_point_click(self):
        """前台点击：真实移动系统鼠标，适用于必须激活界面的场景。"""
        if len(self.click_point) < 2:
            self.operation_feedback("⚠️ 请先拾取点击点位")
            return
        try:
            global_pos = self.webview.mapToGlobal(QPoint(int(self.click_point[0]),
                                                         int(self.click_point[1])))
            win_input.real_click(int(global_pos.x()), int(global_pos.y()), restore_cursor=False)
            self.operation_feedback("🖱️ 前台点击成功 (%d, %d)" % (self.click_point[0], self.click_point[1]))
        except Exception as exc:
            log_exception("前台点击失败", exc)
            self.operation_feedback("⚠️ 前台点击失败: %s" % exc)

    # ================================================================ 配置 ==
    def save_settings(self):
        self.config["url"] = self.url_input.text().strip()
        self.config["zoom_level"] = self.zoom_spin.value() / 100.0
        self.config["auto_refresh"] = self.auto_operation_cb.isChecked()
        self.config["operation_interval"] = self.operation_interval.value()
        self.config["operation_action"] = self.operation_action_combo.currentData()
        self.config["click_point"] = list(self.click_point)
        self.config["click_point_space"] = "webview_local"
        self.config["selected_ip"] = self.ip_combo.currentText()
        self.config["reminder_sound_index"] = self.sound_combo.currentIndex()
        self.config["reminder_custom_path"] = self.custom_sound_path
        self.config["roi_list"] = [[r.x(), r.y(), r.width(), r.height()] for r in self.roi_list]
        self.config["roi_space"] = "webview_local"
        self.config["target_same_count"] = self.target_same_count_spin.value()
        self.config["target_value"] = self.target_value_input.text().strip()
        self.config["background_strategy"] = self.strategy_combo.currentData()
        self.config["allow_temporary_foreground"] = self.temp_foreground_cb.isChecked()
        self.config["keep_page_active"] = self.keep_active_cb.isChecked()
        if save_config(self.config):
            self.log("💾 配置文件保存成功！")
            self.status_label.setText("设置已保存")

    def refresh_ip_list(self):
        from PySide6.QtNetwork import QNetworkInterface, QAbstractSocket
        self.ip_combo.clear()
        addresses = []
        for address in QNetworkInterface.allAddresses():
            if address.protocol() == QAbstractSocket.IPv4Protocol:
                text = address.toString()
                if text != "127.0.0.1" and not text.startswith("169.254"):
                    addresses.append(text)
        if not addresses:
            addresses.append("127.0.0.1")
        self.ip_combo.addItems(sorted(set(addresses)))
        saved = self.config.get("selected_ip", "")
        if saved and self.ip_combo.findText(saved) >= 0:
            self.ip_combo.setCurrentText(saved)

    # ================================================================ 退出 ==
    def closeEvent(self, event):
        event.accept()
        self.quit_app()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self.cancel_operation("程序正在退出")
        write_log("程序开始执行退出流程")
        try:
            for timer in (getattr(self, "refresh_clock", None), getattr(self, "roi_clock_timer", None),
                          getattr(self, "alarm_loop_timer", None), getattr(self, "diagnostic_timer", None)):
                if timer is not None and hasattr(timer, "stop"):
                    timer.stop()
            self.stop_alarm_audio()
            self.controller.shutdown()
            if getattr(self, "ocr_thread", None) is not None and self.ocr_thread.isRunning():
                self.ocr_thread.quit()
                if not self.ocr_thread.wait(3000):
                    write_log("OCR 线程未在 3 秒内结束，交给 Qt 处理", "WARN")
            if getattr(self, "screenshot_service", None) is not None:
                self.screenshot_service.stop()
            if getattr(self, "tray", None) is not None:
                self.tray.hide()
            write_log("程序退出流程完成")
        except Exception as exc:
            log_exception("退出流程异常", exc)
        finally:
            QApplication.quit()
