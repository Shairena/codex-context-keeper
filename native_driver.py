"""Explicitly risk-accepted native owner driver, NOT an atomic idle guard."""
import json
import os
from pathlib import Path
import subprocess
import sys
from platform_support import bridge_executable


class UnsafeNativeDriver:
    unsafe_driver = True
    verified_atomic_idle_guard = False

    def __init__(self, executable=None, home=None, data=None):
        self.executable = Path(executable or bridge_executable())
        self.home = Path(home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
        self.data = Path(data).resolve() if data is not None else None

    def call(self, args, timeout):
        if not self.executable.is_file():
            return {'disposition': 'not_started', 'detail': 'Отсутствует нативный мост ContextKeeperBridge для этой платформы.'}
        kwargs = {'capture_output': True, 'encoding': 'utf-8', 'errors': 'strict', 'timeout': timeout}
        kwargs['env'] = dict(os.environ, CODEX_HOME=str(self.home))
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([str(self.executable), *args], **kwargs)
        lines = result.stdout.strip().splitlines()
        if len(lines) != 1:
            raise ValueError('Некорректный ответ native bridge; результат операции не подтверждён.')
        value = json.loads(lines[0])
        if not isinstance(value, dict) or value.get('disposition') not in ('observed', 'not_started', 'completed', 'ambiguous', 'accepted'):
            raise ValueError('Неизвестное состояние native bridge.')
        return value

    def inspect(self, tid, origin):
        return self.call(['inspect', tid, origin], 35)

    def observe(self, tid):
        return self.call(['observe', tid, '-'], 7)

    def remind(self, tid, owner, percent, context_key):
        if self.data is None:
            raise ValueError('Для напоминания нужно явное хранилище Keeper.')
        return self.call(['remind', tid, owner, str(percent), context_key, str(self.data)], 30)

    def run_native(self, tid, origin, owner):
        return self.call(['compact', tid, origin, owner, '--accept-known-race'], 120)
