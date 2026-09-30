# -*- coding: utf-8 -*-
"""网页内浮层：识别框 ROI、报警消除按钮、拾取点位、右上角控制栏。

坐标统一使用 QWebEngineView 的内部坐标。好处：
  - 移动/缩放软件窗口不必重新框选；
  - 直接换算为 CSS 像素即可交给 Chromium 调试通道；
  - OCR 截图可以从网页本身取图，不再依赖屏幕坐标。
"""
from PySide6.QtCore import QRect, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .coords import clamp_rect_values

HANDLE_SIZE = 10
CORNER_SIZE = 8
MIN_DRAG_SIZE = 8


class PersistentROIOverlay(QWidget):
    roi_list_selected = Signal(list)
    clear_alarm_requested = Signal(int)
    point_selected = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.rects = []
        self.is_editing = False
        self.is_picking_point = False
        # 非编辑状态下不拦截网页鼠标事件，网页可正常点击、滚动、输入。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.boxes_visible = True
        self.alarm_states = {}
        self.alarm_buttons = {}
        self.drag_index = None
        self.drag_action = None
        self.drag_start = None
        self.original_rect = None
        self.drawing_rect = None

        self.bar = QWidget(self)
        self.bar.setStyleSheet("""
            QWidget { background:#181825; border:1px solid #45475a; border-radius:6px; }
            QLabel { color:#a6e3a1; font-weight:bold; font-size:11px; }
            QPushButton { background:#313244; color:#fff; border:1px solid #45475a;
                          border-radius:4px; padding:4px 8px; font-size:11px; font-weight:bold; }
            QPushButton:hover { background:#45475a; }
        """)
        layout = QHBoxLayout(self.bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(5)
        self.tip_label = QLabel("网页内调整：拖动框体移动 | 拖四角缩放 | 空白处划框")
        self.btn_done = QPushButton("✅ 完成")
        self.btn_clear = QPushButton("🗑️ 清空")
        self.btn_cancel = QPushButton("取消")
        self.btn_done.clicked.connect(self.finish_editing)
        self.btn_clear.clicked.connect(self.clear_rects)
        self.btn_cancel.clicked.connect(self.cancel_editing)
        layout.addWidget(self.tip_label, 1)
        layout.addWidget(self.btn_done)
        layout.addWidget(self.btn_clear)
        layout.addWidget(self.btn_cancel)
        self.bar.hide()

    # ---------------------------------------------------------- 布局 --
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_bar()

    def reposition_bar(self):
        self.bar.adjustSize()
        self.bar.move(max(8, (self.width() - self.bar.width()) // 2), 10)

    # ---------------------------------------------------------- 状态 --
    def set_boxes_visible(self, visible):
        self.boxes_visible = bool(visible)
        self.update_alarm_buttons()
        self.update()

    def toggle_boxes_visibility(self):
        self.boxes_visible = not self.boxes_visible
        self.update_alarm_buttons()
        self.update()
        return self.boxes_visible

    def set_rects(self, rects):
        self.rects = [QRect(rect) for rect in rects]
        self.update_alarm_buttons()
        self.update()

    # --------------------------------------------------- 拾取点位模式 --
    def start_point_picker(self):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.is_picking_point = True
        self.is_editing = False
        self.setCursor(Qt.CrossCursor)
        self.bar.hide()
        self.raise_()
        self.update()

    def finish_point_picker(self):
        self.is_picking_point = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.ArrowCursor)
        self.update()

    # ------------------------------------------------------ 编辑模式 --
    def start_editing(self, existing_rects):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.is_editing = True
        self.rects = [QRect(rect) for rect in existing_rects]
        self.drag_index = None
        self.drag_action = None
        self.drawing_rect = None
        self.setCursor(Qt.CrossCursor)
        self.bar.show()
        self.reposition_bar()
        self.bar.raise_()
        self.raise_()
        self._sync_tip()

    def _sync_tip(self):
        self.tip_label.setText("网页内调整：拖动/缩放 | 当前 %d 个选框" % len(self.rects))

    def finish_editing(self):
        if not self.is_editing:
            return
        self.is_editing = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.ArrowCursor)
        self.bar.hide()
        self.update()
        self.roi_list_selected.emit([QRect(rect) for rect in self.rects])

    def cancel_editing(self):
        self.is_editing = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.ArrowCursor)
        self.bar.hide()
        self.update()

    def clear_rects(self):
        self.rects.clear()
        self.drag_action = None
        self.drag_index = None
        self.drawing_rect = None
        self.alarm_states = {}
        self.update_alarm_buttons()
        self.update()

    # ---------------------------------------------------------- 报警 --
    def set_alarm_states(self, states):
        self.alarm_states = dict(states or {})
        self.update_alarm_buttons()
        self.update()

    def update_alarm_buttons(self):
        active = set()
        for index, _ in enumerate(self.rects, 1):
            if not self.alarm_states.get(index, False) or not self.boxes_visible:
                continue
            active.add(index)
            # 按钮挂在网页视图上而不是本浮层上：浮层平时对鼠标透明，
            # 挂在它下面的控件会一起收不到点击。两者原点尺寸一致，坐标可直接复用。
            owner = self.parentWidget() or self
            button = self.alarm_buttons.get(index)
            if button is None:
                button = QPushButton("🔕 消除报警", owner)
                button.setFixedSize(92, 26)
                button.setStyleSheet(
                    "QPushButton{background:#ef4444;color:white;border:1px solid #fecaca;"
                    "border-radius:5px;font-size:11px;font-weight:bold;padding:2px 5px;}"
                    "QPushButton:hover{background:#dc2626;}")
                button.clicked.connect(
                    lambda checked=False, idx=index: self.clear_alarm_requested.emit(idx))
                self.alarm_buttons[index] = button
            rect = self.rects[index - 1]
            button.move(max(2, rect.right() - button.width()),
                       max(2, rect.top() - button.height() - 4))
            button.show()
            button.raise_()
        for index, button in list(self.alarm_buttons.items()):
            if index not in active:
                button.hide()
        if self.is_editing:
            self.bar.raise_()

    # ------------------------------------------------------ 鼠标交互 --
    def _handle_at(self, rect, pos):
        for point, name in ((rect.topLeft(), "NW"), (rect.topRight(), "NE"),
                            (rect.bottomLeft(), "SW"), (rect.bottomRight(), "SE")):
            if (pos - point).manhattanLength() <= HANDLE_SIZE:
                return name
        if rect.contains(pos):
            return "MOVE"
        return None

    def _clamp(self, x, y, width, height):
        return clamp_rect_values(x, y, width, height, self.width(), self.height())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        if self.is_picking_point:
            # 吞掉按下事件，直到抬起才算完成拾取，绝不会顺带点击网页。
            event.accept()
            return
        if not self.is_editing:
            return
        if self.bar.geometry().contains(pos):
            return
        for index in reversed(range(len(self.rects))):
            action = self._handle_at(self.rects[index], pos)
            if action:
                self.drag_index = index
                self.drag_action = action
                self.drag_start = pos
                self.original_rect = QRect(self.rects[index])
                return
        self.drag_action = "DRAW"
        self.drag_index = None
        self.drag_start = pos
        self.drawing_rect = QRect(pos, pos)
        self.update()

    def mouseMoveEvent(self, event):
        if (not self.is_editing) or not self.drag_action:
            return
        pos = event.position().toPoint()
        rect = self.original_rect
        if self.drag_action == "MOVE":
            moved = rect.translated(pos - self.drag_start)
            x, y, w, h = self._clamp(moved.x(), moved.y(), moved.width(), moved.height())
            self.rects[self.drag_index] = QRect(x, y, w, h)
        elif self.drag_action == "SE":
            x, y, w, h = self._clamp(rect.x(), rect.y(),
                                     pos.x() - rect.x(), pos.y() - rect.y())
            self.rects[self.drag_index] = QRect(x, y, w, h)
        elif self.drag_action == "NW":
            x, y, w, h = self._clamp(pos.x(), pos.y(),
                                     rect.right() - pos.x(), rect.bottom() - pos.y())
            self.rects[self.drag_index] = QRect(x, y, w, h)
        elif self.drag_action == "NE":
            x, y, w, h = self._clamp(rect.x(), pos.y(),
                                     pos.x() - rect.x(), rect.bottom() - pos.y())
            self.rects[self.drag_index] = QRect(x, y, w, h)
        elif self.drag_action == "SW":
            x, y, w, h = self._clamp(pos.x(), rect.y(),
                                     rect.right() - pos.x(), pos.y() - rect.y())
            self.rects[self.drag_index] = QRect(x, y, w, h)
        elif self.drag_action == "DRAW":
            self.drawing_rect = QRect(self.drag_start, pos).normalized()
        self.update_alarm_buttons()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.is_picking_point:
            point = event.position().toPoint()
            self.finish_point_picker()
            event.accept()
            self.point_selected.emit(point)
            return
        if self.is_editing and self.drag_action == "DRAW":
            rect = self.drawing_rect
            if rect is not None and rect.width() > MIN_DRAG_SIZE and rect.height() > MIN_DRAG_SIZE:
                x, y, w, h = self._clamp(rect.x(), rect.y(), rect.width(), rect.height())
                self.rects.append(QRect(x, y, w, h))
        self.drag_action = None
        self.drag_index = None
        self.drawing_rect = None
        self._sync_tip()
        self.update_alarm_buttons()
        self.update()

    # ---------------------------------------------------------- 绘制 --
    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.boxes_visible and not self.is_editing:
            return
        for index, rect in enumerate(self.rects, 1):
            alarm = self.alarm_states.get(index, False)
            color = QColor("#ef4444") if alarm else QColor("#00ff66")
            painter.setPen(QPen(color, 3 if alarm else 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            badge = QRect(rect.x(), max(0, rect.y() - 21), 42, 21)
            painter.fillRect(badge, color)
            painter.setPen(QPen(QColor("#000000")))
            painter.drawText(badge, Qt.AlignCenter, "#%d" % index)
            if self.is_editing:
                painter.setPen(QPen(QColor("#38bdf8"), 2))
                painter.setBrush(QColor("#ffffff"))
                for corner in (rect.topLeft(), rect.topRight(),
                               rect.bottomLeft(), rect.bottomRight()):
                    painter.drawRect(corner.x() - CORNER_SIZE // 2,
                                     corner.y() - CORNER_SIZE // 2,
                                     CORNER_SIZE, CORNER_SIZE)
        if self.is_editing and self.drag_action == "DRAW" and self.drawing_rect:
            painter.setPen(QPen(QColor("#38bdf8"), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.drawing_rect)


class FloatingControlBar(QWidget):
    """独立于 Overlay 的右上角控制栏：按钮可点、整条可拖动。"""
    moved = Signal()

    def __init__(self, parent, on_hide_show, on_settings):
        super().__init__(parent)
        self._drag_offset = None
        self._dragging = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget { background:#181825; border:1px solid #45475a; border-radius:7px; }
            QLabel { color:#38bdf8; font-weight:bold; font-size:11px; padding:0 3px;
                     background:transparent; border:none; }
            QPushButton { background:#313244; color:#cdd6f4; border:1px solid #45475a;
                          border-radius:5px; padding:5px 9px; font-size:11px; font-weight:bold; }
            QPushButton:hover { background:#45475a; color:#fff; }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(5)
        self.lbl_countdown = QLabel("⏱️ 操作: -- | OCR检测: --")
        self.lbl_countdown.setMinimumWidth(150)
        self.btn_toggle_vis = QPushButton("👁️ 隐藏识别框")
        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_toggle_vis.clicked.connect(on_hide_show)
        self.btn_settings.clicked.connect(on_settings)
        layout.addWidget(self.lbl_countdown)
        layout.addWidget(self.btn_toggle_vis)
        layout.addWidget(self.btn_settings)
        self.adjustSize()

    def set_countdown(self, text):
        self.lbl_countdown.setText(text)
        self.adjustSize()

    def set_visibility_text(self, visible):
        self.btn_toggle_vis.setText("👁️ 隐藏识别框" if visible else "👁️ 显示识别框")
        self.adjustSize()

    def set_settings_text(self, opened):
        self.btn_settings.setText("✕ 收起" if opened else "⚙ 设置")
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child in (self.btn_toggle_vis, self.btn_settings):
                return super().mousePressEvent(event)
            self._dragging = True
            self._drag_offset = event.position().toPoint()
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_offset is not None:
            parent = self.parentWidget()
            if parent is not None:
                moved = self.mapToParent(event.position().toPoint() - self._drag_offset)
                x = max(0, min(moved.x(), max(0, parent.width() - self.width())))
                y = max(0, min(moved.y(), max(0, parent.height() - self.height())))
                self.move(x, y)
                self.moved.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
