"""Opt-in real Chromium tests; uses installed Edge, never downloads a browser.
uv run --with playwright python tests/test_browser.py
Not named unittest.TestCase: normal unit discovery has no external dependencies.
"""
import functools
import http.server
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from background_click import make_script

def main():
    from playwright.sync_api import sync_playwright
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(ROOT / 'tests')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    passed = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1000, 'height': 700})
            page.goto(f'http://127.0.0.1:{server.server_port}/fixture.html')
            page.wait_for_function("document.querySelector('#same').contentDocument.querySelector('#inner')")
            def capture(selector, zoom=1, inner=False):
                box = page.locator(selector).bounding_box()
                x, y = box['x'] + box['width']/2, box['y'] + box['height']/2
                if inner:
                    child = page.frame_locator(selector).locator('#inner').bounding_box()
                    x, y = child['x']+child['width']/2, child['y']+child['height']/2
                return page.evaluate(make_script('capture', point={'x': x*zoom, 'y': y*zoom,
                                             'zoom': zoom, 'width': 1000*zoom}))
            def click(target): return page.evaluate(make_script('click', target=target))
            def check(name, value):
                assert value, name
                passed.append(name)
                print('PASS', name)
            target = capture('#nested')['target']
            check('nested single activation', click(target)['ok'] and page.evaluate('counts.button') == 1)
            target = capture('#link')['target']
            check('link single activation', click(target)['ok'] and page.evaluate('counts.link') == 1)
            check('disabled rejected', not capture('#disabled')['ok'])
            target = capture('#lost')['target']
            page.locator('#lost').evaluate('(el)=>el.remove()')
            check('missing target rejected', not click(target)['ok'])
            target = capture('#nested')['target']
            page.locator('#button').evaluate('(el)=>el.disabled=true')
            check('disabled after capture rejected', not click(target)['ok'])
            page.locator('#button').evaluate('(el)=>el.disabled=false')
            page.locator('#nested').evaluate('(el)=>el.textContent="changed"')
            check('changed target rejected', not click(target)['ok'])
            check('canvas rejected', not capture('#canvas')['ok'])
            check('cross origin frame rejected', not capture('#cross')['ok'])
            target = capture('#same', inner=True)['target']
            check('same origin frame once', click(target)['ok'] and page.frames[1].evaluate('window.count') == 1)
            for zoom in [0.75, 1.25, 2]:
                check(f'coordinate conversion {zoom}', capture('#nested', zoom)['ok'])
            check('expired request rejected', not page.evaluate(make_script('click', target=target, now=time.time()-10))['ok'])
            page.locator('#scrolled').scroll_into_view_if_needed()
            target = capture('#scrolled')['target']
            check('scrolled page once', click(target)['ok'] and page.evaluate('counts.scrolled') == 1)
            page.evaluate('scrollTo(0,0)')
            check('offscreen target DOM activation', click(target)['ok'] and page.evaluate('counts.scrolled') == 2)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(f'{len(passed)} browser checks passed (Edge headless, not Qt GUI).')

if __name__ == '__main__': main()
