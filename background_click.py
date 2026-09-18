"""DOM-only background actions. Embedded source is collected by PyInstaller imports.

Qt widget positions are device-independent pixels; WebEngine zoomFactor converts
these to CSS pixels. DevicePixelRatio must NOT be applied a second time.
"""
import json
import time

TIMEOUT_MS = 5000


class OperationGate:
    """One pending operation, with generation-safe completion and cancellation."""
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


def valid_target(target):
    """Reject partial/legacy configuration before it reaches UI or JavaScript."""
    def locator(value):
        return (isinstance(value, dict) and isinstance(value.get('selector'), str)
                and isinstance(value.get('fingerprint'), dict)
                and isinstance(value['fingerprint'].get('tag'), str))
    return (isinstance(target, dict) and target.get('version') == 1
            and isinstance(target.get('url'), str)
            and locator(target.get('locator'))
            and isinstance(target.get('frames'), list) and len(target['frames']) <= 16
            and all(isinstance(frame, dict) and locator(frame.get('locator'))
                    and isinstance(frame.get('url'), str) for frame in target['frames']))


DOM_SCRIPT = r"""
(function(request) {
    'use strict';
    function fail(reason) { throw new Error(reason); }
    function url(doc) { return doc.location.href.split('#')[0]; }
    function text(el) { return String(el.textContent || '').trim().replace(/\s+/g, ' ').slice(0,120); }
    function fingerprint(el) {
        return {tag: el.localName, id: el.id || '', text: text(el),
                name: el.getAttribute('name') || '', type: el.getAttribute('type') || '',
                href: el.getAttribute('href') || '', role: el.getAttribute('role') || '',
                src: el.getAttribute('src') || '', aria: el.getAttribute('aria-label') || ''};
    }
    function same(el, expected) { return JSON.stringify(fingerprint(el)) === JSON.stringify(expected); }
    function locate(el) {
        const doc = el.ownerDocument;
        let selector = '';
        if (el.id) {
            const candidate = '#' + CSS.escape(el.id);
            if (doc.querySelectorAll(candidate).length === 1) selector = candidate;
        }
        const parts = [], xpath = [];
        for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
            let index = 1;
            for (let prev = node.previousElementSibling; prev; prev = prev.previousElementSibling)
                if (prev.localName === node.localName) index++;
            parts.unshift(node.localName + ':nth-of-type(' + index + ')');
            xpath.unshift(node.localName + '[' + index + ']');
        }
        if (!selector) selector = parts.join(' > ');
        if (doc.querySelectorAll(selector).length !== 1) fail('目标选择器不唯一');
        return {selector: selector, xpath: '/' + xpath.join('/'), fingerprint: fingerprint(el)};
    }
    function resolve(doc, locator) {
        if (!locator || !locator.fingerprint) fail('目标数据无效，请重新拾取');
        let list = [];
        try { list = doc.querySelectorAll(locator.selector); } catch (_) {}
        if (list.length > 1) fail('目标不唯一，请重新拾取');
        let el = list.length === 1 ? list[0] : null;
        if (!el && locator.xpath) {
            const found = doc.evaluate(locator.xpath, doc, null, 7, null);
            if (found.snapshotLength !== 1) fail('目标已丢失或不唯一');
            el = found.snapshotItem(0);
        }
        if (!el || !same(el, locator.fingerprint)) fail('目标已丢失或内容发生变化，请重新拾取');
        return el;
    }
    function supported(el) {
        if (!el || el.nodeType !== 1) fail('找不到网页元素');
        if (el.getRootNode() !== el.ownerDocument) fail('暂不支持 Shadow DOM 目标');
        if (['canvas', 'iframe', 'frame', 'html', 'body', 'svg'].includes(el.localName))
            fail('不支持此目标（canvas、跨域框架或非交互页面区域）');
        if (typeof el.click !== 'function') fail('目标不支持 DOM click');
        if (el.matches(':disabled') || el.closest('[inert], [aria-disabled="true"]')) fail('目标已禁用');
        const style = el.ownerDocument.defaultView.getComputedStyle(el);
        if (style.visibility !== 'visible' || style.display === 'none' || !el.getClientRects().length)
            fail('目标不可见');
        for (let node = el; node; node = node.parentElement) {
            const s = el.ownerDocument.defaultView.getComputedStyle(node);
            if (Number(s.opacity) === 0 || s.pointerEvents === 'none') fail('目标不可交互');
        }
    }
    function frameDocument(frame) {
        try {
            const doc = frame.contentDocument;
            if (!doc || !doc.documentElement) fail('跨域或未加载 iframe 不支持');
            void doc.location.href;
            return doc;
        } catch (_) { fail('跨域或未加载 iframe 不支持'); }
    }
    function untransformed(frame) {
        for (let n = frame; n; n = n.parentElement) {
            const s = n.ownerDocument.defaultView.getComputedStyle(n);
            if (s.transform !== 'none' || (s.zoom && !['1', 'normal'].includes(s.zoom)))
                fail('暂不支持带 CSS transform/zoom 的 iframe，请移除变换后拾取');
        }
    }
    try {
        if (Date.now() > request.deadline) fail('请求已过期，未点击');
        if (window.visualViewport && (Math.abs(window.visualViewport.scale - 1) > 0.001 ||
            window.visualViewport.offsetLeft || window.visualViewport.offsetTop)) fail('暂不支持捏合视口缩放');
        if (request.kind === 'capture') {
            const p = request.point;
            if (!(p.zoom > 0) || Math.abs(innerWidth * p.zoom - p.width) > Math.max(4, p.zoom * 2))
                fail('网页视口正在变化，请稳定窗口后重新拾取');
            let x = p.x / p.zoom, y = p.y / p.zoom;
            let doc = document, el = null;
            const frames = [];
            for (let depth = 0; depth < 16; depth++) {
                el = doc.elementFromPoint(x, y);
                if (!el) fail('拾取点不在网页内容内');
                if (!['iframe', 'frame'].includes(el.localName)) break;
                untransformed(el);
                const child = frameDocument(el), rect = el.getBoundingClientRect();
                frames.push({locator: locate(el), url: url(child)});
                x -= rect.left + el.clientLeft;
                y -= rect.top + el.clientTop;
                doc = child;
            }
            if (el && el.shadowRoot) fail('暂不支持 Shadow DOM 目标');
            if (el && el.localName === 'canvas') fail('canvas 精细坐标操作不支持');
            const actionable = el && el.closest('button,a,input,select,textarea,summary,[role="button"],[role="link"],[onclick],[data-action]');
            if (actionable) el = actionable;
            supported(el);
            return {ok:true, target:{version:1, url:url(document), frames:frames, locator:locate(el)}};
        }
        const target = request.target;
        if (!target || target.version !== 1 || target.url !== url(document)) fail('页面与拾取时不同，请重新拾取');
        let doc = document;
        for (const step of target.frames) {
            const frame = resolve(doc, step.locator);
            if (!['iframe','frame'].includes(frame.localName)) fail('框架目标无效');
            doc = frameDocument(frame);
            if (url(doc) !== step.url) fail('iframe 已导航，请重新拾取');
        }
        const el = resolve(doc, target.locator);
        supported(el);
        if (Date.now() > request.deadline) fail('请求已过期，未点击');
        // Exactly ONE activation, never synthesize a second click or move/focus the OS mouse.
        el.click();
        return {ok:true, reason:'已触发点击（不代表网页业务成功）'};
    } catch (error) { return {ok:false, reason:String(error.message || error)}; }
})(__REQUEST__)
"""


def make_script(kind, *, target=None, point=None, now=None):
    if kind not in ('capture', 'click'):
        raise ValueError('Unknown DOM operation')
    request = {'kind': kind, 'target': target, 'point': point,
               'deadline': int((time.time() if now is None else now) * 1000) + TIMEOUT_MS}
    return DOM_SCRIPT.replace('__REQUEST__', json.dumps(request, ensure_ascii=True))
