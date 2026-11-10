"""Compressed rollouts and explicit legacy project membership, fake data only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
import zstandard

from core import Store, read_transcript, write_json
from hooks import hook_response


class CompressedTranscriptTests(unittest.TestCase):
    def test_compressed_sync_projects_and_hook_preserve_identity_and_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            home.mkdir()
            tid, pinned, outside, conflict, remote = [str(uuid.uuid4()) for _ in range(5)]
            source = home / 'rollout.jsonl'
            raw = (json.dumps({'type': 'session_meta', 'payload': {'id': tid}}) + '\n').encode()
            source.write_bytes(raw)
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as db:
                db.execute('create table threads(id text, title text, rollout_path text, project_id text, archived integer default 0, thread_source text)')
                db.execute('create table projects(id text, name text)')
                db.executemany('insert into projects values (?,?)', [('native', 'Agent'), ('other', 'Other')])
                db.executemany('insert into threads(id,title,rollout_path,project_id) values (?,?,?,?)',
                    [(value, value, str(source), None) for value in (tid, pinned, outside, conflict, remote)])
                db.commit()
            write_json(home / '.codex-global-state.json', {
                'local-projects': {'legacy': {'name': 'Agent'}},
                'app-server-project-id-by-legacy-project-id-by-host': {
                    'local:' + str(home.resolve()): {'legacy': 'native'}},
                'sidebar-project-thread-orders': {
                    'legacy': {'threadIds': [pinned, outside, conflict, remote]},
                    'other': {'threadIds': [conflict, tid]}},
                'projectless-thread-ids': [outside],
                'thread-project-membership-host-ids': {remote: 'remote-host'},
                'thread-project-assignments': {tid: {'projectKind': 'local', 'projectId': 'legacy'}}})
            store = Store(Path(temp) / 'data', home)
            self.assertEqual(set(store.projects()['native']['ids']), {tid, pinned})
            self.assertNotIn(conflict, store.projects()['other']['ids'])
            self.assertNotIn(tid, store.projects()['other']['ids'])
            self.assertNotIn(remote, store.connection_catalog()['chats'])
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as db:
                db.execute('update threads set archived=1 where id=?', (outside,))
                db.execute("update threads set thread_source='subagent' where id=?", (conflict,))
                db.commit()
            self.assertNotIn(outside, store.connection_catalog()['chats'])
            self.assertNotIn(conflict, store.connection_catalog()['chats'])
            store.add(tid)
            store.sync(tid)
            folder = store.folder(tid)
            (folder / 'memo.md').write_text('контрольная памятка', encoding='utf-8')
            store.update(tid, auto_restore=True)
            tail = (json.dumps({'type': 'response_item', 'payload': {'type': 'message',
                'role': 'user', 'content': [{'type': 'input_text', 'text': 'привет'}]}}) + '\n').encode()
            packed = Path(str(source) + '.zst')
            compressor = zstandard.ZstdCompressor(write_content_size=False)
            compressed = compressor.compress(raw) + compressor.compress(tail)
            packed.write_bytes(compressed)
            source.unlink()
            self.assertIn(tid, store.connection_catalog()['chats'])
            self.assertEqual(store.connect_selected([tid])['connected'], [tid])
            self.assertEqual((folder / 'source.jsonl').read_bytes(), raw + tail)
            bookmarks = json.loads((folder / 'bookmarks.json').read_text(encoding='utf-8'))
            self.assertEqual(bookmarks['offset'], len(raw + tail))
            message = json.loads((folder / 'history.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(message['source_offset'], len(raw))
            self.assertEqual(packed.read_bytes(), compressed)
            event = {'hook_event_name': 'SessionStart', 'source': 'compact',
                'session_id': tid, 'transcript_path': str(source)}
            self.assertIn('контрольная памятка', hook_response(event, store)['hookSpecificOutput']['additionalContext'])
            before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
            for bad in (compressed[:-1], b'not zstd', compressor.compress(raw.replace(tid.encode(), str(uuid.uuid4()).encode()))):
                with self.subTest(bad=bad[:8]):
                    packed.write_bytes(bad)
                    with self.assertRaises(ValueError):
                        store.sync(tid)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()})
            # When Codex reopens the chat, the current uncompressed source wins.
            source.write_bytes(raw + tail)
            self.assertEqual(read_transcript(source), raw + tail)
            store.sync(tid)
            self.assertIn('контрольная памятка', hook_response(event, store)['hookSpecificOutput']['additionalContext'])
            wrong = home / 'wrong.jsonl'
            wrong.write_bytes(raw + tail)
            self.assertNotIn('hookSpecificOutput', hook_response({**event, 'transcript_path': str(wrong)}, store))


if __name__ == '__main__':
    unittest.main()
