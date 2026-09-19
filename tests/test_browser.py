"""Real Qt WebEngine integration tests. Requires PySide6, no OCR or Playwright.

Run on a desktop: python tests/test_browser.py
Optional high-DPI run: set QT_SCALE_FACTOR=1.5 before launching.
Creates a separate process window to cover the browser and check foreground HWND.
"""
import ast
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtCore import Qt, QPoint, QRect, Signal, QUrl
    from PySide6.QtGui import QCursor, QPainter, QColor, QPen
    from PySide6.QtWidgets import QApplication, QWidget, QSizePolicy, QHBoxLayout, QLabel, QPushButton
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtTest import QTest
    from background_click import send_background_click

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
        print('PASS', name, flush=True)

    def spin_until(fn, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if fn():
                return
            QTest.qWait(20)
        raise AssertionError('Timed out waiting for browser')

    def js(code):
        result = []
        view.page().runJavaScript(code, lambda value: result.append(value))
        spin_until(lambda: bool(result))
        return result[0]

    def counts():
        return json.loads(js('JSON.stringify(counts)'))

    def point(selector):
        x, y = json.loads(js('JSON.stringify((()=>{const r=document.querySelector('
                             + json.dumps(selector) + ').getBoundingClientRect();'
                             'return [r.x+r.width/2,r.y+r.height/2]})())'))
        return QPoint(round(x * view.zoomFactor()), round(y * view.zoomFactor()))

    def click_count(name, pt):
        before = counts()[name]
        cursor = QCursor.pos()
        send_background_click(view, pt)
        spin_until(lambda: counts()[name] > before)
        QTest.qWait(80)
        check(name + ' single click', counts()[name] == before + 1)
        check(name + ' cursor unchanged', QCursor.pos() == cursor)

    try:
        loaded = []
        view.loadFinished.connect(loaded.append)
        view.load(QUrl.fromLocalFile(str(ROOT / 'tests' / 'fixture.html')))
        spin_until(lambda: bool(loaded))
        check('page loaded', loaded[-1])
        QTest.qWait(300)

        # Execute the actual overlay class without importing unrelated OCR/server code.
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PersistentROIOverlay')
        env = dict(locals())
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), env)
        overlay = env['PersistentROIOverlay'](view)
        overlay.setGeometry(view.rect())
        overlay.show()
        selected = []
        overlay.point_selected.connect(selected.append)
        pt = point('#button')
        overlay.start_point_picker()
        QTest.mouseClick(overlay, Qt.LeftButton, Qt.NoModifier, pt)
        QTest.qWait(100)
        check('picker saves exact point', selected == [pt])
        check('picker does not click page', counts()['button'] == 0)
        check('picker releases input overlay', not overlay.is_picking_point)
        click_count('button', pt)
        events = json.loads(js('JSON.stringify(events)'))
        check('full mouse sequence', [e['type'] for e in events] ==
              ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
        check('browser input is trusted', all(e['trusted'] for e in events))
        js("document.querySelector('#button span').textContent='changed text'")
        click_count('button', pt)
        click_count('canvas', point('#canvas'))
        click_count('shadow', QPoint(490, 70))
        click_count('same', QPoint(100, 180))
        click_count('cross', QPoint(470, 180))

        for zoom in [0.75, 1.25, 2.0]:
            view.setZoomFactor(zoom)
            QTest.qWait(300)
            click_count('button', point('#button'))
        view.setZoomFactor(1)
        QTest.qWait(200)
        pt = point('#button')
        view.move(150, 140)
        click_count('button', pt)
        js("document.querySelector('#scrolled').scrollIntoView()")
        QTest.qWait(100)
        click_count('scrolled', point('#scrolled'))
        js('scrollTo(0,0)')
        QTest.qWait(100)

        try:
            send_background_click(view, QPoint(-1, 20))
        except ValueError:
            check('out of bounds rejected', True)
        else:
            check('out of bounds rejected', False)

        if sys.platform == 'win32':
            # Independent application, not merely another widget in this process.
            cover = subprocess.Popen([sys.executable, __file__, '--cover'], stdout=subprocess.PIPE, text=True)
            ready = []
            threading.Thread(target=lambda: ready.append(cover.stdout.readline()), daemon=True).start()
            spin_until(lambda: bool(ready))
            cover_hwnd = int(ready[0].strip())
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            def covered():
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
                return pid.value == cover.pid
            from ctypes import wintypes
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
            user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetWindow.restype = ctypes.c_void_p
            rview, rcover = wintypes.RECT(), wintypes.RECT()
            check('read view bounds', bool(user32.GetWindowRect(int(view.winId()), ctypes.byref(rview))))
            check('read cover bounds', bool(user32.GetWindowRect(cover_hwnd, ctypes.byref(rcover))))
            check('other process covers browser bounds', rcover.left <= rview.left and
                  rcover.top <= rview.top and rcover.right >= rview.right and rcover.bottom >= rview.bottom)
            above = user32.GetWindow(int(view.winId()), 3)  # GW_HWNDPREV
            while above and above != cover_hwnd:
                above = user32.GetWindow(above, 3)
            check('other process is above browser in Z order', above == cover_hwnd)
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                spin_until(covered)
                hwnd = user32.GetForegroundWindow()
            else:
                print('SKIP active foreground assertion: test desktop has no foreground HWND', flush=True)
            click_count('button', point('#button'))
            click_count('canvas', point('#canvas'))
            click_count('cross', QPoint(470, 180))
            check('foreground HWND unchanged', user32.GetForegroundWindow() == hwnd)

        print(f'{len(checks)} Qt WebEngine checks passed; DPR={view.devicePixelRatioF()}', flush=True)
    finally:
        if cover is not None:
            cover.terminate()
            cover.wait(timeout=5)
        view.close()
        app.processEvents()


if __name__ == '__main__':
    if '--cover' in sys.argv:
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import QApplication, QLabel
        app = QApplication([])
        label = QLabel('Background click test: covering window')
        label.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        label.resize(1300, 900)
        label.move(0, 0)
        label.show()
        label.raise_()
        label.activateWindow()
        print(int(label.winId()), flush=True)
        QTimer.singleShot(60000, app.quit)
        sys.exit(app.exec())
    main()
