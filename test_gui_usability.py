"""Human-facing status, layout and source selection with synthetic stores only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import json
from unittest.mock import Mock, patch

from core import Store, TranscriptUnavailable, write_json
from gui import Application
from main import gui_home


def codex_home(path):
    path.mkdir()
    with closing(sqlite3.connect(path / 'state_5.sqlite')) as db:
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY)')
        db.commit()
    return path


class GuiUsabilityTests(unittest.TestCase):
    def test_queued_operations_cannot_follow_gui_into_another_source(self):
        from compaction import CompactionQueue
        from test_reliability import fixture
        for source in (None, 'different'):
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
                store, tid, _rollout = fixture(Path(temporary))
                store.update(tid, allow_compaction=True, unsafe_native_compaction=True)
                driver = Mock(unsafe_driver=True)
                queue = CompactionQueue(store, driver)
                item = dict(request_id='fixture-request', thread_id=tid, origin_turn_id='fixture-origin', status='queued')
                if source:
                    item['codex_home'] = str(store.home / source)
                write_json(queue.path, {'schema': 1, 'requests': {'fixture-request': item}})
                queue.tick()
                self.assertEqual(queue.load()['requests']['fixture-request']['status'], 'deferred')
                self.assertEqual(driver.mock_calls, [])

    def test_new_queued_request_records_its_source_before_any_native_call(self):
        from compaction import CompactionQueue
        from test_reliability import fixture
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            store, tid, rollout = fixture(Path(temporary))
            with rollout.open('a', encoding='utf-8') as output:
                output.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'fixture-origin'}}) + '\n')
            store.update(tid, allow_compaction=True, unsafe_native_compaction=True)
            driver = Mock(unsafe_driver=True)
            driver.inspect.return_value = {'disposition': 'observed', 'threadId': tid, 'ownerId': 'fixture-owner', 'idle': False}
            queue = CompactionQueue(store, driver)
            item = queue.request(tid, 'fixture-origin', tid)
            self.assertEqual(item['codex_home'], str(store.home))
            self.assertEqual(driver.mock_calls, [])
            queue.tick()
            driver.inspect.assert_called_once()
            driver.run_native.assert_not_called()

    def test_destroy_cancels_old_gui_timers_before_source_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            app = Application(Store(base / 'data', codex_home(base / 'home')), smoke=True)
            invoked = Mock()
            app.after(1000, invoked)
            app.after_idle(invoked)
            self.assertTrue(app.tk.call('after', 'info'))
            app.destroy()
            self.assertFalse(app.tk.call('after', 'info'))
            self.assertTrue(app.stop_dispatch.is_set())
            invoked.assert_not_called()

    def test_sync_summary_reports_partial_missing_and_actual_errors(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            source = Path(temporary) / 'fixture.jsonl'
            source.write_text('fixture', encoding='utf-8')
            for rows, failures, expected in (
                ({'missing': {'source': None}}, {}, ('Не удалось обновить архивов: 1', 'Успешно обработано: 0')),
                ({'ok': {'source': str(source)}, 'bad': {'source': str(source)}, 'missing': {}},
                 {'bad': ValueError('fixture failure')}, ('Не удалось обновить архивов: 2', 'Успешно обработано: 1')),
                ({'ok': {'source': str(source)}}, {}, ('Архивы обработаны: 1',)),
            ):
                with self.subTest(rows=rows):
                    app = Mock(source_error='')
                    app.store.registry.return_value = {'chats': rows}
                    def sync(tid):
                        if not rows[tid].get('source'):
                            raise TranscriptUnavailable('fixture unavailable')
                        if tid in failures:
                            raise failures[tid]
                        return 'saved'
                    app.store.sync.side_effect = sync
                    with patch('compaction.CompactionQueue'):
                        Application.sync_all(app)
                        operation, done = app.run.call_args.args
                        done(operation())
                    message = app.status.set.call_args.args[0]
                    for phrase in expected:
                        self.assertIn(phrase, message)
                    if failures or 'missing' in rows:
                        self.assertNotIn('Архивы обновлены', message)

    def test_sync_refreshes_moved_source_instead_of_skipping_cached_path(self):
        from test_reliability import fixture
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            store, tid, old_source = fixture(Path(temporary))
            archive = store.folder(tid) / 'source.jsonl'
            before = archive.read_bytes()
            new_source = old_source.with_name('moved.jsonl')
            old_source.rename(new_source)
            with closing(sqlite3.connect(store.home / 'state_5.sqlite')) as db:
                db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(new_source), tid))
                db.commit()
            app = Mock(source_error='', store=store)
            with patch('compaction.CompactionQueue'):
                Application.sync_all(app)
                operation, done = app.run.call_args.args
                done(operation())
            self.assertEqual(store.registry()['chats'][tid]['source'], str(new_source))
            self.assertEqual(archive.read_bytes(), before)
            self.assertIn('Архивы обработаны: 1', app.status.set.call_args.args[0])

    def test_footer_wraps_and_remains_inside_small_and_large_windows(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            app = Application(Store(base / 'data', codex_home(base / 'home')), smoke=True)
            try:
                app.status.set('Не удалось обновить архивов: 6. ' * 9)
                for width, height in ((960, 680), (1200, 900)):
                    app.geometry(f'{width}x{height}')
                    app.update()
                    app.update_idletasks()
                    label = app.status_label
                    self.assertLessEqual(label.winfo_rootx() + label.winfo_width(), app.winfo_rootx() + app.winfo_width())
                    self.assertLessEqual(label.winfo_rooty() + label.winfo_height(), app.winfo_rooty() + app.winfo_height())
                    self.assertLessEqual(int(label.cget('wraplength')), label.winfo_width() - 24)
                    self.assertGreater(label.winfo_height(), 25)
            finally:
                app.destroy()

    def test_choose_source_validates_persists_and_requests_fresh_gui(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            first, second = codex_home(base / 'first'), codex_home(base / 'second')
            store = Store(base / 'data', first)
            registry = (store.root / 'registry.json').read_bytes()
            database = (second / 'state_5.sqlite').read_bytes()
            app = Application(store, smoke=True)
            app.withdraw()
            try:
                with patch('gui.filedialog.askdirectory', return_value=str(second)), patch('gui.messagebox.askyesno', return_value=True):
                    app.choose_source()
                self.assertEqual(app.restart_home, second)
                self.assertEqual(app.store.home, first)  # No live driver/snapshot rebinding.
                self.assertEqual(gui_home(store.root, None), (second, ''))
                self.assertEqual(gui_home(store.root, first), (first, ''))
                self.assertEqual((store.root / 'registry.json').read_bytes(), registry)
                self.assertEqual((second / 'state_5.sqlite').read_bytes(), database)
            finally:
                if not app.restart_home:
                    app.destroy()

    def test_invalid_cancelled_and_busy_source_selection_do_not_change_preferences(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            store = Store(base / 'data', codex_home(base / 'home'))
            missing = base / 'empty'
            missing.mkdir()
            app = Application(store, smoke=True)
            app.withdraw()
            try:
                with patch('gui.filedialog.askdirectory', return_value=str(missing)), patch('gui.messagebox.askyesno') as ask:
                    with self.assertRaises(ValueError):
                        app.choose_source()
                    ask.assert_not_called()
                with patch('gui.filedialog.askdirectory', return_value=''):
                    app.choose_source()
                app.busy = True
                with patch('gui.filedialog.askdirectory') as picker:
                    app.choose_source()
                    picker.assert_not_called()
                self.assertIsNone(app.restart_home)
                self.assertFalse((store.root / 'gui-source.json').exists())
            finally:
                app.destroy()

    def test_invalid_saved_source_blocks_fallback_but_explicit_cli_wins(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            store = Store(base / 'data', codex_home(base / 'home'))
            (store.root / 'gui-source.json').write_text('not JSON', encoding='utf-8')
            selected, error = gui_home(store.root, None)
            self.assertIsNone(selected)
            self.assertTrue(error)
            with patch.object(store, 'projects', side_effect=AssertionError('Must not read a silent fallback')):
                app = Application(store, smoke=True, source_config_error=error)
            try:
                self.assertTrue(app.source_error)
                self.assertNotIn('disabled', app.source_picker.state())
                with patch.object(app, 'run', side_effect=AssertionError('Unknown source must not call native config')):
                    app.read_native()
                invoked = Mock()
                app.guard(invoked)
                invoked.assert_not_called()
                self.assertEqual(gui_home(store.root, store.home), (store.home, ''))
            finally:
                app.destroy()

    def test_gui_restart_waits_for_old_monitor_and_constructs_new_store(self):
        import main
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            first, second = codex_home(base / 'first'), codex_home(base / 'second')
            events = []
            monitor = Mock()
            monitor.is_alive.return_value = False
            monitor.join.side_effect = lambda **_kwargs: events.append('joined')
            def application(store, **kwargs):
                if not events:
                    self.assertEqual(store.home, first)
                    events.append('first')
                    return Mock(restart_home=second, monitor_thread=monitor)
                self.assertEqual(events, ['first', 'joined'])
                self.assertEqual(store.home, second)
                events.append('second')
                return Mock(restart_home=None)
            with patch('gui.Application', side_effect=application), patch.object(sys, 'argv',
                    ['main.py', '--data', str(base / 'data'), '--codex-home', str(first)]):
                main.main()
            self.assertEqual(events, ['first', 'joined', 'second'])
