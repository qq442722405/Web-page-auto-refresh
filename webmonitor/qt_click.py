# -*- coding: utf-8 -*-
"""向本软件内嵌 Chromium 投递鼠标事件（Qt 层，第二梯队）。

要点：
1. QWebEngineView 自身不会把收到的合成事件转给 Chromium，真正接收输入的是它的焦点代理
   （RenderWidgetHostView 对应的窗口），因此必须把事件发到 receiver 上；
2. 直接投递会绕过最上层 ROI 浮层，不需要隐藏识别框；
3. 不按下/抬起之间泵事件循环，避免遗留按下状态；
4. 全部投完后轻泵一次，帮助 Qt 把输入队列冲到渲染进程。
"""
from shiboken6 import isValid


class QtClickError(RuntimeError):
    pass


def _receiver(view):
    receiver = view.focusProxy()
    if receiver is None or not isValid(receiver) or not view.isAncestorOf(receiver):
        raise QtClickError("网页输入窗口尚未准备好，请等待页面加载后再试")
    return receiver


def renderer_ready(view):
    try:
        _receiver(view)
        return True
    except QtClickError:
        return False


def send_background_click(view, point, pump_after=True):
    """在网页内部坐标 point 上完成一次左键点击，不移动系统鼠标。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    if view is None or not isValid(view):
        raise QtClickError("网页视图已不可用")
    if not view.rect().contains(point):
        raise QtClickError("点位已超出网页区域，请重新拾取")

    receiver = _receiver(view)
    local = receiver.mapFrom(view, point)
    if not receiver.rect().contains(local):
        raise QtClickError("点位不在网页内容区，请重新拾取")

    scene = receiver.mapTo(receiver.window(), local)
    screen = receiver.mapToGlobal(local)

    for kind, button, buttons in (
        (QEvent.MouseMove, Qt.NoButton, Qt.NoButton),
        (QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton),
        (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton),
    ):
        if not isValid(receiver):
            raise QtClickError("网页输入窗口已关闭，点击结果未知")
        event = QMouseEvent(kind, QPointF(local), QPointF(scene), QPointF(screen),
                            button, buttons, Qt.NoModifier)
        QApplication.sendEvent(receiver, event)

    if pump_after:
        # 释放之后才泵事件，既不会打断 press/release 的原子性，也能让 Qt 尽快投递。
        QApplication.processEvents()
    return True
