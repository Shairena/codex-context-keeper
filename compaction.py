"""Durable after-turn requests; native dispatch requires explicit known-risk consent."""
from __future__ import annotations
import json
from pathlib import Path
import uuid
from contextlib import contextmanager
import os
from core import protected_threads, lock, now, read_json, thread_id, write_json

TERMINAL = {'completed', 'failed', 'cancelled', 'ambiguous'}


@contextmanager
def worker_lease(root):
    """OS-held cooperating-worker lock, automatically released on process death."""
    path = Path(root) / '.compaction-worker.lock'
    with path.open('a+b') as f:
        if f.seek(0, 2) == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                f.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class UnverifiedDesktopDriver:
    """Never resumes a competing owner or invokes an unverified disruptive RPC."""
    verified_atomic_idle_guard = False

    def snapshot(self, tid):
        raise ValueError('Живой Desktop owner и атомарная проверка idle пока не подтверждены. Сжатие не отправлено.')


class CompactionQueue:
    def __init__(self, store, driver=None):
        self.store = store
        self.path = store.root / 'compaction-requests.json'
        if driver is None:
            from native_driver import UnsafeNativeDriver
            driver = UnsafeNativeDriver(home=store.home)
        self.driver = driver

    def load(self):
        if not self.path.exists():
            return {'schema': 1, 'requests': {}}
        value = read_json(self.path)
        if value.get('schema') != 1 or not isinstance(value.get('requests'), dict):
            raise ValueError('Некорректное состояние очереди; сброс не выполнен.')
        return value

    def request(self, tid, origin_turn_id, bound_thread_id):
        tid = thread_id(tid)
        if tid != thread_id(bound_thread_id) or tid in protected_threads(self.store.home):
            raise ValueError('Инструмент привязан к другому/защищённому чату.')
        if not isinstance(origin_turn_id, str) or not origin_turn_id.strip() or len(origin_turn_id) > 200:
            raise ValueError('Нужен явный ID исходного turn; автоматическое удостоверение caller недоступно.')
        row = self.store.registry()['chats'].get(tid)
        if not row or not row.get('allow_compaction', False):
            raise ValueError('Чат не зарегистрирован или пользователь не разрешил запросы сжатия.')
        # Read-only archive synchronization, never real compaction in the tool call.
        self.store.sync(tid)
        found = False
        with (self.store.folder(tid) / 'source.jsonl').open(encoding='utf-8') as f:
            for line in f:
                obj = json.loads(line)
                payload = obj.get('payload', {})
                if obj.get('type') == 'event_msg' and payload.get('type') == 'task_started' and payload.get('turn_id') == origin_turn_id:
                    found = True
                elif obj.get('type') == 'turn_context' and payload.get('turn_id') == origin_turn_id:
                    found = True
        if not found:
            raise ValueError('Исходный turn не найден в доступном архиве выбранного чата. Запрос отклонён.')
        with lock(self.store.root):
            state = self.load()
            for item in state['requests'].values():
                if item['thread_id'] == tid and item['origin_turn_id'] == origin_turn_id:
                    return dict(item, deduplicated=True)
            rid = str(uuid.uuid4())
            item = dict(request_id=rid, thread_id=tid, origin_turn_id=origin_turn_id, status='queued',
                created=now(), updated=now(), detail='Ожидание завершения ответа. Ничего не сжато.',
                known_race_unsafe=True,
                identity_mode='explicit validated archive identity; stdio caller not authenticated')
            state['requests'][rid] = item
            write_json(self.path, state)
            return item

    def status(self, rid, bound_thread_id):
        item = self.load()['requests'][rid]
        if item['thread_id'] != thread_id(bound_thread_id):
            raise ValueError('Запрос другого чата.')
        return item

    def cancel(self, rid, bound_thread_id):
        with lock(self.store.root):
            state = self.load()
            item = state['requests'][rid]
            if item['thread_id'] != thread_id(bound_thread_id):
                raise ValueError('Запрос другого чата.')
            if item['status'] in ('dispatching', 'confirming', 'ambiguous'):
                raise ValueError('Отмена не может доказанно остановить уже отправленную/неоднозначную операцию.')
            if item['status'] not in TERMINAL:
                item.update(status='cancelled', updated=now(), detail='Отменён до отправки.')
                write_json(self.path, state)
            return item

    def _transition(self, rid, expected, **changes):
        with lock(self.store.root):
            state = self.load()
            item = state['requests'][rid]
            if item['status'] not in expected:
                return False
            item.update(changes, updated=now())
            write_json(self.path, state)
            return True

    def tick(self):
        with worker_lease(self.store.root) as acquired:
            if acquired:
                self._tick()

    def _native_tick(self, rid, item):
        """Consent + fresh owner checks reduce, but explicitly do NOT remove, the race."""
        allowed = {'queued', 'deferred', 'waiting'}
        tid = item['thread_id']
        try:
            if tid in protected_threads(self.store.home):
                raise ValueError('Защищённый чат.')
            before = self.driver.inspect(tid, item['origin_turn_id'])
            if before.get('disposition') != 'observed' or before.get('threadId') != tid or not before.get('ownerId'):
                self._transition(rid, allowed, status='deferred', detail=before.get('detail', 'Owner не подтверждён; ничего не отправлено.'))
                return
            if not before.get('idle') or not before.get('originCompleted'):
                self._transition(rid, allowed, status='waiting', detail='Ожидается завершение исходного ответа и живой idle.')
                return
            self.store.sync(tid)
            self.store.render(tid)
            # One short state lock rechecks consent/cancellation and commits intent.
            with lock(self.store.root):
                state = self.load()
                current = state['requests'][rid]
                row = self.store.registry()['chats'].get(tid, {})
                if current['status'] not in allowed:
                    return
                stop = getattr(self, 'shutdown_event', None)
                if stop is not None and stop.is_set():
                    return  # Normal close cancels new dispatch, not an already sent native operation.
                if not row.get('allow_compaction') or not row.get('unsafe_native_compaction'):
                    current.update(status='deferred', updated=now(), detail='Небезопасный реальный запуск не разрешён; ничего не отправлено.')
                    write_json(self.path, state)
                    return
                current.update(status='dispatching', updated=now(), owner_id=before['ownerId'],
                    known_race_unsafe=True, detail='НЕБЕЗОПАСНО: новое сообщение может прервать ответ в гонке. Намерение записано до RPC; ACK не завершение.')
                write_json(self.path, state)
            result = self.driver.run_native(tid, item['origin_turn_id'], before['ownerId'])
            disposition = result.get('disposition')
            if disposition == 'not_started':
                self._transition(rid, {'dispatching'}, status='waiting', detail=result.get('detail', 'Последняя проверка не разрешила запуск; RPC не отправлен.'))
            elif disposition == 'completed' and result.get('threadId') == tid and result.get('ownerId') == before['ownerId']:
                # Native completion committed BEFORE optional archive refresh; a disk error must not cause a resend.
                self._transition(rid, {'dispatching'}, status='completed', native_result=result,
                    detail='Сжатие завершено по новому item/turn и idle. Риск прерывания нового ответа сохраняется.')
                try:
                    self.store.sync(tid)
                except Exception as exc:
                    self._transition(rid, {'completed'}, archive_warning=str(exc))
            else:
                self._transition(rid, {'dispatching'}, status='ambiguous', native_result=result,
                    detail=result.get('detail', 'Нет подтверждения завершения.') + ' Автоматический повтор запрещён.')
        except Exception as exc:
            current = self.load()['requests'][rid]['status']
            if current in TERMINAL:
                return
            self._transition(rid, {current}, status='ambiguous' if current == 'dispatching' else 'deferred',
                detail=str(exc) + (' Повтор запрещён.' if current == 'dispatching' else ' Сжатие не отправлено.'))

    def _tick(self):
        """UI/MCP worker. Never interprets stale rollout metrics as live idle."""
        for rid, item in self.load()['requests'].items():
            stop = getattr(self, 'shutdown_event', None)
            if stop is not None and stop.is_set():
                return
            if item['status'] in TERMINAL:
                continue
            if item['status'] in ('dispatching', 'confirming'):
                # Another process may be handling it. Never dispatch twice after a restart.
                self._transition(rid, {'dispatching', 'confirming'}, status='ambiguous',
                    detail='Предыдущая отправка могла состояться. Автоматический повтор запрещён; нужна ручная сверка.')
                continue
            row = self.store.registry()['chats'].get(item['thread_id'])
            if not row or not row.get('allow_compaction'):
                self._transition(rid, {'queued', 'deferred', 'waiting'}, status='cancelled', detail='Разрешение пользователя снято.')
                continue
            if getattr(self.driver, 'unsafe_driver', False):
                if not row.get('unsafe_native_compaction'):
                    self._transition(rid, {'queued', 'deferred', 'waiting'}, status='deferred',
                        detail='Реальное небезопасное сжатие выключено. Включите отдельное согласие для этого чата.')
                else:
                    self._native_tick(rid, item)
                continue
            if not self.driver.verified_atomic_idle_guard:
                self._transition(rid, {'queued', 'deferred', 'waiting'}, status='deferred',
                    detail='Живой Desktop owner/атомарный idle guard не подтверждены. Ни одного compact RPC не отправлено.')
                continue
            try:
                before = self.driver.snapshot(item['thread_id'])
                if before['thread_id'] != item['thread_id'] or not before.get('owner_id'):
                    raise ValueError('Не подтверждён владелец именно этого чата.')
                completed = before.get('completed_turns', [])
                if item['origin_turn_id'] not in completed or not before.get('idle'):
                    self._transition(rid, {'queued', 'deferred', 'waiting'}, status='waiting',
                        detail='Исходный turn ещё не завершён или началась другая работа. Сжатие не отправлено.')
                    continue
                self.store.sync(item['thread_id'])
                self.store.render(item['thread_id'])
                # The driver's contract MUST atomically reject changed owner/revision/non-idle.
                if not self._transition(rid, {'queued', 'deferred', 'waiting'}, status='dispatching',
                        owner_id=before['owner_id'], revision=before['revision'], detail='Запись намерения до RPC; ACK не завершение.'):
                    continue
                receipt = self.driver.guarded_start(item['thread_id'], before['owner_id'], before['revision'])
                if receipt.get('deferred'):
                    self._transition(rid, {'dispatching'}, status='waiting', detail='Owner отказал из-за новой работы; ничего не запущено.')
                    continue
                if receipt.get('owner_id') != before['owner_id'] or not receipt.get('accepted'):
                    raise ValueError('Ответ запуска неоднозначен.')
                self._transition(rid, {'dispatching'}, status='confirming', detail='ACK получен; ожидается завершение contextCompaction.')
                after = self.driver.confirm(item['thread_id'], before['owner_id'], before.get('compaction_ids', []))
                if after.get('thread_id') != item['thread_id'] or after.get('owner_id') != before['owner_id'] or not (
                        after.get('new_item_completed') and after.get('turn_completed') and after.get('idle')):
                    raise ValueError('Завершение нового item, turn и idle не подтверждено.')
                self.store.sync(item['thread_id'])
                self._transition(rid, {'confirming'}, status='completed', detail='Новый contextCompaction и завершённый turn подтверждены текущим owner.')
            except Exception as exc:
                current = self.load()['requests'][rid]['status']
                if current in TERMINAL:
                    continue  # Concurrent cancellation/completion is never resurrected.
                state = 'ambiguous' if current in ('dispatching', 'confirming') else 'deferred'
                self._transition(rid, {current}, status=state, detail=str(exc) + (' Повтор запрещён.' if state == 'ambiguous' else ''))
