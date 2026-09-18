"""Dependency-free tests: python -m unittest discover -s tests -v."""
import ast
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from background_click import OperationGate, make_script, valid_target

# Execute actual MainWindow methods without importing Qt/OCR or opening a desktop.
tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8-sig'))
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')

def method(name):
    node = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    env = {}
    exec(compile(module, 'main.py', 'exec'), env)
    return env[name]

class Tests(unittest.TestCase):
    def test_gate_reentry(self):
        gate = OperationGate()
        ticket = gate.begin('click')
        self.assertIsNone(gate.begin('capture'))
        self.assertTrue(gate.finish(ticket))
        self.assertFalse(gate.finish(ticket))

    def test_gate_cancel_stale(self):
        gate = OperationGate()
        old = gate.begin('click')
        gate.cancel()
        new = gate.begin('click')
        self.assertFalse(gate.finish(old))
        self.assertEqual(gate.pending, new)
        self.assertTrue(gate.finish(new))

    def test_request(self):
        script = make_script('click', target={'text': '\");alert(1);//'}, now=1)
        request = json.loads(script.rsplit('})(', 1)[1].rstrip().removesuffix(')'))
        self.assertEqual(request['deadline'], 6000)
        self.assertEqual(script.count('el.click();'), 1)
        with self.assertRaises(ValueError):
            make_script('native')

    def test_target_validation(self):
        for target in [None, [], {}, {'version': 1}, {'version': 1, 'locator': None}]:
            self.assertFalse(valid_target(target))
        target = {'version': 1, 'url': 'http://local', 'frames': [],
                  'locator': {'selector': '#b', 'fingerprint': {'tag': 'button'}}}
        self.assertTrue(valid_target(json.loads(json.dumps(target))))

    def test_mode_dispatch(self):
        for mode, expected in [('click', 'perform_foreground_point_click'),
                               ('background_click', 'perform_background_point_click'),
                               ('refresh', 'refresh_page')]:
            obj = Mock()
            obj.operation_gate = OperationGate()
            obj.picker_action = None
            obj.web_loading = False
            obj.operation_action_combo.currentData.return_value = mode
            method('perform_scheduled_operation')(obj)
            getattr(obj, expected).assert_called_once()
            if mode == 'background_click':
                obj.perform_foreground_point_click.assert_not_called()

    def test_dispatch_loading_pending_picker(self):
        for state in ['loading', 'pending', 'picker']:
            obj = Mock()
            obj.operation_gate = OperationGate()
            obj.web_loading = state == 'loading'
            obj.picker_action = 'background_click' if state == 'picker' else None
            if state == 'pending': obj.operation_gate.begin('click')
            method('perform_scheduled_operation')(obj)
            obj.operation_action_combo.currentData.assert_not_called()

    def test_capture_keeps_mode(self):
        obj = Mock()
        obj.picker_action = 'background_click'
        obj.operation_action_combo.currentData.return_value = 'background_click'
        point = Mock()
        point.x.return_value, point.y.return_value = 10, 20
        method('on_point_selected')(obj, point)
        obj._capture_background_click_target.assert_called_once_with(10, 20)
        obj.operation_action_combo.setCurrentIndex.assert_not_called()
        obj.save_settings.assert_not_called()

    def test_stale_callback_ignored(self):
        obj = Mock()
        obj._quitting = False
        obj.operation_gate = OperationGate()
        ticket = obj.operation_gate.begin('capture')
        obj.operation_gate.cancel()
        method('on_dom_result')(obj, ticket, {'ok': True})
        obj.save_settings.assert_not_called()
        obj.dom_timeout.stop.assert_not_called()

    def test_timeout_stops_automatic(self):
        obj = Mock()
        method('on_dom_timeout')(obj)
        obj.cancel_dom_operation.assert_called_once()
        obj.auto_operation_cb.setChecked.assert_called_once_with(False)

    def test_mode_change_stops_automatic(self):
        obj = Mock()
        obj.auto_operation_cb.isChecked.return_value = True
        method('on_operation_mode_changed')(obj)
        obj.cancel_dom_operation.assert_called_once()
        obj.auto_operation_cb.setChecked.assert_called_once_with(False)

    def test_no_legacy_target_fallback(self):
        obj = Mock()
        obj.background_target = None
        method('perform_background_point_click')(obj)
        obj.run_dom_operation.assert_not_called()
        obj.perform_foreground_point_click.assert_not_called()

if __name__ == '__main__': unittest.main()
