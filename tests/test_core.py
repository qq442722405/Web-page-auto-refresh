# -*- coding: utf-8 -*-
"""无需 GUI/OCR 依赖的逻辑回归测试。

    python -m unittest discover -s tests -v
"""
import ast
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webmonitor.alarm import AlarmTracker, build_rule, same_runs  # noqa: E402
from webmonitor.config import clamp_float, clamp_int, load, normalize_roi, remember  # noqa: E402
from webmonitor.coords import clamp_rect_values, css_clip, css_point, valid_zoom  # noqa: E402
from webmonitor.gate import OperationGate  # noqa: E402
from webmonitor import cdp as cdp_module  # noqa: E402


# ============================================================ 操作闸门 ====
class OperationGateTests(unittest.TestCase):
    def test_reentry_and_cancel(self):
        gate = OperationGate()
        old = gate.begin("click")
        self.assertIsNone(gate.begin("click"))       # 在途期间禁止并发
        self.assertTrue(gate.busy)
        gate.cancel()
        new = gate.begin("click")
        self.assertFalse(gate.finish(old))           # 旧票作废
        self.assertTrue(gate.finish(new))
        self.assertFalse(gate.finish(new))           # 重复完成无效
        self.assertFalse(gate.busy)

    def test_pending_kind(self):
        gate = OperationGate()
        self.assertIsNone(gate.pending_kind)
        ticket = gate.begin("background_click")
        self.assertEqual(gate.pending_kind, "background_click")
        gate.finish(ticket)
        self.assertIsNone(gate.pending_kind)


# ================================================================ 报警 ====
class AlarmRuleTests(unittest.TestCase):
    def test_same_runs(self):
        self.assertEqual(same_runs(["1", "1", "1", "2"], 3), {(0, ("1", "1", "1"))})
        self.assertEqual(len(same_runs(["1", "1", "1"], 2)), 2)
        self.assertEqual(same_runs([], 3), set())
        self.assertEqual(same_runs(["1", "2"], 3), set())

    def test_default_rule_alarm(self):
        tracker = AlarmTracker()
        rule = build_rule("", 3)
        self.assertTrue(tracker.evaluate(1, ["5", "5", "5"], rule))
        self.assertTrue(tracker.is_alarming(1))

    def test_ack_stops_duplicate_but_new_rows_alarm(self):
        tracker = AlarmTracker()
        rule = build_rule("", 2)
        tracker.evaluate(1, ["7", "7"], rule)
        tracker.acknowledge(1, rule)
        self.assertFalse(tracker.is_alarming(1))
        # 相同内容重复出现不再报警
        self.assertFalse(tracker.evaluate(1, ["7", "7"], rule))
        # 表格新增一行带来新组合 -> 仍然报警
        self.assertTrue(tracker.evaluate(1, ["7", "7", "7"], rule))

    def test_target_value_rule(self):
        tracker = AlarmTracker()
        rule = build_rule("0.193", 2)
        self.assertFalse(tracker.evaluate(1, ["0.193"], rule))
        self.assertTrue(tracker.evaluate(1, ["0.193", "0.193"], rule))
        tracker.acknowledge(1, rule)
        self.assertFalse(tracker.evaluate(1, ["0.193", "0.193"], rule))
        self.assertTrue(tracker.evaluate(1, ["0.193", "0.193", "0.193"], rule))

    def test_acknowledge_all_and_reset(self):
        tracker = AlarmTracker()
        rule = build_rule("", 2)
        tracker.evaluate(1, ["3", "3"], rule)
        tracker.evaluate(2, ["4", "4"], rule)
        tracker.acknowledge_all(2, rule)
        self.assertEqual(tracker.active_boxes(), [])
        tracker.reset()
        self.assertEqual(tracker.latest_digits(1), [])

    def test_forget_box(self):
        tracker = AlarmTracker()
        tracker.evaluate(1, ["9", "9"], build_rule("", 2))
        tracker.forget_box(1)
        self.assertEqual(tracker.active_boxes(), [])
        self.assertEqual(tracker.latest_digits(1), [])


# ================================================================ 坐标 ====
class CoordTests(unittest.TestCase):
    def test_zoom_guard(self):
        self.assertEqual(valid_zoom(0), 1.0)
        self.assertEqual(valid_zoom(None), 1.0)
        self.assertEqual(valid_zoom(-3), 1.0)
        self.assertEqual(valid_zoom(2.0), 2.0)

    def test_css_conversion(self):
        self.assertEqual(css_point(100, 50, 2.0), (50.0, 25.0))
        clip = css_clip(100, 50, 200, 80, 2.0)
        self.assertEqual((clip["x"], clip["y"], clip["width"], clip["height"]),
                         (50.0, 25.0, 100.0, 40.0))

    def test_clamp_inside_viewport(self):
        self.assertEqual(clamp_rect_values(-5, -5, 50, 50, 300, 200), (0, 0, 50, 50))
        self.assertEqual(clamp_rect_values(280, 180, 100, 100, 300, 200), (280, 180, 20, 20))
        # 宽高最小为 1，避免空区域
        self.assertEqual(clamp_rect_values(300, 200, 0, 0, 300, 200), (299, 199, 1, 1))


# ================================================================ 配置 ====
class ConfigTests(unittest.TestCase):
    def test_clamps(self):
        self.assertEqual(clamp_int("abc", 1, 10, 5), 5)
        self.assertEqual(clamp_int(999, 1, 10, 5), 10)
        self.assertEqual(clamp_float(0.1, 0.25, 3.0, 1.0), 0.25)
        self.assertEqual(normalize_roi([0, 0, -1, 10], [1, 1, 1, 1]), [1, 1, 1, 1])

    def test_load_defaults_and_save(self):
        import tempfile
        from webmonitor import config as config_module

        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, "auto_login_config.json")
            with patch.object(config_module, "CONFIG_FILE", config_file):
                cfg = load()
                self.assertEqual(cfg["operation_action"], "refresh")
                self.assertEqual(cfg["roi_space"], "webview_local")
                remember(cfg, target_value="0.5", background_strategy="cdp")
                again = load()
                self.assertEqual(again["target_value"], "0.5")
                self.assertEqual(again["background_strategy"], "cdp")

    def test_corrupt_file_falls_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, "broken.json")
            with open(config_file, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            from webmonitor import config as config_module
            with patch.object(config_module, "CONFIG_FILE", config_file):
                cfg = load()
                self.assertEqual(cfg["operation_interval"], 60)

    def test_stale_action_and_strategy_normalized(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, "cfg.json")
            with open(config_file, "w", encoding="utf-8") as handle:
                json.dump({"operation_action": "obsolete", "background_strategy": "?",
                           "click_point": "bad", "roi_list": [[0, 0, 0, 0]]},
                          handle)
            from webmonitor import config as config_module
            with patch.object(config_module, "CONFIG_FILE", config_file):
                cfg = load()
                self.assertEqual(cfg["operation_action"], "refresh")
                self.assertEqual(cfg["background_strategy"], "auto")
                self.assertEqual(cfg["click_point"], [])
                self.assertEqual(cfg["roi_list"], [])


# ================================================================ CDP ====
class FrameTests(unittest.TestCase):
    @staticmethod
    def _frame(payload, opcode=0x1, fin=True, masked=False):
        header = bytearray([(0x80 if fin else 0x00) | opcode])
        length = len(payload)
        if length < 126:
            header.append((0x80 if masked else 0x00) | length)
        elif length < (1 << 16):
            header.append((0x80 if masked else 0x00) | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append((0x80 if masked else 0x00) | 127)
            header += length.to_bytes(8, "big")
        if masked:
            mask = b"\x01\x02\x03\x04"
            header += mask
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return bytes(header) + payload

    def test_recv_text_handles_fragments_and_ping(self):
        ws = cdp_module.WebSocket("ws://127.0.0.1:1/x")
        part1 = json.dumps({"a": 1}).encode()[:4]
        part2 = json.dumps({"a": 1}).encode()[4:]
        data = (self._frame(part1, fin=False) + self._frame(b"ping", opcode=0x9)
                + self._frame(part2, opcode=0x0))
        ws._buffer = data
        ws.sock = Mock()
        self.assertEqual(ws.recv_text(), '{"a": 1}')

    def test_send_text_is_masked(self):
        ws = cdp_module.WebSocket("ws://127.0.0.1:1/x")
        sent = []
        ws.sock = Mock()
        ws.sock.sendall.side_effect = lambda data: sent.append(data)
        ws.send_text("hi")
        frame = sent[0]
        self.assertEqual(frame[0] & 0x0F, 0x1)
        self.assertTrue(frame[1] & 0x80)          # 客户端必须掩码
        self.assertEqual(frame[1] & 0x7F, 2)


class CdpSessionTests(unittest.TestCase):
    def _session_with_responses(self, responses):
        session = cdp_module.CdpSession("ws://127.0.0.1:1/x")
        queue = list(responses)

        class FakeWS:
            connected = True
            sent = []

            def send_text(self, text):
                FakeWS.sent.append(json.loads(text))

            def recv_text(self, timeout=None):
                if not queue:
                    raise AssertionError("没有待返回的消息")
                return queue.pop(0)

            def close(self):
                pass

        session.ws = FakeWS()
        return session, FakeWS

    def test_call_matches_id_and_skips_events(self):
        session, fake = self._session_with_responses([
            json.dumps({"method": "Page.loadEventFired", "params": {}}),
            json.dumps({"id": 1, "result": {"ok": True}}),
        ])
        self.assertEqual(session.call("Page.reload", {}), {"ok": True})
        self.assertEqual(fake.sent[0]["method"], "Page.reload")

    def test_call_raises_on_protocol_error(self):
        session, _ = self._session_with_responses([
            json.dumps({"id": 1, "error": {"code": -32000, "message": "boom"}}),
        ])
        with self.assertRaises(cdp_module.CDPError) as ctx:
            session.call("Input.dispatchMouseEvent", {})
        self.assertIn("boom", str(ctx.exception))

    def test_dispatch_click_sequence(self):
        session, fake = self._session_with_responses([
            json.dumps({"id": i, "result": {}}) for i in range(1, 4)
        ])
        session.dispatch_click(10.0, 20.0)
        kinds = [item["params"]["type"] for item in fake.sent]
        self.assertEqual(kinds, ["mouseMoved", "mousePressed", "mouseReleased"])
        self.assertEqual(fake.sent[0]["params"]["x"], 10.0)
        self.assertEqual(fake.sent[-1]["params"]["buttons"], 0)


# ==================================================== 通道编排（纯逻辑） ==
def _controller_module():
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - 无 GUI 环境
        raise unittest.SkipTest("需要 PySide6 才能测试通道编排: %s" % exc)
    QApplication.instance() or QApplication([])
    from webmonitor import page_controller
    return page_controller


def _point(x, y):
    point = Mock()
    point.x.return_value = x
    point.y.return_value = y
    return point


class ChannelOrderTests(unittest.TestCase):
    def _controller(self, **kwargs):
        page_controller = _controller_module()
        controller = page_controller.PageController(Mock())
        controller.configure(**kwargs)
        return controller

    def test_auto_order(self):
        controller = self._controller(cdp_enabled=True, cdp_port=9333, strategy="auto",
                                      allow_temporary_foreground=True)
        self.assertEqual(controller._click_channels(), ["cdp", "qt", "native"])

    def test_auto_without_temporary_foreground(self):
        controller = self._controller(cdp_enabled=True, cdp_port=9333, strategy="auto",
                                      allow_temporary_foreground=False)
        self.assertEqual(controller._click_channels(), ["cdp", "qt"])

    def test_single_strategy(self):
        controller = self._controller(cdp_enabled=True, cdp_port=9333, strategy="qt")
        self.assertEqual(controller._click_channels(), ["qt"])
        # 用户显式指定“仅真实点击”时，兜底开关不影响该通道
        controller.configure(strategy="native", allow_temporary_foreground=False)
        self.assertEqual(controller._click_channels(), ["native"])

    def test_cdp_disabled_falls_back(self):
        controller = self._controller(cdp_enabled=False, strategy="auto",
                                      allow_temporary_foreground=True)
        self.assertEqual(controller._click_channels(), ["qt", "native"])

    def test_click_reports_all_failures(self):
        page_controller = _controller_module()
        controller = page_controller.PageController(Mock())
        controller.configure(cdp_enabled=False, strategy="qt", allow_temporary_foreground=False)
        attempts = []

        def fail(name, x, y):
            attempts.append(name)
            raise page_controller.OperationError("模拟失败 %s" % name)

        controller._dispatch_click = fail
        with self.assertRaises(page_controller.OperationError) as ctx:
            controller.click(_point(1, 2))
        self.assertEqual(attempts, ["qt"])
        self.assertIn("模拟失败", str(ctx.exception))

    def test_no_channel(self):
        controller = self._controller(cdp_enabled=False, strategy="cdp",
                                      allow_temporary_foreground=False)
        self.assertEqual(controller._click_channels(), [])


# ==================================================== 源码结构与规范 ====
class SourceHygieneTests(unittest.TestCase):
    def test_window_methods_exist(self):
        """主窗口必须提供这些入口，避免重构时漏方法（静态检查，不启动 GUI）。"""
        tree = ast.parse((ROOT / "webmonitor" / "window.py").read_text(encoding="utf-8"))
        klass = next(node for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        names = {node.name for node in klass.body if isinstance(node, ast.FunctionDef)}
        required = {"perform_scheduled_operation", "perform_background_point_click",
                    "perform_foreground_point_click", "refresh_page", "save_settings",
                    "toggle_roi_monitor", "perform_roi_ocr_check", "on_point_selected",
                    "start_point_picker", "quit_app", "run_channel_self_test"}
        self.assertTrue(required.issubset(names), sorted(required - names))

    def test_no_module_level_task_side_effects(self):
        """除日志/崩溃钩子外，模块导入不应启动线程或网络服务。"""
        watch = ["screenshot_service.py", "cdp.py", "page_controller.py"]
        for name in watch:
            tree = ast.parse((ROOT / "webmonitor" / name).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    func = getattr(node.value.func, "id", None)
                    self.assertNotIn(func, {"serve_forever", "start", "connect"})


if __name__ == "__main__":
    unittest.main()
