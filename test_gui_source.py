"""GUI source failure/recovery uses only fictional registry/SQLite fixtures."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from core import Store, write_json
from gui import Application


class GuiSourceTests(unittest.TestCase):
    def fixture(self, base):
        home = base / 'home'
        home.mkdir()
        store = Store(base / 'data', home)
        tid = '00000000-0000-4000-8000-000000000001'
        row = dict(title='Saved fixture', folder='archives/saved', selected=dict(history=True, memo=True, notes=True),
                   auto_restore=False, status='saved', limit=6000, allow_compaction=False, unsafe_native_compaction=False)
        write_json(store.root / 'registry.json', {'schema': 1, 'chats': {tid: row}})
        archive = store.root / row['folder'] / 'memo.md'
        archive.parent.mkdir(parents=True)
        archive.write_text('Preserve this fixture memo', encoding='utf-8')
        return store, tid, archive

    def test_warning_monitor_does_not_dispatch_until_database_recovery(self):
        tmp = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=tmp) as folder:
            store, tid, _archive = self.fixture(Path(folder))
            with patch('gui.threading.Thread') as thread, patch('gui.ContextWarnings') as warnings:
                app = Application(store)
                app.withdraw()
                try:
                    monitor = thread.call_args.kwargs['target']
                    # Run exactly one monitor iteration, with no thread or IPC.
                    app.warning_refresh.wait = lambda _timeout: app.stop_dispatch.set()
                    monitor()
                    warnings.return_value.tick.assert_not_called()
                    with closing(sqlite3.connect(store.home / 'state_5.sqlite')) as db:
                        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY)')
                        db.commit()
                    app.refresh()
                    self.assertFalse(app.source_error)
                    app.stop_dispatch.clear()
                    monitor()
                    warnings.return_value.tick.assert_called_once()
                finally:
                    app.destroy()

    def test_missing_database_preserves_saved_rows_and_blocks_source_actions(self):
        tmp = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=tmp) as folder:
            store, tid, archive = self.fixture(Path(folder))
            registry = (store.root / 'registry.json').read_bytes()
            memo = archive.read_bytes()
            with patch('gui.messagebox.showerror') as errors, patch('gui.install_hook') as install:
                app = Application(store, smoke=True)
                app.withdraw()
                try:
                    self.assertTrue(app.source_error)
                    self.assertIn(str(store.home), app.source_info.get())
                    self.assertIn('Проверить базу', app.source_info.get())
                    self.assertEqual(app.tree.parent(tid), 'source:unavailable')
                    self.assertFalse(app.tree.exists('project:outside'))
                    self.assertTrue(all(str(button['state']) == 'disabled' for button in app.source_buttons))
                    app.run = Mock(side_effect=AssertionError('Unavailable source must not dispatch work'))
                    app.sync_all()
                    app.connect_selected([tid])
                    app.periodic()
                    app.run.assert_not_called()
                    install.assert_not_called()
                    errors.assert_not_called()
                    self.assertEqual((store.root / 'registry.json').read_bytes(), registry)
                    self.assertEqual(archive.read_bytes(), memo)
                    self.assertFalse(list(store.home.glob('state_*.sqlite')))
                finally:
                    app.destroy()

    def test_unreadable_or_invalid_schema_retries_into_actual_project_membership(self):
        tmp = Path(__file__).resolve().parent
        for invalid in ('corrupt', 'missing-table', 'unreadable'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory(dir=tmp) as folder:
                store, tid, archive = self.fixture(Path(folder))
                database = store.home / 'state_5.sqlite'
                if invalid == 'corrupt':
                    database.write_bytes(b'not a SQLite database')
                else:
                    with closing(sqlite3.connect(database)) as db:
                        db.execute('CREATE TABLE unrelated(id TEXT)')
                before_db = database.read_bytes()
                before_registry = (store.root / 'registry.json').read_bytes()
                with patch.object(store, 'projects', side_effect=PermissionError('fixture unavailable')) if invalid == 'unreadable' else patch('gui.messagebox.showerror'):
                    app = Application(store, smoke=True)
                app.withdraw()
                try:
                    self.assertTrue(app.source_error)
                    self.assertEqual(app.tree.parent(tid), 'source:unavailable')
                    self.assertEqual(database.read_bytes(), before_db)
                    database.unlink()
                    with closing(sqlite3.connect(database)) as db:
                        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT)')
                        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT)')
                        db.execute('INSERT INTO threads VALUES (?,?)', (tid, 'agent'))
                        db.execute('INSERT INTO projects VALUES (?,?)', ('agent', 'Agent fixture'))
                        db.commit()
                    valid_db = database.read_bytes()
                    app.refresh()
                    self.assertFalse(app.source_error)
                    self.assertEqual(app.tree.parent(tid), 'project:agent')
                    self.assertTrue(all(str(button['state']) == 'normal' for button in app.source_buttons))
                    self.assertEqual(database.read_bytes(), valid_db)
                    self.assertEqual((store.root / 'registry.json').read_bytes(), before_registry)
                    self.assertEqual(archive.read_text(encoding='utf-8'), 'Preserve this fixture memo')
                finally:
                    app.destroy()
