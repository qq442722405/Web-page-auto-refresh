"""Dependency-free regression tests; run with unittest discovery."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from background_click import OperationGate

tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8-sig'))
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')


def method(name, **env):
    node = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), env)
    return env[name]


class Tests(unittest.TestCase):
    def test_gate_reentry_and_cancel(self):
        gate = OperationGate()
        old = gate.begin('click')
        self.assertIsNone(gate.begin('click'))
        gate.cancel()
        new = gate.begin('click')
        self.assertFalse(gate.finish(old))
        self.assertEqual(gate.pending, new)
        self.assertTrue(gate.finish(new))
        self.assertFalse(gate.finish(new))

    def test_mode_dispatch(self):
        for mode, expected in [('click', 'perform_foreground_point_click'),
                               ('background_click', 'perform_background_point_click'),
                               ('refresh', 'refresh_page')]:
            obj = Mock(operation_gate=OperationGate(), picker_action=None, web_loading=False)
            obj.operation_action_combo.currentData.return_value = mode
            method('perform_scheduled_operation')(obj)
            getattr(obj, expected).assert_called_once()
            if mode == 'background_click':
                obj.perform_foreground_point_click.assert_not_called()

    def test_dispatch_loading_pending_picker(self):
        for state in ['loading', 'pending', 'picker']:
            obj = Mock(operation_gate=OperationGate(), web_loading=state == 'loading',
                       picker_action='background_click' if state == 'picker' else None)
            if state == 'pending':
                obj.operation_gate.begin('click')
            method('perform_scheduled_operation')(obj)
            obj.operation_action_combo.currentData.assert_not_called()

    def test_both_modes_save_same_point_without_clicking(self):
        for mode in ['click', 'background_click']:
            obj = Mock(picker_action=mode, config={})
            obj.operation_action_combo.currentData.return_value = mode
            obj.webview.rect().contains.return_value = True
            point = Mock()
            point.x.return_value, point.y.return_value = 10, 20
            method('on_point_selected')(obj, point)
            self.assertEqual(obj.click_point, [10, 20])
            self.assertEqual(obj.config['click_point_space'], 'webview_local')
            self.assertIsNone(obj.picker_action)
            obj.save_settings.assert_called_once()
            obj.operation_action_combo.setCurrentIndex.assert_not_called()
            obj.perform_background_point_click.assert_not_called()
            obj.perform_foreground_point_click.assert_not_called()

    def test_cancelled_or_outside_pick_preserves_point(self):
        for action, inside in [(None, True), ('background_click', False)]:
            obj = Mock(picker_action=action, click_point=[10, 20], config={})
            obj.operation_action_combo.currentData.return_value = 'background_click'
            obj.webview.rect().contains.return_value = inside
            method('on_point_selected')(obj, Mock())
            self.assertEqual(obj.click_point, [10, 20])
            obj.save_settings.assert_not_called()

    def test_background_uses_point_and_releases_gate(self):
        obj = Mock(web_loading=False, _quitting=False, click_point=[10, 20],
                   operation_gate=OperationGate())
        point, send = Mock(), Mock()
        method('perform_background_point_click', QPoint=point, send_background_click=send)(obj)
        point.assert_called_once_with(10, 20)
        send.assert_called_once_with(obj.webview, point.return_value)
        self.assertIsNone(obj.operation_gate.pending)
        obj._native_click_screen.assert_not_called()

    def test_background_failure_stops_and_never_moves_mouse(self):
        obj = Mock(web_loading=False, _quitting=False, click_point=[10, 20],
                   operation_gate=OperationGate())
        send = Mock(side_effect=ValueError('outside'))
        method('perform_background_point_click', QPoint=Mock(), send_background_click=send)(obj)
        self.assertIsNone(obj.operation_gate.pending)
        obj.auto_operation_cb.setChecked.assert_called_once_with(False)
        obj._native_click_screen.assert_not_called()
        obj.perform_foreground_point_click.assert_not_called()

    def test_missing_point_or_loading_never_sends(self):
        for point, loading in [([], False), ([10, 20], True)]:
            obj = Mock(web_loading=loading, _quitting=False, click_point=point,
                       operation_gate=OperationGate())
            send = Mock()
            method('perform_background_point_click', send_background_click=send)(obj)
            send.assert_not_called()

    def test_mode_change_stops_automatic(self):
        obj = Mock()
        obj.auto_operation_cb.isChecked.return_value = True
        method('on_operation_mode_changed')(obj)
        obj.cancel_operation.assert_called_once()
        obj.auto_operation_cb.setChecked.assert_called_once_with(False)


if __name__ == '__main__':
    unittest.main()
