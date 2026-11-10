"""Opt-in compaction delivery. Does not register itself or trigger compaction."""
from __future__ import annotations
import json
import os
from pathlib import Path
import time
import shlex
import sys
from core import Store, protected_threads, atomic, lock, read_json, thread_id, write_json, metadata, transcript_path


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
        # Codex may spell the same Windows file with or without the \\?\ prefix.
        # Compare file identity, still failing closed for a different/missing file.
        if event.get('transcript_path'):
            actual = transcript_path(metadata(store.home, tid)['source'])
            observed = transcript_path(event['transcript_path'])
            if actual is None or observed is None or not observed.samefile(actual):
                raise ValueError('Путь события отличается от зарегистрированного источника.')
        # Delivery must not wait for a multi-GB archive scan or its writer lock.
        text = store.render(tid, write_preview=False)
        return {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': text}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'systemMessage': 'Context Keeper: восстановление не выполнено; данные сохранены. ' + str(exc)}


def command(executable, data, home, *, resolve_paths=True):
    # Hook shell parsing is platform owned. Reject shell interpolation characters.
    values = [str(Path(p).resolve() if resolve_paths else Path(p).absolute()) for p in (executable, data, home)]
    if sys.platform != 'win32':
        return shlex.join([values[0], '--keeper-hook', '--data', values[1], '--codex-home', values[2]])
    if any(any(c in v for c in '"\r\n%`$!&|<>^') for v in values):
        raise ValueError('Путь содержит символы оболочки; переместите приложение/данные в простой путь.')
    invocation = f'"{values[0]}" --keeper-hook --data "{values[1]}" --codex-home "{values[2]}"'
    # Codex's native Windows command shell is PowerShell. A quoted executable
    # is a string expression there unless invoked with the call operator.
    return ('& ' if os.name == 'nt' else '') + invocation


def _keeper_handlers(config):
    groups = config.get('hooks', {}).get('SessionStart', [])
    if not isinstance(groups, list):
        raise ValueError('Неподдерживаемый SessionStart; готовность хука неизвестна.')
    found = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
            raise ValueError('Неподдерживаемая группа хуков; готовность неизвестна.')
        for handler in group['hooks']:
            if not isinstance(handler, dict) or not isinstance(handler.get('command', ''), str):
                raise ValueError('Неподдерживаемый обработчик; готовность неизвестна.')
            if '--keeper-hook' in handler['command']:
                found.append((group, handler))
    if len(found) > 1:
        raise ValueError('Несколько обработчиков Context Keeper; нужна ручная сверка.')
    return found


def _command_state(group, handler, cmd):
    if (handler.get('type') != 'command' or group.get('matcher') != '^compact$'
            or 'commandWindows' in handler or 'command_windows' in handler):
        raise ValueError('Некорректный Context Keeper hook; нужна ручная сверка.')
    if handler['command'] == cmd:
        return 'current'
    if os.name == 'nt' and cmd.startswith('& ') and handler['command'] == cmd[2:]:
        return 'legacy'
    raise ValueError('Найден другой Context Keeper hook; нужна ручная сверка.')


def configured_hook(path, cmd):
    """Read-only check for this exact compact hook; never infer Codex trust."""
    path = Path(path)
    if not path.exists():
        return False
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    found = _keeper_handlers(config)
    return bool(found and _command_state(*found[0], cmd) == 'current')


def runtime_hook_status(response, cmd):
    """Interpret read-only Codex hooks/list; trusted is not a live delivery test."""
    contexts = response.get('data') if isinstance(response, dict) else None
    if not isinstance(contexts, list):
        raise ValueError('Codex не вернул список хуков.')
    matches = [hook for context in contexts if isinstance(context, dict)
        for hook in context.get('hooks', []) if isinstance(hook, dict) and hook.get('command') == cmd]
    if not matches:
        return 'не обнаружен Codex'
    if len(matches) != 1:
        return 'несколько совпадений; нужна ручная сверка'
    hook = matches[0]
    if hook.get('eventName') != 'sessionStart' or hook.get('matcher') != '^compact$':
        return 'неверное событие или фильтр'
    if not hook.get('enabled'):
        return 'отключён в Codex'
    if hook.get('trustStatus') != 'trusted':
        return 'ожидает доверия в Codex (/hooks)'
    return 'включён и доверен; живой возврат не подтверждён'


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
        found = _keeper_handlers(config)
        if found:
            if _command_state(*found[0], cmd) == 'current':
                return 'Уже установлен'
            # Migrate only this installation's exact pre-call-operator command.
            # Preserve timeout, status text, other hooks and per-chat selections.
            found[0][1]['command'] = cmd
        else:
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
            owned = (cmd, cmd[2:]) if os.name == 'nt' and cmd.startswith('& ') else (cmd,)
            group['hooks'] = [handler for handler in group['hooks'] if handler.get('command') not in owned]
        if path.read_bytes() != before:
            raise ValueError('hooks.json изменился; удаление отменено.')
        atomic(path.with_name(path.name + '.keeper-backup-' + str(time.time_ns())), before)
        write_json(path, config)
