"""Real Qt WebEngine integration tests. Requires PySide6 + QtWebEngine, no OCR.

Run on a desktop: python tests/test_browser.py
Optional high-DPI run: set QT_SCALE_FACTOR=1.5 before launching.

Covers:
  * Qt 合成鼠标事件在网页内的坐标点击（canvas / Shadow DOM / iframe / 缩放 / 滚动 / 窗口移动）
  * 其它进程遮挡时，两种后台通道仍然能点动网页
  * Chromium 调试通道（CDP）在同一场景下的实际表现
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows CI 的控制台默认使用 cp1252，打印中文会抛 UnicodeEncodeError。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is None:
        continue
    try:
        _reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CDP_PORT = 9455


def main():
    # 调试端口必须在创建 QApplication 之前声明，否则 Chromium 不会监听。
    os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "127.0.0.1:%d" % CDP_PORT

    from PySide6.QtCore import QPoint, Qt, QUrl
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtTest import QTest

    from webmonitor.page_controller import PageController
    from webmonitor.qt_click import send_background_click
    from webmonitor.ui_overlay import PersistentROIOverlay

    app = QApplication.instance() or QApplication([])
    view = QWebEngineView()
    view.resize(900, 650)
    view.move(80, 80)
    view.show()
    checks = []
    cover = None

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print("PASS", name, flush=True)

    def spin_until(fn, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if fn():
                return
            QTest.qWait(20)
        raise AssertionError("Timed out waiting for browser")

    def js(code):
        result = []
        view.page().runJavaScript(code, lambda value: result.append(value))
        spin_until(lambda: bool(result))
        return result[0]

    def counts():
        return json.loads(js("JSON.stringify(counts)"))

    def point(selector):
        x, y = json.loads(js(
            "JSON.stringify((()=>{const r=document.querySelector(" + json.dumps(selector)
            + ").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())"))
        return QPoint(round(x * view.zoomFactor()), round(y * view.zoomFactor()))

    def click_count(name, pt, channel="qt"):
        before = counts()[name]
        cursor = QCursor.pos()
        if channel == "cdp":
            controller.click(pt)
        else:
            send_background_click(view, pt)
        spin_until(lambda: counts()[name] > before, timeout=15)
        QTest.qWait(80)
        check("%s single click via %s" % (name, channel), counts()[name] == before + 1)
        check("%s cursor unchanged via %s" % (name, channel), QCursor.pos() == cursor)

    try:
        loaded = []
        view.loadFinished.connect(loaded.append)
        view.load(QUrl.fromLocalFile(str(ROOT / "tests" / "fixture.html")))
        spin_until(lambda: bool(loaded))
        check("page loaded", loaded[-1])
        QTest.qWait(300)

        controller = PageController(view)
        controller.configure(cdp_enabled=True, cdp_port=CDP_PORT, strategy="cdp")
        QTest.qWait(300)

        overlay = PersistentROIOverlay(view)
        overlay.setGeometry(view.rect())
        overlay.show()
        selected = []
        overlay.point_selected.connect(selected.append)
        button_point = point("#button")
        overlay.start_point_picker()
        QTest.mouseClick(overlay, Qt.LeftButton, Qt.NoModifier, button_point)
        QTest.qWait(100)
        check("picker saves exact point", selected == [button_point])
        check("picker does not click page", counts()["button"] == 0)
        check("picker releases input overlay", not overlay.is_picking_point)

        click_count("button", button_point)
        events = json.loads(js("JSON.stringify(events)"))
        check("full mouse sequence", [e["type"] for e in events] ==
              ["pointerdown", "mousedown", "pointerup", "mouseup", "click"])
        check("browser input is trusted", all(e["trusted"] for e in events))
        js("document.querySelector('#button span').textContent='changed text'")
        click_count("button", button_point)
        click_count("canvas", point("#canvas"))
        click_count("shadow", QPoint(490, 70))
        click_count("same", QPoint(100, 180))
        click_count("cross", QPoint(470, 180))

        for zoom in (0.75, 1.25, 2.0):
            view.setZoomFactor(zoom)
            QTest.qWait(300)
            click_count("button", point("#button"))
        view.setZoomFactor(1)
        QTest.qWait(200)
        button_point = point("#button")
        view.move(150, 140)
        click_count("button", button_point)
        js("document.querySelector('#scrolled').scrollIntoView()")
        QTest.qWait(100)
        click_count("scrolled", point("#scrolled"))
        js("scrollTo(0,0)")
        QTest.qWait(100)

        try:
            send_background_click(view, QPoint(-1, 20))
        except ValueError:
            check("out of bounds rejected", True)
        else:
            check("out of bounds rejected", False)

        # ---- 遮挡场景 ----
        if sys.platform == "win32":
            cover = subprocess.Popen([sys.executable, __file__, "--cover", str(int(view.winId()))],
                                     stdout=subprocess.PIPE, text=True)
            ready = []
            threading.Thread(target=lambda: ready.append(cover.stdout.readline()), daemon=True).start()
            spin_until(lambda: bool(ready))
            cover_hwnd = int(ready[0].strip())
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                                        ctypes.POINTER(ctypes.c_ulong)]
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
            user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetWindow.restype = ctypes.c_void_p

            view_rect, cover_rect = wintypes.RECT(), wintypes.RECT()
            check("read view bounds", bool(user32.GetWindowRect(int(view.winId()),
                                                                ctypes.byref(view_rect))))
            check("read cover bounds", bool(user32.GetWindowRect(cover_hwnd,
                                                                 ctypes.byref(cover_rect))))
            check("other process covers browser bounds",
                  cover_rect.left <= view_rect.left and cover_rect.top <= view_rect.top
                  and cover_rect.right >= view_rect.right and cover_rect.bottom >= view_rect.bottom)
            above = user32.GetWindow(int(view.winId()), 3)  # GW_HWNDPREV
            while above and above != cover_hwnd:
                above = user32.GetWindow(above, 3)
            check("other process is above browser in Z order", above == cover_hwnd)
            foreground = user32.GetForegroundWindow()
            if foreground and foreground != int(view.winId()):
                check("another window is foreground", True)
            else:
                print("SKIP active foreground assertion: no external foreground window available",
                      flush=True)

            # 被完全遮挡时，Qt 事件通道与调试通道都应继续工作
            click_count("button", button_point, channel="qt")
            controller.configure(strategy="cdp")
            click_count("canvas", point("#canvas"), channel="cdp")
            click_count("cross", QPoint(470, 180), channel="cdp")
            check("foreground HWND unchanged", user32.GetForegroundWindow() == foreground)

        print("%d Qt WebEngine checks passed; DPR=%s" % (len(checks), view.devicePixelRatioF()),
              flush=True)
    finally:
        if cover is not None:
            cover.terminate()
            cover.wait(timeout=5)
        view.close()
        app.processEvents()


if __name__ == "__main__":
    if "--cover" in sys.argv:
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import QApplication, QLabel

        app = QApplication([])
        label = QLabel("Background click test: covering window")
        label.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        label.resize(300, 200)
        label.show()
        label.raise_()
        label.activateWindow()

        def place_cover():
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_uint]
            rect = wintypes.RECT()
            if not user32.GetWindowRect(int(sys.argv[-1]), ctypes.byref(rect)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not user32.SetWindowPos(int(label.winId()), -1, rect.left - 16, rect.top - 16,
                                       rect.right - rect.left + 32, rect.bottom - rect.top + 32,
                                       0x0040):
                raise ctypes.WinError(ctypes.get_last_error())
            QTimer.singleShot(200, lambda: print(int(label.winId()), flush=True))

        QTimer.singleShot(100, place_cover)
        QTimer.singleShot(60000, app.quit)
        sys.exit(app.exec())
    main()
