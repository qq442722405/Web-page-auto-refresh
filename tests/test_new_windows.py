"""Real browser regression for manual clicks that request a new window.

python tests/test_new_windows.py (requires PySide6, not OCR).
"""
import functools
import http.server
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtCore import QPoint, Qt, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtTest import QTest

    from webmonitor.qt_click import send_background_click
    from webmonitor.web_page import MonitorWebPage

    app = QApplication.instance() or QApplication([])
    view = QWebEngineView()
    view.setPage(MonitorWebPage(view))
    view.resize(850, 600)
    view.show()
    received_posts = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received_posts.append(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'<title>POST OK</title><p id="destination">POST received</p>')

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT / "tests")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_port

    def wait(fn, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if fn():
                return
            QTest.qWait(20)
        raise AssertionError("Navigation did not complete: %s" % view.url().toString())

    def js(code):
        result = []
        view.page().runJavaScript(code, result.append)
        wait(lambda: bool(result))
        return result[0]

    loaded = []
    view.loadFinished.connect(loaded.append)
    passed = 0
    try:
        for mode in ("manual", "background"):
            for target in ("same", "blank", "named", "script", "post"):
                loaded.clear()
                view.load(QUrl(base + "/new_windows.html"))
                wait(lambda: bool(loaded))
                assert loaded[-1]
                coords = json.loads(js(
                    "JSON.stringify((()=>{const r=document.getElementById(" + json.dumps(target)
                    + ").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())"))
                point = QPoint(round(coords[0]), round(coords[1]))
                loaded.clear()
                if mode == "manual":
                    # 顶层窗口输入走 Qt 的鼠标命中判定，等价于真实用户点击
                    QTest.mouseClick(view.windowHandle(), Qt.LeftButton, Qt.NoModifier, point)
                else:
                    send_background_click(view, point)
                wait(lambda: bool(loaded))
                assert loaded[-1]
                assert js('!!document.getElementById("destination")')
                if target == "post":
                    assert received_posts[-1] == "message=payload-kept", received_posts
                print("PASS", mode, target, flush=True)
                passed += 1
        print("%d new-window navigation checks passed" % passed, flush=True)
    finally:
        view.close()
        app.processEvents()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
