"""Live Desktop observations and at-most-once context reminders; never compact."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

from core import file_lease, now, protected_threads, read_json, thread_id, write_json
from native_driver import UnsafeNativeDriver


def load_warning_state(root):
    path = root / 'context-warnings.json'
    if not path.exists():
        return {'schema': 1, 'chats': {}}
    value = read_json(path)
    if value.get('schema') != 1 or not isinstance(value.get('chats'), dict):
        raise ValueError('Некорректное состояние предупреждений; автоматический сброс запрещён.')
    return value


def observation(tid, value):
    used, window = value.get('usedTokens'), value.get('contextWindow')
    available = (value.get('disposition') == 'observed' and value.get('threadId') == tid
        and bool(value.get('ownerId')) and type(used) is int and type(window) is int
        and 0 <= used <= window and window > 0)
    return {'thread_id': tid, 'available': available, 'observed_at': now(),
        'source': 'Codex Desktop owner snapshot', 'idle': value.get('idle') is True,
        'used_tokens': used if available else None, 'window_tokens': window if available else None,
        'remaining_tokens': window - used if available else None,
        'percent': round(used * 100 / window, 2) if available else None,
        'owner_id': value.get('ownerId'), 'context_key': value.get('contextKey'),
        'detail': value.get('detail', '') if not available else ''}


def observe_context(store, tid, driver=None):
    tid = thread_id(tid)
    if tid not in store.registry()['chats'] or tid in protected_threads(store.home):
        raise ValueError('Нужен зарегистрированный незащищённый чат.')
    return observation(tid, (driver or UnsafeNativeDriver(home=store.home)).observe(tid))


def counter_text(value):
    if not value or not value.get('available'):
        return 'Живой счётчик: нет живых данных (чат не загружен или Codex не отдал метрику).'
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['observed_at'])).total_seconds()
    except (ValueError, KeyError, TypeError):
        age = 91
    if not 0 <= age <= 90:
        return 'Живой счётчик: наблюдение устарело; обновите метрику.'
    return (f"Последнее живое наблюдение: занято {value['used_tokens']:,} / {value['window_tokens']:,}; "
        f"осталось {value['remaining_tokens']:,}; заполнено {value['percent']}%.\n"
        f"Измерено: {value['observed_at']}. Это не прогноз размера следующего запроса.")


class ContextWarnings:
    def __init__(self, store, driver=None, stop=None):
        self.store = store
        self.driver = driver or UnsafeNativeDriver(home=store.home, data=store.root)
        self.stop = stop or threading.Event()
        self.path = store.root / 'context-warnings.json'

    def tick(self):
        with file_lease(self.store.root, '.context-warning-worker.lock') as acquired:
            if not acquired:
                return
            state = load_warning_state(self.store.root)
            rows = self.store.registry()['chats']
            protected = protected_threads(self.store.home)
            state['chats'] = {tid: record for tid, record in state['chats'].items() if tid in rows}
            for record in state['chats'].values():
                if record.get('status') == 'dispatching':
                    record.update(status='ambiguous', detail='Предыдущая отправка могла состояться; автоматический повтор запрещён.')
            ids = [tid for tid in rows if tid not in protected]
            def read(tid):
                if self.stop.is_set():
                    return tid, None
                try:
                    return tid, observation(tid, self.driver.observe(tid))
                except Exception as exc:
                    return tid, observation(tid, {'detail': str(exc)})
            # Bound native processes and timeout unloaded owners; no thread/resume.
            with ThreadPoolExecutor(max_workers=4) as pool:
                for tid, value in pool.map(read, ids):
                    if value is None:
                        continue
                    record = state['chats'].setdefault(tid, {})
                    record['observation'] = value
                    row = self.store.registry()['chats'].get(tid, {})
                    percent = row.get('context_warning_percent', 90)
                    if (self.stop.is_set() or tid in protected_threads(self.store.home) or not row.get('context_warning_enabled') or not value['available']
                            or not value['idle'] or not value['context_key']
                            or value['used_tokens'] * 100 < value['window_tokens'] * percent):
                        continue
                    key = value['context_key'] + ':' + str(percent)
                    if record.get('intent_context') == key and record.get('status') in ('dispatching', 'sent', 'ambiguous'):
                        continue
                    record.update(intent_context=key, status='dispatching', updated=now())
                    write_json(self.path, state)  # Durable intent before a message can be accepted.
                    try:
                        current = self.store.registry()['chats'].get(tid, {})
                        if (self.stop.is_set() or tid in protected_threads(self.store.home)
                                or not current.get('context_warning_enabled') or current.get('context_warning_percent', 90) != percent):
                            record.update(status='not_started', detail='Разрешение изменилось до отправки.')
                            write_json(self.path, state)
                            continue
                        result = self.driver.remind(tid, value['owner_id'], percent, value['context_key'])
                        disposition = result.get('disposition')
                        if disposition == 'accepted' and result.get('threadId') == tid and result.get('turnId'):
                            record.update(status='sent', turn_id=result['turnId'], detail='Сообщение принято Codex.')
                        elif disposition == 'not_started':
                            record.update(status='not_started', detail=result.get('detail', 'Отложено: чат занят или метрика изменилась.'))
                        else:
                            record.update(status='ambiguous', detail='Результат отправки не подтверждён; автоматический повтор запрещён.')
                    except Exception as exc:
                        record.update(status='ambiguous', detail=str(exc) + ' Автоматический повтор запрещён.')
                    record['updated'] = now()
                    write_json(self.path, state)
            write_json(self.path, state)
