"""Counter and reminder contracts with fake owner observations; never native send."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from context_warnings import ContextWarnings, counter_text, load_warning_state, observe_context, observation
from core import file_lease, write_json
from management_mcp import call_management
from test_reliability import fixture


def high(tid, **changes):
    return {'disposition': 'observed', 'threadId': tid, 'ownerId': 'owner', 'idle': True,
        'usedTokens': 900, 'contextWindow': 1000, 'contextKey': 'context-1', **changes}


class WarningTests(unittest.TestCase):
    def test_reminder_once_across_restart_rearms_after_context_change_and_never_compacts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            store.configure_context_warnings([tid], True, 90)
            driver = Mock()
            driver.observe.return_value = high(tid)
            driver.remind.return_value = {'disposition': 'accepted', 'threadId': tid, 'turnId': 'warning-turn'}
            monitor = ContextWarnings(store, driver)
            monitor.tick()
            driver.remind.assert_called_once_with(tid, 'owner', 90, 'context-1')
            ContextWarnings(store, driver).tick()
            driver.remind.assert_called_once()
            # The alert's own answer and a small usage drop do not rearm it.
            driver.observe.return_value = high(tid, usedTokens=880)
            monitor.tick()
            driver.observe.return_value = high(tid, usedTokens=920)
            monitor.tick()
            driver.remind.assert_called_once()
            driver.observe.return_value = high(tid, contextKey='context-2')
            monitor.tick()
            self.assertEqual(driver.remind.call_count, 2)
            self.assertEqual(load_warning_state(store.root)['chats'][tid]['status'], 'sent')
            driver.run_native.assert_not_called()
            self.assertFalse((store.root / 'compaction-requests.json').exists())

    def test_active_missing_wrong_below_disabled_and_stopped_never_send(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            store.configure_context_warnings([tid], True, 90)
            driver = Mock()
            for value in (high(tid, idle=False), high(tid, usedTokens=None), high(tid, contextWindow=0),
                    high(tid, usedTokens=899), high(str(uuid.uuid4())), {'disposition': 'not_started'}):
                driver.observe.return_value = value
                ContextWarnings(store, driver).tick()
            driver.remind.assert_not_called()
            store.configure_context_warnings([tid], False, 90)
            driver.observe.return_value = high(tid)
            ContextWarnings(store, driver).tick()
            driver.remind.assert_not_called()
            stop = threading.Event()
            stop.set()
            store.configure_context_warnings([tid], True, 90)
            ContextWarnings(store, driver, stop).tick()
            driver.remind.assert_not_called()
            write_json(store.home / 'context-keeper-protected-threads.json', [tid])
            ContextWarnings(store, driver).tick()
            driver.remind.assert_not_called()
            with self.assertRaises(ValueError):
                observe_context(store, tid, driver)

    def test_intent_precedes_send_unknown_result_never_retried_but_guard_decline_retries(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            store.configure_context_warnings([tid], True, 90)
            driver = Mock()
            driver.observe.return_value = high(tid)
            def lose_ack(*args):
                self.assertEqual(load_warning_state(store.root)['chats'][tid]['status'], 'dispatching')
                raise TimeoutError('unknown acceptance')
            driver.remind.side_effect = lose_ack
            ContextWarnings(store, driver).tick()
            ContextWarnings(store, driver).tick()
            driver.remind.assert_called_once()
            self.assertEqual(load_warning_state(store.root)['chats'][tid]['status'], 'ambiguous')
            driver.observe.return_value = high(tid, contextKey='next')
            driver.remind.side_effect = None
            driver.remind.return_value = {'disposition': 'not_started'}
            ContextWarnings(store, driver).tick()
            driver.remind.return_value = {'disposition': 'accepted', 'threadId': tid, 'turnId': 'confirmed'}
            ContextWarnings(store, driver).tick()
            self.assertEqual(driver.remind.call_count, 3)
            # Crash after durable intent is also never blindly resent.
            state = load_warning_state(store.root)
            state['chats'][tid]['status'] = 'dispatching'
            write_json(store.root / 'context-warnings.json', state)
            ContextWarnings(store, driver).tick()
            self.assertEqual(driver.remind.call_count, 3)

    def test_counter_is_fresh_read_only_and_configuration_validates_atomically(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            driver = Mock()
            driver.observe.return_value = high(tid, usedTokens=125, contextWindow=1000)
            before = (store.root / 'registry.json').read_bytes()
            value = observe_context(store, tid, driver)
            self.assertEqual((value['used_tokens'], value['remaining_tokens'], value['percent']), (125, 875, 12.5))
            self.assertEqual((store.root / 'registry.json').read_bytes(), before)
            self.assertFalse((store.root / 'context-warnings.json').exists())
            self.assertIn('12.5%', counter_text(value))
            stale = {**value, 'observed_at': (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()}
            self.assertIn('устарело', counter_text(stale))
            self.assertIn('нет живых данных', counter_text(observation(tid, {})))
            for enabled, percent in ((True, True), (1, 90), (True, 101), (True, 0)):
                with self.assertRaises(ValueError):
                    store.configure_context_warnings([tid], enabled, percent)
            with self.assertRaises(ValueError):
                store.configure_context_warnings([tid, str(uuid.uuid4())], True, 90)
            self.assertEqual((store.root / 'registry.json').read_bytes(), before)
            self.assertEqual(call_management(store, 'configure_context_warnings',
                {'thread_ids': [tid], 'enabled': True, 'percent': 90})['configured'], [tid])
            with patch('management_mcp.observe_context', return_value=value) as live:
                self.assertEqual(call_management(store, 'get_context_status', {'thread_id': tid}), value)
                live.assert_called_once_with(store, tid)

    def test_single_observer_lease_and_disconnected_state_is_pruned(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            driver = Mock()
            with file_lease(store.root, '.context-warning-worker.lock') as acquired:
                self.assertTrue(acquired)
                ContextWarnings(store, driver).tick()
                driver.observe.assert_not_called()
            driver.observe.return_value = high(tid)
            ContextWarnings(store, driver).tick()
            store.disconnect([tid])
            ContextWarnings(store, driver).tick()
            self.assertEqual(load_warning_state(store.root)['chats'], {})

    def test_revoked_after_intent_never_reaches_sender(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            store.configure_context_warnings([tid], True, 90)
            driver = Mock()
            driver.observe.return_value = high(tid)
            revoked = False
            def save_then_revoke(path, state):
                nonlocal revoked
                write_json(path, state)
                if not revoked and state['chats'].get(tid, {}).get('status') == 'dispatching':
                    revoked = True
                    store.configure_context_warnings([tid], False, 90)
            with patch('context_warnings.write_json', side_effect=save_then_revoke):
                ContextWarnings(store, driver).tick()
            driver.remind.assert_not_called()
            self.assertEqual(load_warning_state(store.root)['chats'][tid]['status'], 'not_started')

    def test_observer_runs_under_archive_writer_lease(self):
        from core import lock
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            store.configure_context_warnings([tid], True, 90)
            driver = Mock()
            driver.observe.return_value = high(tid)
            driver.remind.return_value = {'disposition': 'accepted', 'threadId': tid, 'turnId': 'warning'}
            with lock(store.root):
                ContextWarnings(store, driver).tick()
            driver.remind.assert_called_once()


if __name__ == '__main__':
    unittest.main()
