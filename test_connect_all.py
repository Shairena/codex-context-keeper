"""Bulk connection contract using disposable Codex databases only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from core import KEYS, Store, write_json
from gui import Application, ChatPicker


class ConnectAllTests(unittest.TestCase):
    def test_bulk_preserves_documents_skips_protected_and_retries_failed_archives(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            home = root / 'home'
            home.mkdir()
            existing, fresh, protected, broken, missing, outside, loose, remote, root_only, unnamed, blank = [str(uuid.uuid4()) for _ in range(11)]
            project, other = 'project-agent', 'project-other'
            database = home / 'state_5.sqlite'
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('create table threads(id text, title text, rollout_path text, archived integer, project_id text, cwd text)')
                conn.execute('create table projects(id text, name text)')
                conn.executemany('insert into projects values (?,?)', [(project, 'Agent'), (other, 'Other')])
                for tid in (existing, fresh, protected, broken, missing, outside, loose, remote, root_only, unnamed, blank):
                    source = home / f'{tid}.jsonl'
                    if tid != missing:
                        source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': tid}}) + '\n', encoding='utf-8')
                    if tid == broken:
                        source.write_text('invalid json\n', encoding='utf-8')
                    pid = None if tid in (existing, root_only) else other if tid == outside else project
                    title = '' if tid == unnamed else ' \t ' if tid == blank else tid
                    conn.execute('insert into threads values (?,?,?,?,?,?)', (tid, title, str(source), 0, pid, 'X:/ExampleProject'))
                conn.commit()
            write_json(home / '.codex-global-state.json', {
                'local-projects': {'legacy-agent': {'name': 'Agent'}},
                'app-server-project-id-by-legacy-project-id-by-host': {'local:' + str(home.resolve()): {'legacy-agent': project}},
                'thread-project-assignments': {existing: {'projectKind': 'local', 'projectId': 'legacy-agent'}},
                'projectless-thread-ids': [loose], 'thread-project-membership-host-ids': {remote: 'remote-host'}})
            write_json(home / 'context-keeper-protected-threads.json', [protected])
            store = Store(root / 'data', home)
            store.add(existing)
            store.update(existing, limit=9000, preparation_threshold=12345)
            store.update(existing, allow_compaction=True, unsafe_native_compaction=True)
            project_members = store.projects()[project]['ids']
            catalog = store.connection_catalog()
            self.assertEqual(catalog['groups'][-1]['name'], 'Чаты вне проекта')
            self.assertIn(loose, catalog['groups'][-1]['ids'])
            self.assertIn(root_only, catalog['groups'][-1]['ids'])
            self.assertNotIn(remote, catalog['chats'])
            for tid in (missing, unnamed, blank):
                self.assertNotIn(tid, catalog['chats'])
                self.assertFalse(any(tid in group['ids'] for group in catalog['groups']))
            self.assertIn(protected, catalog['chats'])
            folder = store.folder(existing)
            (folder / 'memo.md').write_text('Keep memo', encoding='utf-8')
            (folder / 'notes.md').write_text('Keep notes', encoding='utf-8')
            with patch('compaction.CompactionQueue.tick', side_effect=AssertionError('must not compact')):
                result = store.connect_selected(project_members)
            self.assertEqual(set(result['connected']), {existing, fresh})
            self.assertEqual(result['protected'], [protected])
            self.assertEqual(result['unavailable'], sorted([missing, unnamed, blank]))
            self.assertEqual(len(result['errors']), 1)
            self.assertIn(broken, result['errors'][0])
            rows = store.registry()['chats']
            self.assertNotIn(protected, rows)
            for tid in (missing, unnamed, blank, outside, loose, remote, root_only):
                self.assertNotIn(tid, rows)
            self.assertFalse(rows[broken]['unsafe_native_compaction'])
            for tid in (existing, fresh):
                self.assertEqual(rows[tid]['selected'], dict.fromkeys(KEYS, True))
                self.assertTrue(rows[tid]['auto_restore'])
                for flag in ('allow_compaction', 'unsafe_native_compaction'):
                    self.assertFalse(rows[tid][flag])
            self.assertEqual(store.folder(existing), folder)
            self.assertEqual((folder / 'memo.md').read_text(encoding='utf-8'), 'Keep memo')
            self.assertEqual((folder / 'notes.md').read_text(encoding='utf-8'), 'Keep notes')
            self.assertEqual(rows[existing]['limit'], 9000)
            self.assertEqual(rows[existing]['preparation_threshold'], 12345)
            self.assertEqual(store.connect_selected(project_members)['connected'], result['connected'])
            (home / f'{broken}.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': broken}}) + '\n', encoding='utf-8')
            self.assertEqual(len(store.connect_selected(project_members)['connected']), 3)
            before = (store.root / 'registry.json').read_bytes()
            with self.assertRaises(ValueError):
                store.connect_selected(['not-a-thread-id'])
            self.assertEqual((store.root / 'registry.json').read_bytes(), before)
            self.assertEqual(store.connect_selected([outside, root_only, outside])['connected'], sorted([outside, root_only]))
            self.assertNotIn(loose, store.registry()['chats'])
            self.assertEqual(store.connect_selected([])['connected'], [])
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute('select count(*) from threads').fetchone()[0], 11)

    def test_invalid_protection_list_fails_before_registration(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            write_json(root / 'context-keeper-protected-threads.json', ['invalid'])
            store = Store(root / 'data', root)
            with self.assertRaises(ValueError):
                store.connect_selected([])
            self.assertEqual(store.registry()['chats'], {})

    def test_gui_cancel_busy_and_hook_refusal_do_not_connect(self):
        app = Mock(busy=False)
        with patch('gui.messagebox.askyesno', return_value=False):
            Application.connect_selected(app, ['one'])
        app.run.assert_not_called()
        app.busy = True
        with patch('gui.messagebox.askyesno') as ask:
            Application.connect_selected(app, ['one'])
        ask.assert_not_called()
        app.busy = False
        app.install.return_value = False
        with patch('gui.messagebox.askyesno', return_value=True), patch('gui.configured_hook', return_value=False):
            Application.connect_selected(app, ['one'])
        app.run.assert_not_called()

    def test_gui_confirmed_existing_hook_runs_only_bulk_registration(self):
        app = Mock(busy=False)
        with patch('gui.messagebox.askyesno', return_value=True) as ask, patch('gui.configured_hook', return_value=True):
            Application.connect_selected(app, ['one'])
        self.assertIn('Самосжатие и удалённое сжатие будут выключены', ask.call_args.args[1])
        self.assertNotIn('НЕБЕЗОПАС', ask.call_args.args[1])
        app.install.assert_not_called()
        app.run.call_args.args[0]()
        app.store.connect_selected.assert_called_once_with(['one'], progress=app.report_progress)
        picker = object.__new__(ChatPicker)
        picker.members = {'project': {'one', 'two'}, 'one': {'one'}, 'two': {'two'}, 'outside': {'three'}, 'missing': set()}
        picker.chosen = set()
        picker.refresh_marks = Mock()
        picker.toggle('one')
        self.assertEqual(picker.chosen, {'one'})
        picker.toggle('project')
        self.assertEqual(picker.chosen, {'one', 'two'})
        picker.toggle('outside')
        picker.toggle('project')
        picker.toggle('missing')
        self.assertEqual(picker.chosen, {'three'})
        picker.destroy = Mock()
        picker.accept()
        self.assertEqual(picker.result, ['three'])


if __name__ == '__main__':
    unittest.main()
