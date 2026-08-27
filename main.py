# -*- coding: utf-8 -*-
"""
网页刷新数字监控 (V15.0 - 右上角设置菜单版)
更新日志：
 1. 取消各监视窗口单框齿轮，界面保持简洁。
 2. 屏幕右上角常驻全局控制栏：
    - ⏱️ 实时显示【网页刷新倒计时】与【OCR检测倒计时】
    - 👁️ 一键【隐藏/显示所有识别窗口】
    - ⚙️ 点击统一【调整所有框选窗口】（拖拽/拉伸/增删框选）
 3. 继承 V10.8 增量防重复报警逻辑，解决消除报警后表格新增行导致误报的问题。
依赖：PySide6, PySide6.QtWebEngineWidgets, ddddocr, opencv-python, numpy
"""

import sys
import json
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
    DDDDOCR_ERR_MSG = str(e)

# ==================== 2. PySide6 核心组件导入 ====================
from PySide6.QtCore import QUrl, Qt, QTimer, QDateTime, QRect, QPoint, Signal, QEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLineEdit, QLabel, QSpinBox, QCheckBox,
    QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy, QFileDialog, QDialog,
    QComboBox, QTextEdit, QAbstractSpinBox
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QTextCursor, QGuiApplication, QImage

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
        print(f"保存配置文件失败: {e}")

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rects = []
        self.is_editing = False
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
        self.countdown_text = "⏱️ 刷新: -- | OCR检测: --"

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

        self.top_right_bar=QWidget(self)
        self.top_right_bar.setStyleSheet("""
            QWidget { background:#181825; border:1px solid #45475a; border-radius:6px; }
            QLabel { color:#38bdf8; font-weight:bold; font-size:11px; padding:0 3px; }
            QPushButton { background:#313244; color:#cdd6f4; border:1px solid #45475a;
                          border-radius:4px; padding:5px 9px; font-size:11px; font-weight:bold; }
            QPushButton:hover { background:#45475a; color:#fff; }
        """)
        tr=QHBoxLayout(self.top_right_bar); tr.setContentsMargins(7,4,7,4); tr.setSpacing(5)
        self.lbl_countdown=QLabel(self.countdown_text)
        self.btn_toggle_vis=QPushButton("👁️ 隐藏识别框")
        self.btn_gear=QPushButton("⚙️ 调整识别框")
        self.btn_toggle_vis.clicked.connect(self.toggle_boxes_visibility)
        self.btn_gear.clicked.connect(self.on_top_right_gear_clicked)
        tr.addWidget(self.lbl_countdown); tr.addWidget(self.btn_toggle_vis); tr.addWidget(self.btn_gear)
        self.top_right_bar.show()
        self.raise_()
        self.reposition_bars()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "settings_btn") and hasattr(self, "centralWidget"):
            cw = self.centralWidget()
            self.settings_btn.move(cw.width() - self.settings_btn.width() - 10, 10)
        if hasattr(self, "settings_panel") and hasattr(self, "centralWidget"):
            cw = self.centralWidget()
            self.settings_panel.setGeometry(max(0, cw.width() - self.settings_panel.width() - 10),
                                            50, self.settings_panel.width(), max(0, cw.height() - 60))
        if hasattr(self, "roi_overlay"):
            self.roi_overlay.setGeometry(self.webview.rect())
            self.roi_overlay.raise_()
        if hasattr(self, "settings_btn"):
            self.settings_btn.raise_()
        if hasattr(self, "settings_panel") and self.settings_panel.isVisible():
            self.settings_panel.raise_()
            self.settings_btn.raise_()
        if hasattr(self, "roi_overlay"):
            self.roi_overlay.raise_()

    def eventFilter(self, obj, event):
        if hasattr(self, "webview") and obj is self.webview:
            if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
                if hasattr(self, "roi_overlay") and self.roi_overlay:
                    self.roi_overlay.setGeometry(self.webview.rect())
                    self.roi_overlay.raise_()

        return super().eventFilter(obj, event)

    def log(self, text):
        time_str = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.log_box.append(f"[{time_str}] {text}")
        self.log_box.moveCursor(QTextCursor.End)

    def populate_sound_options(self):
        self.sound_combo.clear()
        self.sound_files_map.clear()
        self.sound_combo.addItem("默认蜂鸣 (Beep)", "")

        media_dir = r"C:\Windows\Media"
        if os.path.exists(media_dir):
            wav_files = glob.glob(os.path.join(media_dir, "*.wav"))
            for wav_path in sorted(wav_files):
                filename = os.path.basename(wav_path)
                name_no_ext = os.path.splitext(filename)[0]
                display_name = f"🔔 {name_no_ext}"
                self.sound_files_map[display_name] = wav_path
                self.sound_combo.addItem(display_name, wav_path)

        self.sound_combo.addItem("📂 自定义 .wav 文件...", "CUSTOM")

        saved_sound_idx = self.config.get("reminder_sound_index", 0)
        if saved_sound_idx < self.sound_combo.count():
            self.sound_combo.setCurrentIndex(saved_sound_idx)
        else:
            self.sound_combo.setCurrentIndex(0)

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

    def toggle_settings_panel(self):
        visible = self.settings_panel.isVisible()
        if visible:
            self.settings_panel.hide()
            self.settings_btn.setText("⚙️ 设置")
            self.settings_btn.raise_()
            self.log("⚙️ 设置菜单已收起")
        else:
            self.settings_panel.setGeometry(max(0, self.centralWidget().width() - self.settings_panel.width() - 10),
                                            50, self.settings_panel.width(), max(0, self.centralWidget().height() - 60))
            self.settings_panel.show()
            self.settings_panel.raise_()
            self.settings_btn.setText("▲ 收起")
            self.settings_btn.raise_()
            self.log("⚙️ 设置菜单已展开（不影响网页显示区域）")

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

    def start_roi_selection(self):
        self.log("提示：请点击网页右上角“⚙️ 调整识别框”进行框选")
        self.roi_overlay.start_editing(self.roi_list)

    def clear_all_rois(self):
        self.roi_list.clear()
        self.box_latest_digits.clear()
        self.box_ack_count.clear()
        self.box_ack_matches.clear()
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
        self.log(f"📐 框选更新完成，当前共有 {len(self.roi_list)} 个监控区域")
        self.status_label.setText(f"✅ 已更新 {len(self.roi_list)} 个选框")
        self.perform_roi_ocr_check()

    def on_manual_detect_clicked(self):
        self.log("👆 触发【手动检测】...")
        if not HAS_DDDDOCR or self.ocr is None:
            msg = f"❌ 缺失 ddddocr 模块 (原因: {DDDDOCR_ERR_MSG or '未初始化'})"
            self.roi_status_label.setText(f"状态: {msg}")
            self.log(msg)
            return

        if not self.roi_list:
            self.roi_status_label.setText("状态: ⚠️ 请先划定框选区域！")
            self.log("⚠️ 区域未划定，无法执行检测！")
            return

        self.perform_roi_ocr_check()

    def calc_roi_check_interval(self):
        auto_sec = self.auto_interval.value()
        mult = self.roi_multiplier_spin.value()
        return auto_sec * mult + 5

    def toggle_roi_monitor(self):
        if self.roi_clock_timer.isActive():
            self.roi_clock_timer.stop()
            self.roi_toggle_btn.setText("▶️ 定时检测")
            self.roi_toggle_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
            self.roi_countdown_label.setText("")
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

            sec = self.calc_roi_check_interval()
            self.roi_remaining_seconds = sec
            self.roi_countdown_label.setText(f"⏱️ 下次检测倒计时: {self.roi_remaining_seconds}秒")
            self.roi_clock_timer.start(1000)

            self.roi_toggle_btn.setText("⏸️ 停止定时")
            self.roi_toggle_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
            self.roi_status_label.setText("状态: 正在定时检测中...")
            self.log(f"▶️ 定时检测开启，周期: {sec}秒")
            self.perform_roi_ocr_check()
            
        self.update_top_right_countdown_bar()

    def on_roi_clock_tick(self):
        if self.roi_remaining_seconds > 1:
            self.roi_remaining_seconds -= 1
            self.roi_countdown_label.setText(f"⏱️ 下次检测倒计时: {self.roi_remaining_seconds}秒")
        else:
            self.perform_roi_ocr_check()
            self.roi_remaining_seconds = self.calc_roi_check_interval()
            self.roi_countdown_label.setText(f"⏱️ 下次检测倒计时: {self.roi_remaining_seconds}秒")
        
        self.update_top_right_countdown_bar()

    def update_top_right_countdown_bar(self):
        refresh_str = f"{self.remaining_seconds}s" if self.refresh_clock.isActive() else "已停止"
        roi_str = f"{self.roi_remaining_seconds}s" if self.roi_clock_timer.isActive() else "已停止"
        text = f"⏱️ 刷新: {refresh_str} | OCR检测: {roi_str}"
        if hasattr(self, 'roi_overlay') and self.roi_overlay:
            self.roi_overlay.update_countdown_text(text)

    def segment_rows(self, img_bgr):
        h_img, w_img = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        
        if np.mean(gray) > 127:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        kernel_len = max(10, w_img // 3)
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
        horiz_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel)
        binary_clean = cv2.subtract(binary, horiz_lines)

        proj = np.sum(binary_clean, axis=1)
        rows = []
        in_row = False
        start_y = 0
        min_row_height = 4

        for y, val in enumerate(proj):
            if val > (1 * 255) and not in_row:
                in_row = True
                start_y = y
            elif val <= (1 * 255) and in_row:
                in_row = False
                if (y - start_y) >= min_row_height:
                    rows.append((max(0, start_y - 2), min(h_img, y + 2)))

        if in_row and (len(proj) - start_y) >= min_row_height:
            rows.append((max(0, start_y - 2), h_img))

        if not rows or (len(rows) == 1 and (rows[0][1] - rows[0][0]) > h_img * 0.75 and h_img > 30):
            contours, _ = cv2.findContours(binary_clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            boxes = []
            for c in contours:
                bx, by, bw, bh = cv2.boundingRect(c)
                if bh >= 4 and bw >= 2:
                    boxes.append((bx, by, bw, bh))

            if boxes:
                boxes.sort(key=lambda b: b[1])
                grouped_rows = []
                curr_row = [boxes[0]]

                for b in boxes[1:]:
                    prev_y = curr_row[-1][1]
                    prev_h = curr_row[-1][3]
                    if abs(b[1] - prev_y) < max(prev_h, b[3]) * 0.6:
                        curr_row.append(b)
                    else:
                        grouped_rows.append(curr_row)
                        curr_row = [b]
                if curr_row:
                    grouped_rows.append(curr_row)

                rows = []
                for g in grouped_rows:
                    min_y = min(b[1] for b in g)
                    max_y = max(b[1] + b[3] for b in g)
                    rows.append((max(0, min_y - 2), min(h_img, max_y + 2)))

        if not rows:
            rows = [(0, h_img)]

        return rows

    def perform_roi_ocr_check(self):
        if not self.roi_list or not HAS_DDDDOCR or self.ocr is None:
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

                self.box_latest_digits[box_idx] = box_digits
                val_str = " | ".join(box_digits) if box_digits else "无"
                log_lines.append(f"【区域#{box_idx}】 (共{len(box_digits)}行数字): [{val_str}]")

                has_new_unacked_alarm = False

                if user_target_val:
                    curr_count = box_digits.count(user_target_val)
                    ack_count = self.box_ack_count.get(box_idx, 0)
                    if curr_count >= target_same_n and curr_count > ack_count:
                        has_new_unacked_alarm = True
                else:
                    current_matches = set()
                    for i in range(len(box_digits) - target_same_n + 1):
                        sub_group = tuple(box_digits[i : i + target_same_n])
                        if sub_group and all(x == sub_group[0] for x in sub_group):
                            current_matches.add((i, sub_group))

                    ack_matches = self.box_ack_matches.get(box_idx, set())
                    new_matches = current_matches - ack_matches

                    if len(new_matches) > 0:
                        has_new_unacked_alarm = True

                if has_new_unacked_alarm:
                    self.box_is_alarming[box_idx] = True

            self.log(f"🎯 监控检测结果:\n" + "\n".join(log_lines))
            self.roi_overlay.set_alarm_states(self.box_is_alarming)

            alarming_boxes = [idx for idx, is_al in self.box_is_alarming.items() if is_al]
            if alarming_boxes:
                msg = f"🚨 区域 {alarming_boxes} 触发增量新报警！"
                self.roi_status_label.setText(f"状态: {msg}")
                self.status_label.setText(msg)
                
                if not self.alarm_loop_timer.isActive():
                    self.alarm_loop_timer.start(1200)
                    self.execute_single_alarm_tick()
            else:
                self.stop_alarm_audio()
                rule_tip = f"目标值 '{user_target_val}'" if user_target_val else f"单区域连续 {target_same_n} 行相同"
                self.roi_status_label.setText(f"状态: 监控中 | 规则: {rule_tip}")

        except Exception as e:
            self.log(f"❌ OCR 运行异常: {e}")

    def clear_alarm_for_box(self, box_idx):
        self.box_is_alarming[box_idx] = False

        user_target_val = self.target_value_input.text().strip()
        box_digits = self.box_latest_digits.get(box_idx, [])

        if user_target_val:
            self.box_ack_count[box_idx] = box_digits.count(user_target_val)
        else:
            target_same_n = self.target_same_count_spin.value()
            current_matches = set()
            for i in range(len(box_digits) - target_same_n + 1):
                sub_group = tuple(box_digits[i : i + target_same_n])
                if sub_group and all(x == sub_group[0] for x in sub_group):
                    current_matches.add((i, sub_group))

            if box_idx not in self.box_ack_matches:
                self.box_ack_matches[box_idx] = set()
            self.box_ack_matches[box_idx].update(current_matches)

        self.log(f"🔕 已消除【区域 #{box_idx}】的报警，追加新行时不再重复报警")
        self.roi_overlay.set_alarm_states(self.box_is_alarming)

        if not any(self.box_is_alarming.values()):
            self.stop_alarm_audio()
            self.roi_status_label.setText("状态: 报警已消除，监控中...")

    def clear_all_alarms(self):
        for box_idx in range(1, len(self.roi_list) + 1):
            self.clear_alarm_for_box(box_idx)

    def stop_alarm_audio(self):
        if self.alarm_loop_timer.isActive():
            self.alarm_loop_timer.stop()

    def execute_single_alarm_tick(self):
        cur_data = self.sound_combo.currentData()
        target_wav = self.custom_sound_path if cur_data == "CUSTOM" else cur_data

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
        cur_data = self.sound_combo.currentData()
        if cur_data == "CUSTOM": 
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择自定义报警铃声", self.custom_sound_path or os.getcwd(), "音频文件 (*.wav)"
            )
            if file_path:
                self.custom_sound_path = file_path
                self.log("🎵 已设定自定义铃声路径")
            else:
                self.sound_combo.setCurrentIndex(0)

    def open_account_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("账号设置")
        dlg.setModal(True)
        dlg.resize(360, 170)
        layout = QVBoxLayout(dlg)
        row1 = QHBoxLayout(); row1.addWidget(QLabel("账号"))
        account = QLineEdit(self.config.get("account", "")); account.setPlaceholderText("请输入账号")
        row1.addWidget(account, 1); layout.addLayout(row1)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("密码"))
        password = QLineEdit(self.config.get("password", "")); password.setEchoMode(QLineEdit.Password); password.setPlaceholderText("请输入密码")
        row2.addWidget(password, 1); layout.addLayout(row2)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton("取消"); save = QPushButton("保存")
        buttons.addWidget(cancel); buttons.addWidget(save); layout.addLayout(buttons)
        cancel.clicked.connect(dlg.reject)
        def do_save():
            self.config["account"] = account.text().strip()
            self.config["password"] = password.text()
            save_config(self.config)
            self.account_btn.setText("👤 账号 ✓" if self.config["account"] and self.config["password"] else "👤 账号")
            self.log("👤 账号密码已保存")
            dlg.accept()
        save.clicked.connect(do_save)
        dlg.exec()

    def paste_credentials(self):
        account = self.config.get("account", "").strip()
        password = self.config.get("password", "").strip()
        if not account or not password: return
        safe_account = json.dumps(account)
        safe_password = json.dumps(password)
        js_cmd = f"if (typeof window.__fillV7 === 'function') {{ window.__fillV7({safe_account}, {safe_password}); }}"
        self.webview.page().runJavaScript(js_cmd)
        self.log("📋 已一键粘贴账号密码并触发 Enter(回车) 键，请在需要时手动输入验证码！")

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
            
            from PySide6.QtNetwork import QNetworkRequest
            self.reply = self.nam.get(QNetworkRequest(QUrl(qr_api)))
            self.reply.finished.connect(self.on_qr_downloaded)
            self.log(f"📸 截图已保存，生成链接: {self.current_file_url}")

    def on_qr_downloaded(self):
        from PySide6.QtNetwork import QNetworkReply
        if self.reply.error() == QNetworkReply.NoError:
            qr_pixmap = QPixmap()
            qr_pixmap.loadFromData(self.reply.readAll())
            QRDialog(self, qr_pixmap, self.current_file_url).exec()
        self.reply.deleteLater()

    def save_settings(self):
        self.config["url"] = self.url_input.text().strip()
        # 账号和密码由独立“账号”弹窗保存，这里不再读取已删除的输入框。
        self.config["zoom_level"] = self.zoom_spin.value() / 100.0
        self.config["auto_refresh"] = self.auto_refresh_cb.isChecked()
        self.config["auto_interval"] = self.auto_interval.value()
        self.config["selected_ip"] = self.ip_combo.currentText() 
        self.config["reminder_sound_index"] = self.sound_combo.currentIndex()
        self.config["reminder_custom_path"] = self.custom_sound_path
        
        self.config["roi_list"] = [[r.x(), r.y(), r.width(), r.height()] for r in self.roi_list]
        self.config["roi_multiplier"] = self.roi_multiplier_spin.value()
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
        self.update_top_right_countdown_bar()

    def stop_auto_timer(self):
        self.refresh_clock.stop()
        self.countdown_label.setText("")
        self.log("⏱️ 自动刷新已停止")
        self.update_top_right_countdown_bar()

    def on_refresh_clock_tick(self):
        if self.remaining_seconds > 1:
            self.remaining_seconds -= 1
            self.countdown_label.setText(f"下次刷新: {self.remaining_seconds}秒")
        else:
            self.refresh_page()
            self.remaining_seconds = self.auto_interval.value()
        self.update_top_right_countdown_bar()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
        else:
            super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if self.is_fullscreen:
            self.showNormal()
            self.is_fullscreen = False
            if hasattr(self, "settings_btn"):
                self.settings_btn.show()
                self.settings_btn.raise_()
        else:
            if hasattr(self, "settings_panel"):
                self.settings_panel.hide()
            if hasattr(self, "settings_btn"):
                self.settings_btn.hide()
            self.showFullScreen()
            self.is_fullscreen = True

    def closeEvent(self, event):
        event.accept()
        self.quit_app()

    def quit_app(self):
        if hasattr(self, 'refresh_clock'): self.refresh_clock.stop()
        if hasattr(self, 'roi_clock_timer'): self.roi_clock_timer.stop()
        if hasattr(self, 'alarm_loop_timer'): self.alarm_loop_timer.stop()
        self.tray.hide()
        QApplication.quit()
        try:
            current_pid = os.getpid()
            if sys.platform == "win32":
                subprocess.Popen(f"taskkill /F /T /PID {current_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import signal
                os.killpg(os.getpgrp(), signal.SIGKILL)
        except: pass
        os._exit(0)


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
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--ignore-certificate-errors"
    app = QApplication(sys.argv)
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
    
    win = MainWindow()
    win.show()
    sys.exit(app.exec())