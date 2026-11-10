"""Reliability contracts against disposable stores and a real child-process lease."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid
import zstandard

from core import KEYS, Store, lock, read_transcript
from hooks import hook_response


def fixture(root):
    home = root / 'home'
    home.mkdir()
    tid = str(uuid.uuid4())
    source = home / 'rollout.jsonl'
    source.write_bytes((json.dumps({'type': 'session_meta', 'payload': {'id': tid}}) + '\n').encode())
    with closing(sqlite3.connect(home / 'state_5.sqlite')) as db:
        db.execute('create table threads(id text, title text, rollout_path text)')
        db.execute('insert into threads values(?,?,?)', (tid, 'Synthetic', str(source)))
        db.commit()
    store = Store(root / 'data', home)
    store.add(tid)
    store.sync(tid)
    store.update(tid, selected=dict.fromkeys(KEYS, True), auto_restore=True)
    return store, tid, source


class ReliabilityTests(unittest.TestCase):
    def test_hook_delivers_during_writer_lease_and_killed_writer_releases_lock(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            folder = store.folder(tid)
            (folder / 'memo.md').write_text('Independent memo delivery', encoding='utf-8')
            before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
            code = 'from core import lock; import sys,os\nwith lock(sys.argv[1]):\n print(os.getpid(),flush=True)\n sys.stdin.read()'
            # Windows venv python.exe is a redirector, not the lease owner.
            child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-u', '-c', code, str(store.root)],
                cwd=Path(__file__).parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
            try:
                self.assertEqual(int(child.stdout.readline().strip()), child.pid, 'Kill must target the actual lease owner')
                with self.assertRaises(ValueError):
                    with lock(store.root):
                        self.fail('Concurrent writer acquired lease')
                event = dict(session_id=tid, hook_event_name='SessionStart', source='compact', transcript_path=str(source))
                with patch.object(store, 'sync', side_effect=AssertionError('hook must not synchronize')):
                    reply = hook_response(event, store)
                text = reply['hookSpecificOutput']['additionalContext']
                self.assertIn('Independent memo delivery', text)
                self.assertIn('last successful archive sync', text)
                self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()})
                wrong = source.with_name('wrong.jsonl')
                wrong.write_bytes(source.read_bytes())
                self.assertNotIn('hookSpecificOutput', hook_response({**event, 'transcript_path': str(wrong)}, store))
            finally:
                child.kill()
                child.wait(timeout=5)
                for pipe in (child.stdin, child.stdout, child.stderr):
                    pipe.close()
            self.assertTrue((store.root / '.keeper.lock').exists())
            with lock(store.root):
                pass  # A remaining lock file is not a remaining kernel lease.

    def test_unchanged_warm_sync_skips_source_but_changes_and_cold_store_validate(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            folder = store.folder(tid)
            # Flag changes do not invalidate verified source bytes.
            with patch('core.read_transcript', wraps=read_transcript) as read:
                store.sync(tid)
                read.assert_not_called()
                with closing(sqlite3.connect(store.home / 'state_5.sqlite')) as db:
                    db.execute('update threads set title=? where id=?', ('Renamed', tid))
                    db.commit()
                store.sync(tid)
                self.assertEqual(store.registry()['chats'][tid]['title'], 'Renamed')
                read.assert_not_called()
                Store(store.root, store.home).sync(tid)
                self.assertEqual(read.call_count, 1)
                (folder / 'history.md').unlink()
                store.sync(tid)
                self.assertEqual(read.call_count, 2)
                self.assertTrue((folder / 'history.md').is_file())
                original = source.read_bytes()
                tail = (json.dumps({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new'}}) + '\n').encode()
                source.write_bytes(original + tail[:-1])
                store.sync(tid)
                self.assertEqual(read.call_count, 3)
                self.assertEqual((folder / 'source.jsonl').read_bytes(), original)
                source.write_bytes(original + tail)
                store.sync(tid)
                self.assertEqual(read.call_count, 4)
                packed = Path(str(source) + '.zst')
                packed.write_bytes(zstandard.ZstdCompressor().compress(original + tail))
                source.unlink()
                store.sync(tid)
                self.assertEqual(read.call_count, 5)
                store.sync(tid)
                self.assertEqual(read.call_count, 5)
                packed.write_bytes(zstandard.ZstdCompressor().compress(original))
                with self.assertRaises(ValueError):
                    store.sync(tid)
                self.assertEqual((folder / 'source.jsonl').read_bytes(), original + tail)
            store.disconnect([tid])
            self.assertNotIn(tid, store._sync_signatures)

    def test_busy_context_read_is_queued_once_and_preserves_later_edits(self):
        from gui import Application
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store, tid, source = fixture(Path(temp))
            app = Application(store, smoke=True)
            app.withdraw()
            release = threading.Event()
            try:
                settings = Mock(snapshot=None)
                settings.read.return_value = {'file': 'fake.toml', 'configured': dict.fromkeys(
                    ('model_context_window', 'model_auto_compact_token_limit'), 600000), 'effective': {}}
                app.settings = settings
                app.run(lambda: release.wait(3))
                app.read_native()
                app.read_native()
                self.assertIsNotNone(app.pending_native_read)
                settings.read.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'Дождитесь'):
                    app.save_native()
                app.window.insert(0, '950000')
                release.set()
                deadline = time.monotonic() + 3
                while (app.busy or app.pending_native_read is not None) and time.monotonic() < deadline:
                    app.check_work()
                    time.sleep(.01)
                self.assertFalse(app.busy)
                self.assertIsNone(app.pending_native_read)
                settings.read.assert_called_once()
                self.assertEqual(app.window.get(), '950000')
                self.assertEqual(app.compact.get(), '600 000')
                app.pending_close = True
                app.read_native()
                settings.read.assert_called_once()
            finally:
                release.set()
                app.destroy()


if __name__ == '__main__':
    unittest.main()
