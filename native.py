"""Fresh minimal implementation of documented Codex configuration RPC."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

LIMIT_KEYS = ('model_context_window', 'model_auto_compact_token_limit')


def resolve_cli():
    explicit = os.environ.get('CODEX_CLI_PATH')
    if explicit and Path(explicit).is_file():
        return explicit
    root = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI' / 'Codex' / 'bin'
    candidates = list(root.glob('*/codex.exe')) if root.is_dir() else []
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    path = shutil.which('codex.exe')
    if path:
        return path
    raise ValueError('Не найден codex.exe. Укажите CODEX_CLI_PATH или установите Codex Desktop.')


class Rpc:
    def __init__(self, executable=None, cwd=None, home=None):
        self.executable = executable or resolve_cli()
        self.cwd = cwd
        self.home = Path(home).resolve() if home is not None else None

    def call(self, method, params):
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf-8', errors='strict', cwd=self.cwd)
        if self.home is not None:
            kwargs['env'] = dict(os.environ, CODEX_HOME=str(self.home))
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen([self.executable, 'app-server', '--stdio'], **kwargs)
        replies = queue.Queue()
        def reader():
            try:
                for line in process.stdout:
                    replies.put(json.loads(line))
            except Exception:
                replies.put({'error': {'message': 'Некорректный ответ протокола'}})
            finally:
                replies.put(None)
        def drain():
            # Never store/print stderr: it can contain unrelated configuration diagnostics.
            for _ in process.stderr:
                pass
        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=drain, daemon=True).start()
        deadline = time.monotonic() + 30
        def request(rid, name, args):
            process.stdin.write(json.dumps({'id': rid, 'method': name, 'params': args}) + '\n')
            process.stdin.flush()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Codex app-server не ответил за 30 секунд.')
                try:
                    result = replies.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError('Codex app-server не ответил за 30 секунд.')
                if result is None:
                    raise ValueError('Codex закрыл поток протокола.')
                if result.get('id') != rid:
                    continue
                if 'error' in result:
                    code = result['error'].get('code', 'unknown')
                    raise ValueError(f'Операция {name} отклонена Codex (код {code}); настройки не подтверждены.')
                return result['result']
        try:
            request(1, 'initialize', {'clientInfo': {'name': 'codex-context-keeper', 'title': 'Codex Context Keeper', 'version': '0.2.1'},
                'capabilities': {'experimentalApi': True}})
            process.stdin.write(json.dumps({'method': 'initialized', 'params': {}}) + '\n')
            process.stdin.flush()
            return request(2, method, params)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()


class ContextSettings:
    def __init__(self, rpc=None):
        self.rpc = rpc or Rpc()
        self.snapshot = None

    def read(self):
        response = self.rpc.call('config/read', {'includeLayers': True})
        layers = [layer for layer in response.get('layers', [])
            if isinstance(layer.get('name'), dict) and layer['name'].get('type') == 'user' and not layer['name'].get('profile')]
        if len(layers) != 1:
            raise ValueError('Не найден единственный пользовательский слой конфигурации.')
        user = layers[0]
        result = dict(file=user['name']['file'], version=user['version'],
            configured={k: user.get('config', {}).get(k) for k in LIMIT_KEYS},
            effective={k: response.get('config', {}).get(k) for k in LIMIT_KEYS})
        for value in (*result['configured'].values(), *result['effective'].values()):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError('Некорректные лимиты в ответе Codex.')
        self.snapshot = result
        return result

    def save(self, window, compact):
        if self.snapshot is None:
            raise ValueError('Сначала прочитайте текущие настройки.')
        if any(v is not None and (type(v) is not int or v <= 0 or v > 2**63 - 1) for v in (window, compact)):
            raise ValueError('Нужны положительные целые числа; пусто означает отсутствие переопределения.')
        if window is not None and compact is not None and compact > window:
            raise ValueError('Автопорог не может превышать заданное окно.')
        desired = dict(zip(LIMIT_KEYS, (window, compact)))
        edits = [{'keyPath': k, 'value': v, 'mergeStrategy': 'replace'} for k, v in desired.items()
            if v != self.snapshot['configured'][k]]
        if not edits:
            return self.snapshot
        result = self.rpc.call('config/batchWrite', {'filePath': self.snapshot['file'],
            'expectedVersion': self.snapshot['version'], 'reloadUserConfig': False, 'edits': edits})
        if result.get('status') != 'ok':
            self.snapshot = None
            raise ValueError('Запись не подтверждена или перекрыта другим слоем. Перечитайте настройки.')
        result = self.read()
        if result['configured'] != desired:
            raise ValueError('Перечитанные значения не совпали с запросом. Проверьте конфигурацию.')
        return result


def parse_limit(text):
    text = ''.join(text.split())
    if not text:
        return None
    if not text.isascii() or not text.isdigit() or not 0 < int(text) <= 2**63 - 1:
        raise ValueError('Лимит должен быть положительным целым числом.')
    return int(text)
