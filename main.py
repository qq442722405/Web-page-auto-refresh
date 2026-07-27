# ---------- 屏幕 ROI 框选遮罩组件 ----------
class ROIOverlay(QWidget):
    roi_selected = Signal(QRect)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 修正：将 Qt.StayOnTopHint 改为 Qt.WindowStaysOnTopHint
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
