"""One narrow synthetic development smoke; independent review is separate."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import closing
from core import Store, write_json, protected_threads
from hooks import configured_hook, hook_response, install_hook, runtime_hook_status
from native import ContextSettings


def smoke(hook_executable=None):
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
            registered_source = ('\\\\?\\' + str(source)) if os.name == 'nt' else str(source)
            c.execute('insert into threads values (?,?,?)', (tid, 'Синтетический / чат', registered_source))
            c.commit()
        store = Store(base / 'data', home)
        assert store.add(tid) == tid
        store.sync(tid)
        store.update(tid, selected={'history': True, 'memo': False, 'notes': False}, auto_restore=True)
        rendered = store.render(tid)
        assert 'Точная цитата' in rendered and len(rendered) <= 6000
        assert hook_response({'session_id': tid, 'source': 'compact', 'hook_event_name': 'SessionStart'}, store)['hookSpecificOutput']
        event = {'session_id': tid, 'source': 'compact', 'hook_event_name': 'SessionStart',
            'transcript_path': str(source)}
        assert hook_response(event, store)['hookSpecificOutput'], 'Equivalent Windows transcript spelling must restore'
        other_source = home / 'other.jsonl'
        other_source.write_bytes(source.read_bytes())
        assert 'hookSpecificOutput' not in hook_response({**event, 'transcript_path': str(other_source)}, store), 'Different file must fail closed'
        if hook_executable is not None:
            result = subprocess.run([str(hook_executable), '--keeper-hook', '--data', str(store.root),
                '--codex-home', str(home)], input=json.dumps(event), capture_output=True,
                text=True, encoding='utf-8', timeout=30, check=True)
            delivered = json.loads(result.stdout)
            assert 'Точная цитата' in delivered['hookSpecificOutput']['additionalContext'], 'Packaged hook must restore same file'
            result = subprocess.run([str(hook_executable), '--keeper-hook', '--data', str(store.root),
                '--codex-home', str(home)], input=json.dumps({**event, 'transcript_path': str(other_source)}),
                capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
            assert 'hookSpecificOutput' not in json.loads(result.stdout), 'Packaged hook must reject different file'
        assert not hook_response({'session_id': str(uuid.uuid4()), 'source': 'compact', 'hook_event_name': 'SessionStart'}, store)
        hook_file, hook_cmd = home / 'hooks.json', 'synthetic --keeper-hook'
        assert not configured_hook(hook_file, hook_cmd)
        install_hook(hook_file, hook_cmd)
        assert configured_hook(hook_file, hook_cmd)
        active = {'data': [{'hooks': [{'command': hook_cmd, 'eventName': 'sessionStart',
            'matcher': '^compact$', 'enabled': True, 'trustStatus': 'trusted'}]}]}
        assert runtime_hook_status(active, hook_cmd) == 'включён и доверен; живой возврат не подтверждён'
        active['data'][0]['hooks'][0]['trustStatus'] = 'untrusted'
        assert 'ожидает доверия' in runtime_hook_status(active, hook_cmd)
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
        print('Synthetic archive/bookmark/hook/config-read smoke OK; no live data or settings changed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--hook-exe', type=Path)
    smoke(parser.parse_args().hook_exe)
