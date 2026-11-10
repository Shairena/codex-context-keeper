"""Connection defaults and queue/dispatch gates; no real compaction."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from compaction import CompactionQueue
from gui import Application
from test_reliability import fixture


class CompactionDefaultsTests(unittest.TestCase):
    def test_connect_and_reconnect_leave_both_permissions_off_and_queue_cannot_dispatch(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            with source.open('ab') as stream:
                stream.write((json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'origin'}}) + '\n').encode())
            store.update(tid, allow_compaction=True, unsafe_native_compaction=True)
            self.assertEqual(store.connect_selected([tid])['connected'], [tid])
            row = store.registry()['chats'][tid]
            self.assertFalse(row['allow_compaction'])
            self.assertFalse(row['unsafe_native_compaction'])
            self.assertTrue(row['auto_restore'])
            self.assertTrue(row['context_warning_enabled'])
            driver = Mock(unsafe_driver=True)
            queue = CompactionQueue(store, driver)
            with self.assertRaises(ValueError):
                queue.request(tid, 'origin', tid)
            self.assertFalse(queue.path.exists())
            store.update(tid, allow_compaction=True)
            request = queue.request(tid, 'origin', tid)
            queue.tick()
            self.assertEqual(queue.status(request['request_id'], tid)['status'], 'deferred')
            driver.inspect.assert_not_called()
            driver.run_native.assert_not_called()
            # Reconnection applies off again, even after explicit previous opt-in.
            store.update(tid, unsafe_native_compaction=True)
            store.connect_selected([tid])
            self.assertFalse(store.registry()['chats'][tid]['allow_compaction'])
            self.assertFalse(store.registry()['chats'][tid]['unsafe_native_compaction'])

    def test_per_chat_opt_in_has_simple_confirmation_and_cannot_change_other_gate(self):
        tid = 'selected'
        app = Mock()
        app.selected.return_value = tid
        app.store.registry.return_value = {'chats': {tid: {'title': 'Synthetic', 'allow_compaction': False, 'unsafe_native_compaction': False}}}
        with patch('gui.messagebox.askyesno', return_value=False):
            Application.toggle_compaction(app)
            Application.toggle_unsafe(app)
        app.store.update.assert_not_called()
        with patch('gui.messagebox.askyesno', return_value=True) as ask:
            Application.toggle_compaction(app)
            app.store.update.assert_called_once_with(tid, allow_compaction=True)
            app.store.update.reset_mock()
            Application.toggle_unsafe(app)
            app.store.update.assert_called_once_with(tid, unsafe_native_compaction=True)
            for call in ask.call_args_list:
                self.assertNotIn('НЕБЕЗОПАС', call.args[1])
                self.assertNotIn('риск', call.args[1])


if __name__ == '__main__':
    unittest.main()
