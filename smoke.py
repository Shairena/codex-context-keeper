"""One narrow synthetic development smoke; independent review is separate."""
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid
from contextlib import closing
from core import Store, write_json, protected_threads
from hooks import hook_response
from native import ContextSettings


def smoke():
    (Path(__file__).parent / 'review_artifacts').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='keeper-smoke-', dir=Path(__file__).parent / 'review_artifacts') as temp:
        base = Path(temp)
        home = base / 'home'
        home.mkdir()
        tid = str(uuid.uuid4())
        source = home / 'synthetic.jsonl'
        rows = [dict(type='session_meta', payload={'id': tid}), dict(type='response_item', timestamp='2026-10-14',
            payload=dict(type='message', role='user', content=[{'type': 'input_text', 'text': '[[память:one]]\nТочная цитата\n[[/память]]'}])),
            dict(type='compacted', payload={'message': 'compacted view only'})]
        source.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as c:
            c.execute('create table threads(id text, title text, rollout_path text)')
            c.execute('insert into threads values (?,?,?)', (tid, 'Синтетический / чат', str(source)))
            c.commit()
        store = Store(base / 'data', home)
        assert store.add(tid) == tid
        store.sync(tid)
        store.update(tid, selected={'history': True, 'memo': False, 'notes': False}, auto_restore=True)
        rendered = store.render(tid)
        assert 'Точная цитата' in rendered and len(rendered) <= 6000
        assert hook_response({'session_id': tid, 'source': 'compact', 'hook_event_name': 'SessionStart'}, store)['hookSpecificOutput']
        assert not hook_response({'session_id': str(uuid.uuid4()), 'source': 'compact', 'hook_event_name': 'SessionStart'}, store)
        assert source.read_bytes() == (store.folder(tid) / 'source.jsonl').read_bytes()
        class Fake:
            def call(self, method, params):
                assert method == 'config/read'
                return {'layers': [{'name': {'type': 'user', 'file': 'synthetic.toml'}, 'version': '1',
                    'config': {'model_context_window': 600000}}], 'config': {}}
        assert ContextSettings(Fake()).read()['configured']['model_context_window'] == 600000
        write_json(home / 'context-keeper-protected-threads.json', [tid])
        assert tid in protected_threads(home)
        try:
            store.sync(tid)
        except ValueError:
            pass
        else:
            raise AssertionError('Local exclusion must block sync')
        write_json(home / 'context-keeper-protected-threads.json', {'invalid': tid})
        try:
            protected_threads(home)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed protection must fail closed')
        print('Synthetic archive/bookmark/config-read smoke OK; no live data or settings changed.')


if __name__ == '__main__':
    smoke()
