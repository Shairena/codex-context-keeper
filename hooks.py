"""Opt-in compaction delivery. Does not register itself or trigger compaction."""
from __future__ import annotations
import json
from pathlib import Path
import time
from core import Store, protected_threads, atomic, lock, read_json, thread_id, write_json


def hook_response(event, store):
    if not isinstance(event, dict) or event.get('hook_event_name') != 'SessionStart' or event.get('source') != 'compact':
        return {}
    try:
        tid = thread_id(event.get('session_id', ''))
    except (ValueError, AttributeError):
        return {}
    try:
        if tid in protected_threads(store.home):
            return {}
        row = store.registry()['chats'].get(tid)
        if not row or not row['auto_restore']:
            return {}
        if event.get('transcript_path') and Path(event['transcript_path']).resolve() != Path(row['source']).resolve():
            raise ValueError('Путь события отличается от зарегистрированного источника.')
        store.sync(tid)
        text = store.render(tid)
        return {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': text}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'systemMessage': 'Context Keeper: восстановление не выполнено; данные сохранены. ' + str(exc)}


def command(executable, data, home):
    # Hook shell parsing is platform owned. Reject shell interpolation characters.
    values = [str(Path(p).resolve()) for p in (executable, data, home)]
    if any(any(c in v for c in '"\r\n%`$!&|<>^') for v in values):
        raise ValueError('Путь содержит символы оболочки; переместите приложение/данные в простой путь.')
    return f'"{values[0]}" --keeper-hook --data "{values[1]}" --codex-home "{values[2]}"'


def install_hook(path, cmd):
    """Explicit UI action only, preserving every existing handler."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock(path.parent / '.context-keeper-hook-edit'):
        before = path.read_bytes() if path.exists() else None
        config = json.loads(before.decode('utf-8-sig')) if before is not None else {'hooks': {}}
        if not isinstance(config, dict) or not isinstance(config.setdefault('hooks', {}), dict):
            raise ValueError('Неподдерживаемая структура hooks.json; файл не изменён.')
        groups = config['hooks'].setdefault('SessionStart', [])
        if not isinstance(groups, list):
            raise ValueError('Неподдерживаемый SessionStart; файл не изменён.')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
                raise ValueError('Неподдерживаемая группа; файл не изменён.')
            for handler in group.get('hooks', []):
                if not isinstance(handler, dict):
                    raise ValueError('Неподдерживаемый обработчик; файл не изменён.')
                if '--keeper-hook' in handler.get('command', ''):
                    if handler['command'] == cmd:
                        return 'Уже установлен'
                    raise ValueError('Другой Context Keeper уже зарегистрирован. Изменение отменено.')
        groups.append({'matcher': '^compact$', 'hooks': [{'type': 'command', 'command': cmd,
            'timeout': 30, 'additionalContextLimit': 0, 'statusMessage': 'Восстановление выбранных данных чата'}]})
        if before is not None:
            backup = path.with_name(path.name + '.keeper-backup-' + str(time.time_ns()))
            atomic(backup, before)
        if (path.read_bytes() if path.exists() else None) != before:
            raise ValueError('hooks.json изменён другим приложением. Повторите после сверки.')
        write_json(path, config)
    return 'Хук записан. Требуются доверие/перечитывание Codex; живое выполнение ещё не подтверждено.'


def remove_hook(path, cmd):
    path = Path(path).resolve()
    with lock(path.parent / '.context-keeper-hook-edit'):
        before = path.read_bytes()
        config = json.loads(before.decode('utf-8-sig'))
        groups = config['hooks'].get('SessionStart', [])
        for group in groups:
            group['hooks'] = [handler for handler in group['hooks'] if handler.get('command') != cmd]
        if path.read_bytes() != before:
            raise ValueError('hooks.json изменился; удаление отменено.')
        atomic(path.with_name(path.name + '.keeper-backup-' + str(time.time_ns())), before)
        write_json(path, config)
