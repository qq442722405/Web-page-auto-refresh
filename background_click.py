"""Coordinate mouse input to embedded Chromium, without touching the OS mouse.

Points are QWebEngineView logical pixels. Qt handles DPI, zoom and frame hit tests.
"""


class OperationGate:
    def __init__(self):
        self.generation = 0
        self.pending = None

    def begin(self, kind):
        if self.pending is not None:
            return None
        self.generation += 1
        self.pending = (self.generation, kind)
        return self.pending

    def finish(self, ticket):
        if self.pending != ticket or ticket is None:
            return False
        self.pending = None
        return True

    def cancel(self):
        self.generation += 1
        self.pending = None


def send_background_click(view, point):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    if not view.rect().contains(point):
        raise ValueError('点位已超出网页区域，请重新拾取')
    # QWebEngineView itself does not forward these events to Chromium. The
    # focus proxy is its render widget; direct delivery bypasses the ROI overlay.
    receiver = view.focusProxy()
    if (receiver is None or not isValid(receiver)
            or not view.isAncestorOf(receiver)):
        raise RuntimeError('网页输入窗口尚未准备好，请等待页面加载后再试')
    local = receiver.mapFrom(view, point)
    if not receiver.rect().contains(local):
        raise ValueError('点位不在网页内容区，请重新拾取')
    scene = receiver.mapTo(receiver.window(), local)
    screen = receiver.mapToGlobal(local)
    for kind, button, buttons in (
        (QEvent.MouseMove, Qt.NoButton, Qt.NoButton),
        (QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton),
        (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton),
    ):
        if not isValid(receiver):
            raise RuntimeError('网页输入窗口已关闭，点击结果未知')
        event = QMouseEvent(kind, QPointF(local), QPointF(scene), QPointF(screen),
                            button, buttons, Qt.NoModifier)
        # Do not pump the event loop between press and release, or leave queued
        # presses that can run after a mode change/navigation.
        QApplication.sendEvent(receiver, event)
