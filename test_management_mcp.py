"""One disposable end-to-end management protocol regression; no live Codex."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from core import Store
from unittest.mock import Mock, patch


class ManagementTests(unittest.TestCase):
    def test_relocated_gui_preserves_only_matching_same_file_hook_command(self):
        from gui import Application
        from hooks import command, install_hook
        from core import write_json
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            base = Path(temp)
            suffix = '.exe' if sys.platform == 'win32' else ''
            old, new, home = base / 'old', base / 'new', base / 'home'
            for directory in (old, new, home, old/'data', new/'data'):
                directory.mkdir(exist_ok=True)
            (new / ('ContextKeeperHook' + suffix)).write_bytes(b'fixture')
            (old / ('ContextKeeperHook' + suffix)).write_bytes(b'fixture')
            write_json(new / 'relocation.json', {'previous_install': str(old)})
            hook_path = home / 'hooks.json'
            legacy = command(old/('ContextKeeperHook' + suffix), old/'data', home, resolve_paths=False)
            install_hook(hook_path, legacy)
            original = hook_path.read_bytes()
            app = Mock()
            app.store.root, app.store.home = new/'data', home
            app.hook_path.get.return_value = str(hook_path)
            with patch('gui.sys.frozen', True, create=True), patch('gui.sys.executable', str(new/('ContextKeeper' + suffix))):
                with patch('gui.Path.samefile', return_value=True) as identity:
                    self.assertEqual(Application.hook_command(app), legacy)
                    self.assertEqual(identity.call_count, 2)
                with patch('gui.Path.samefile', return_value=False):
                    self.assertEqual(Application.hook_command(app), command(new/('ContextKeeperHook' + suffix), new/'data', home))
            self.assertEqual(hook_path.read_bytes(), original)

    def test_progress_reveals_new_chat_before_worker_finishes_and_window_size_roundtrips(self):
        from core import KEYS, write_json, read_json
        from gui import Application
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            write_json(root / 'registry.json', {})
            write_json(root / 'window.json', {'width': 1000, 'height': 700, 'maximized': False})
            old, added = str(uuid.uuid4()), str(uuid.uuid4())
            row = {'title': 'Old', 'selected': dict.fromkeys(KEYS, True), 'auto_restore': True, 'status': 'saved', 'limit': 6000}
            rows = {old: row}
            store = Mock(home=root, root=root)
            store.registry.return_value = {'chats': rows}
            store.projects.return_value = {'agent': {'name': 'Agent', 'ids': [old, added]}}
            app = Application(store, smoke=True)
            app.withdraw()
            started, release, completed = threading.Event(), threading.Event(), threading.Event()
            try:
                self.assertEqual(app.normal_size, (1000, 700))
                app.tree.item('project:agent', open=False)
                def worker():
                    rows[added] = {**row, 'title': 'Новый чат'}
                    write_json(root / 'registry.json', {'changed': True})
                    app.report_progress(1, 2, 'Новый чат', 'Обновление архива', added)
                    started.set()
                    if not release.wait(3):
                        raise RuntimeError('Test gate timed out')
                    return 'done'
                app.run(worker, lambda value: completed.set())
                self.assertTrue(started.wait(2))
                app.check_work()
                self.assertTrue(app.busy)
                self.assertTrue(app.tree.exists(added))
                self.assertTrue(app.tree.item('project:agent', 'open'))
                self.assertEqual(app.tree.selection(), (added,))
                self.assertIn('1/2', app.status.get())
                self.assertIn('Прошло', app.elapsed.get())
                self.assertEqual(str(app.progress['mode']), 'indeterminate')
                release.set()
                deadline = time.monotonic() + 2
                while not completed.is_set() and time.monotonic() < deadline:
                    app.check_work()
                    time.sleep(.01)
                self.assertTrue(completed.is_set())
                self.assertFalse(app.busy)
                # A separate MCP writer updates the registry without a GUI job.
                rows.pop(added)
                write_json(root / 'registry.json', {'external': True})
                app.check_work()
                self.assertFalse(app.tree.exists(added))
                app.normal_size = (1024, 720)
                app.save_window_size()
                self.assertEqual(read_json(root / 'window.json')['width'], 1024)
                app.normal_size = (1120, 790)
                app.restore_window_size()
                self.assertEqual(app.normal_size, (1024, 720))
            finally:
                release.set()
                app.destroy()

    def test_history_button_reads_utf8_without_external_association_or_mutation(self):
        from gui import Application
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp)
            history = folder / 'history.md'
            expected = '# История Юникода\n\nПривет! Ёжик — проверка Unicode.\n'
            history.write_bytes(expected.encode('utf-8'))
            original = history.read_bytes()
            app = Mock()
            app.store.folder.return_value = folder
            with patch('gui.open_document') as external:
                Application.open_file(app, 'history.md')
            external.assert_not_called()
            app.text_window.assert_called_once_with('История чата — UTF-8', expected)
            self.assertEqual(history.read_bytes(), original)
            with patch('gui.os.startfile') as external:
                Application.open_file(app, None)
            external.assert_called_once_with(str(folder))

    def test_archive_layout_migration_rollback_and_pending_move_recovery(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            home = root / 'home'
            home.mkdir()
            tid = str(uuid.uuid4())
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as conn:
                conn.execute('create table threads(id text, title text, rollout_path text, project_id text)')
                conn.execute('create table projects(id text, name text)')
                conn.execute('insert into projects values (?,?)', ('agent', 'Agent'))
                conn.execute('insert into threads values (?,?,?,?)', (tid, 'Saved', str(home/'unused'), 'agent'))
                conn.commit()
            store = Store(root / 'data', home)
            legacy = store.root / ('Saved -- ' + tid)
            legacy.mkdir()
            (legacy / 'notes.md').write_text('Exact notes', encoding='utf-8')
            from core import write_json, read_json
            write_json(legacy / 'bookmarks.json', {'thread_id': tid})
            registry = store.registry()
            registry['chats'][tid] = {'title': 'Saved', 'folder': legacy.name}
            write_json(store.root / 'registry.json', registry)
            calls = 0
            def fail_commit(path, value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt()
                write_json(path, value)
            with patch('core.write_json', side_effect=fail_commit), self.assertRaises(KeyboardInterrupt):
                store.organize_archives()
            self.assertTrue(legacy.is_dir())
            self.assertEqual(store.folder(tid), legacy)
            result = store.organize_archives()
            self.assertEqual(result['moved'], [tid])
            self.assertFalse(result['errors'])
            target = store.folder(tid)
            self.assertEqual(target.relative_to(store.root).parts[0], 'archives')
            self.assertTrue(target.parent.name.startswith('Agent -- '))
            self.assertEqual((target / 'notes.md').read_text(encoding='utf-8'), 'Exact notes')
            registry = store.registry()
            registry['chats'][tid]['moving_to'] = registry['chats'][tid]['folder']
            registry['chats'][tid]['folder'] = legacy.name
            write_json(store.root / 'registry.json', registry)
            self.assertEqual(store.folder(tid), target)
            self.assertEqual(store.organize_archives()['moved'], [tid])
            self.assertNotIn('moving_to', store.registry()['chats'][tid])
            with self.assertRaises(ValueError):
                store.archive_path('../escape')

    def test_registered_project_tree_and_disconnect_except_selection(self):
        from core import KEYS
        from gui import Application
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            one, other = str(uuid.uuid4()), str(uuid.uuid4())
            rows = {tid: {'title': title, 'selected': dict.fromkeys(KEYS, True), 'auto_restore': True, 'status': 'saved'}
                for tid, title in ((one, 'Iva'), (other, 'S'))}
            store = Mock(home=Path(temp), root=Path(temp))
            (Path(temp) / 'registry.json').write_text('{}', encoding='utf-8')
            store.registry.return_value = {'chats': rows}
            store.projects.return_value = {'agent': {'name': 'Agent', 'ids': [one]}}
            app = Application(store, smoke=True)
            app.withdraw()
            try:
                self.assertEqual(app.tree.parent(one), 'project:agent')
                self.assertEqual(app.tree.parent(other), 'project:outside')
                app.tree.selection_set('project:agent')
                with self.assertRaises(ValueError):
                    app.selected()
                with patch('gui.messagebox.askyesno', return_value=False), patch.object(app, 'run') as run:
                    app.disconnect_others()
                    run.assert_not_called()
                with patch('gui.messagebox.askyesno', return_value=True), patch.object(app, 'run') as run:
                    app.disconnect_others()
                    run.call_args.args[0]()
                store.disconnect.assert_called_once_with([other])
                app.tree.selection_remove(*app.tree.selection())
                with self.assertRaises(ValueError):
                    app.disconnect_others()
            finally:
                app.destroy()

    def test_stdio_connect_disconnect_reconnect_and_validation_without_worker(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            home = root / 'home'
            home.mkdir()
            tid = str(uuid.uuid4())
            source = home / 'session.jsonl'
            source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': tid}}) + '\n', encoding='utf-8')
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as conn:
                conn.execute('create table threads(id text, title text, rollout_path text)')
                conn.execute('insert into threads values (?,?,?)', (tid, 'Synthetic chat', str(source)))
                conn.commit()
            data = root / 'data'

            def rpc(requests, *, legacy=False):
                argv = [sys.executable, '-B', str(Path(__file__).with_name('main.py')),
                    '--data', str(data), '--codex-home', str(home)]
                argv += ['--mcp', '--bound-thread', tid] if legacy else ['--mcp-management']
                result = subprocess.run(argv, input=''.join(json.dumps({'jsonrpc': '2.0', 'id': i + 1, **request}) + '\n'
                    for i, request in enumerate(requests)), text=True, encoding='utf-8', capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                return [json.loads(line)['result'] for line in result.stdout.splitlines()]

            def call(name, args=None):
                return {'method': 'tools/call', 'params': {'name': name, 'arguments': args or {}}}

            replies = rpc([{'method': 'initialize', 'params': {}}, {'method': 'tools/list'},
                call('list_available_chats'), call('connect_chats', {'thread_ids': [tid]}), call('list_connected_chats')])
            self.assertEqual(replies[0]['serverInfo']['name'], 'context-keeper-management')
            self.assertEqual({t['name'] for t in replies[1]['tools']},
                {'list_available_chats', 'list_connected_chats', 'connect_chats', 'disconnect_chats', 'organize_archives', 'get_context_status', 'configure_context_warnings'})
            for reply in replies[2:]:
                self.assertFalse(reply['isError'], reply)
            store = Store(data, home)
            row = store.registry()['chats'][tid]
            self.assertTrue(all(row['selected'].values()))
            self.assertTrue(row['auto_restore'])
            for key in ('allow_compaction', 'unsafe_native_compaction'):
                self.assertFalse(row[key])
            folder = store.folder(tid)
            (folder / 'notes.md').write_text('Preserved notes', encoding='utf-8')
            original = (data / 'registry.json').read_bytes()
            events = []
            store.connect_selected([tid], progress=lambda *event: events.append(event))
            self.assertEqual(events[0][:2], (0, 1))
            self.assertEqual(events[-1][3:], ('Подключён', tid))
            self.assertTrue(any(len(event) == 5 and event[3] == 'Обновление архива' for event in events))
            original = (data / 'registry.json').read_bytes()
            invalid = rpc([call('disconnect_chats', {'thread_ids': []}),
                call('disconnect_chats', {'thread_ids': [tid, str(uuid.uuid4())]}),
                call('connect_chats', {'thread_ids': 'wrong-type'}),
                call('request_compaction_after_turn', {'origin_turn_id': 'no'})])
            self.assertTrue(all(reply['isError'] for reply in invalid))
            self.assertEqual((data / 'registry.json').read_bytes(), original)
            self.assertFalse(rpc([call('disconnect_chats', {'thread_ids': [tid]})])[0]['isError'])
            self.assertEqual(store.registry()['chats'], {})
            self.assertEqual(len(list(data.glob('registry-backup-*.json'))), 1)
            self.assertFalse(rpc([call('connect_chats', {'thread_ids': [tid]})])[0]['isError'])
            self.assertEqual(store.folder(tid), folder)
            self.assertEqual((folder / 'notes.md').read_text(encoding='utf-8'), 'Preserved notes')
            self.assertFalse((data / 'compaction-requests.json').exists())
            self.assertFalse((data / '.compaction-worker.lock').exists())
            legacy = rpc([{'method': 'initialize', 'params': {}}, {'method': 'tools/list'}], legacy=True)
            self.assertEqual(legacy[0]['serverInfo']['name'], 'codex-context-keeper')
            self.assertEqual(len(legacy[1]['tools']), 3)


if __name__ == '__main__':
    unittest.main()
