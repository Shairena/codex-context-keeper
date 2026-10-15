"""Explicitly risk-accepted native owner driver, NOT an atomic idle guard."""
import json
import os
from pathlib import Path
import subprocess
import sys


class UnsafeNativeDriver:
    unsafe_driver = True
    verified_atomic_idle_guard = False

    def __init__(self, executable=None, home=None):
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent / 'dist'
        self.executable = Path(executable or base / 'ContextKeeperBridge.exe')
        self.home = Path(home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()

    def call(self, args, timeout):
        if not self.executable.is_file():
            return {'disposition': 'not_started', 'detail': 'Отсутствует ContextKeeperBridge.exe рядом с приложением.'}
        kwargs = {'capture_output': True, 'encoding': 'utf-8', 'errors': 'strict', 'timeout': timeout}
        kwargs['env'] = dict(os.environ, CODEX_HOME=str(self.home))
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([str(self.executable), *args], **kwargs)
        lines = result.stdout.strip().splitlines()
        if len(lines) != 1:
            raise ValueError('Некорректный ответ native bridge; результат операции не подтверждён.')
        value = json.loads(lines[0])
        if not isinstance(value, dict) or value.get('disposition') not in ('observed', 'not_started', 'completed', 'ambiguous'):
            raise ValueError('Неизвестное состояние native bridge.')
        return value

    def inspect(self, tid, origin):
        return self.call(['inspect', tid, origin], 35)

    def run_native(self, tid, origin, owner):
        return self.call(['compact', tid, origin, owner, '--accept-known-race'], 120)
