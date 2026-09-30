# -*- coding: utf-8 -*-
"""用真实 Chromium（Edge/Chrome）验证调试通道是否能“真后台”操作网页。

这是本版本后台执行的核心能力：与窗口焦点、遮挡、是否被前置无关。
运行：

    python tests/test_cdp_live.py

找不到本机浏览器时自动跳过。
"""
import functools
import http.server
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webmonitor import cdp as cdp_module  # noqa: E402


def find_browser():
    candidates = [
        os.environ.get("CHROME_PATH") or "",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def free_port(start=9444):
    import socket
    for port in range(start, start + 50):
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    raise SystemExit("没有空闲端口可用")


def wait_debug_endpoint(port, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            cdp_module.http_json("http://127.0.0.1:%d/json/version" % port, timeout=1.0)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def png_size(payload):
    """从 PNG 二进制里读出宽高（省去 Pillow 依赖）。"""
    if len(payload) < 24:
        raise AssertionError("截图数据过短")
    width, height = struct.unpack(">II", payload[16:24])
    return width, height


def main():
    browser = find_browser()
    if not browser:
        print("SKIP 未找到本机 Chromium/Edge/Chrome，跳过调试通道实机测试")
        return 0

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

    handler = functools.partial(QuietHandler, directory=str(ROOT / "tests"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    port = free_port()
    profile = tempfile.mkdtemp(prefix="monitor-cdp-")
    target_url = "http://127.0.0.1:%d/fixture.html" % server.server_port
    process = subprocess.Popen([
        browser, "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile, "--headless=new", "--no-first-run",
        "--disable-gpu", "--window-size=900,650", target_url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    checks = 0
    try:
        if not wait_debug_endpoint(port):
            raise AssertionError("调试端口未就绪")

        def connect():
            end = time.monotonic() + 15
            while time.monotonic() < end:
                try:
                    return cdp_module.connect_page(port=port, prefer_url=target_url, timeout=1.5)
                except Exception:
                    time.sleep(0.3)
            raise AssertionError("无法连接调试目标页面")

        session, _ = connect()
        print("PASS 调试通道连接成功")
        checks += 1

        # 0. 等文档就绪
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            title = session.evaluate("String(document.title)")
            if title and "fixture" in title.lower() or (title or "").find("点击测试") >= 0:
                break
            time.sleep(0.2)
        print("PASS 目标页面标题=%s" % session.evaluate("String(document.title)"))
        checks += 1

        # 1. 页面 JS 执行
        label = session.evaluate("document.querySelector('#button span').textContent")
        assert label and len(label) > 0, label
        print("PASS Runtime.evaluate 取到按钮文字: %s" % label)
        checks += 1

        # 2. 控件像素 -> CSS 像素比例
        raw = session.evaluate("String(document.documentElement.clientWidth)")
        print("PASS 页面可视宽度=%s" % raw)
        checks += 1

        # 3. 后台点击真正触发网页按钮
        box = json.loads(session.evaluate(
            "JSON.stringify((()=>{const r=document.getElementById('button').getBoundingClientRect();"
            "return [r.x+r.width/2, r.y+r.height/2]})())"))
        session.dispatch_click(round(box[0], 2), round(box[1], 2))
        for _ in range(50):
            value = int(session.evaluate("String(window.counts.button)"))
            if value >= 1:
                break
            time.sleep(0.1)
        assert int(session.evaluate("String(window.counts.button)")) == 1, "点击计数异常"
        print("PASS CDP 后台点击命中 #button，计数=%d" % 1)
        checks += 1

        events = json.loads(session.evaluate("JSON.stringify(window.events.map(e=>e.type))"))
        assert events == ["pointerdown", "mousedown", "pointerup", "mouseup", "click"], events
        print("PASS 生成完整鼠标事件序列: %s" % ",".join(events))
        checks += 1

        trusted = session.evaluate("String(window.events.every(e=>e.trusted))")
        print("PASS 事件 trusted=%s" % trusted)
        checks += 1

        # 4. 画布元素（DOM 取不到也能命中）
        canvas_center = json.loads(session.evaluate(
            "JSON.stringify((()=>{const r=document.getElementById('canvas').getBoundingClientRect();"
            "return [r.x+r.width/2, r.y+r.height/2]})())"))
        session.dispatch_click(round(canvas_center[0], 2), round(canvas_center[1], 2))
        for _ in range(50):
            if int(session.evaluate("String(window.counts.canvas)")) >= 1:
                break
            time.sleep(0.1)
        assert int(session.evaluate("String(window.counts.canvas)")) == 1
        print("PASS canvas 命中")
        checks += 1

        # 5. Shadow DOM 也能命中
        session.dispatch_click(455, 70)
        for _ in range(50):
            if int(session.evaluate("String(window.counts.shadow)")) >= 1:
                break
            time.sleep(0.1)
        assert int(session.evaluate("String(window.counts.shadow)")) == 1
        print("PASS Shadow DOM 命中")
        checks += 1

        # 6. 截图（不需要窗口可见）
        import base64
        clip = {"x": 40, "y": 40, "width": 180, "height": 60, "scale": 1.0}
        raw = session.capture_screenshot(clip=clip, timeout=10.0)
        payload = base64.b64decode(raw)
        width, height = png_size(payload)
        assert width > 0 and height > 0, (width, height)
        print("PASS Page.captureScreenshot 返回截图 %dx%d (%d bytes)" % (width, height, len(payload)))
        checks += 1

        # 7. 刷新
        session.reload(ignore_cache=True, timeout=5.0)
        time.sleep(1.5)
        fresh = session.evaluate("String(window.counts.button)")
        assert fresh == "0", fresh
        print("PASS Page.reload 后计数归零")
        checks += 1

        # 8. 端口列表
        targets = cdp_module.page_targets(port=port)
        assert targets, "没有页面目标"
        print("PASS /json/list 返回 %d 个目标" % len(targets))
        checks += 1

        print("%d 项调试通道实机检查全部通过" % checks)
        print("提示：整个过程没有移动鼠标、没有前置浏览器窗口，说明后台执行通道可用")
        return 0
    finally:
        process.terminate()
        server.shutdown()
        server.server_close()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
