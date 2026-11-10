"""Russian desktop UI; automatic work is read-only archive synchronization."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from core import KEYS, atomic, lock, read_json, write_json, transcript_path
from native import ContextSettings, Rpc, parse_limit
from hooks import command, configured_hook, install_hook, remove_hook, runtime_hook_status
from context_warnings import ContextWarnings, load_warning_state, counter_text
from version import VERSION
from platform_support import hook_executable, open_document


class ChatPicker(tk.Toplevel):
    def __init__(self, parent, catalog):
        super().__init__(parent)
        self.title('Выбор чатов для Context Keeper')
        self.geometry('920x650')
        self.transient(parent)
        self.result = None
        self.chosen, self.members, self.labels = set(), {}, {}
        ttk.Label(self, text='Отметьте проект целиком или отдельные чаты. ОК подключит выбранные; '
            'Отмена ничего не меняет. Чаты без названия или файла скрыты; защищённые выбрать нельзя.', wraplength=880).pack(padx=12, pady=10, anchor='w')
        pane = ttk.Frame(self)
        pane.pack(fill='both', expand=True, padx=12)
        self.tree = ttk.Treeview(pane, show='tree', selectmode='browse')
        scroll = ttk.Scrollbar(pane, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.tag_configure('unavailable', foreground='#777777')
        for index, group in enumerate(catalog['groups']):
            gid = f'group:{index}'
            self.labels[gid] = f"{group['name']} — {len(group['ids'])} чатов"
            self.members[gid] = {tid for tid in group['ids'] if not catalog['chats'][tid]['reason']}
            self.tree.insert('', 'end', iid=gid, open=False)
            for tid in sorted(group['ids'], key=lambda tid: (catalog['chats'][tid]['title'].casefold(), tid)):
                row = catalog['chats'][tid]
                self.labels[tid] = row['title'][:160] + (f" — {row['reason']}" if row['reason'] else '')
                self.members[tid] = set() if row['reason'] else {tid}
                self.tree.insert(gid, 'end', iid=tid, tags=('unavailable',) if row['reason'] else ())
        self.tree.bind('<ButtonRelease-1>', self.click_row)
        self.tree.bind('<space>', lambda _: self.toggle(self.tree.focus()))
        bottom = ttk.Frame(self)
        bottom.pack(fill='x', padx=12, pady=12)
        self.counter = tk.StringVar()
        ttk.Label(bottom, textvariable=self.counter).pack(side='left')
        ttk.Button(bottom, text='Отмена', command=self.destroy).pack(side='right')
        ttk.Button(bottom, text='ОК', command=self.accept).pack(side='right', padx=8)
        self.refresh_marks()
        self.grab_set()

    def click_row(self, event):
        if 'indicator' not in self.tree.identify('element', event.x, event.y):
            self.toggle(self.tree.identify_row(event.y))

    def toggle(self, iid):
        members = self.members.get(iid, set())
        if members <= self.chosen:
            self.chosen.difference_update(members)
        else:
            self.chosen.update(members)
        self.refresh_marks()
        return 'break'

    def refresh_marks(self):
        for iid, members in self.members.items():
            mark = '—' if not members else '☑' if members <= self.chosen else '◩' if members & self.chosen else '☐'
            self.tree.item(iid, text=mark + ' ' + self.labels[iid])
        self.counter.set(f'Выбрано чатов: {len(self.chosen)}')

    def accept(self):
        self.result = sorted(self.chosen)
        self.destroy()


class Application(tk.Tk):
    def __init__(self, store, smoke=False):
        super().__init__()
        self.store, self.smoke = store, smoke
        self.settings = None
        self.pending_native_read = None
        self.work = queue.Queue()
        self.busy = False
        self.pending_close = False
        self.stop_dispatch = threading.Event()
        self.warning_refresh = threading.Event()
        self.warning_error = ''
        self.last_counter_update = 0
        self.protocol('WM_DELETE_WINDOW', self.request_close)
        self.title('Хранитель контекста Codex — ' + VERSION)
        self.minsize(960, 680)
        self.resizable(True, True)
        self.normal_size = (1120, 790)
        self.restore_window_size()
        self.bind('<Configure>', self.remember_window_size)
        self.option_add('*Font', ('Segoe UI', 10))
        style = ttk.Style(self)
        style.theme_use('vista' if 'vista' in style.theme_names() else 'clam')
        style.configure('Treeview', rowheight=30)
        self.status = tk.StringVar(value='Готово. Зарегистрированные чаты обновляются каждые 30 секунд, пока открыто приложение.')
        self.build()
        self.refresh()
        self.after(100, self.check_work)
        if smoke:
            self.after(700, self.destroy)
        else:
            self.after(30000, self.periodic)
            def monitor():
                warnings = ContextWarnings(self.store, stop=self.stop_dispatch)
                while not self.stop_dispatch.is_set():
                    try:
                        warnings.tick()
                        self.warning_error = ''
                    except Exception as exc:
                        self.warning_error = str(exc)
                    self.warning_refresh.wait(30)
                    self.warning_refresh.clear()
            threading.Thread(target=monitor, daemon=True).start()

    def build(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=12, pady=10)
        chats, context, integrations, live = (ttk.Frame(notebook, padding=12) for _ in range(4))
        notebook.add(chats, text='Чаты и документы')
        notebook.add(context, text='Контекст Codex — общие настройки')
        notebook.add(live, text='Живой контекст и предупреждения')
        notebook.add(integrations, text='Восстановление после сжатия')
        top = ttk.Frame(chats)
        top.pack(fill='x')
        ttk.Label(top, text='ID чата / codex://threads/…').pack(side='left')
        self.add_id = ttk.Entry(top, width=43)
        self.add_id.pack(side='left', padx=8)
        ttk.Button(top, text='Добавить чат', command=self.add_chat).pack(side='left')
        ttk.Button(top, text='Выбрать чаты…', command=lambda: self.guard(self.pick_chats)).pack(side='left', padx=8)
        ttk.Button(top, text='Обновить архивы', command=self.sync_all).pack(side='left', padx=8)
        self.tree = ttk.Treeview(chats, columns=('history', 'memo', 'notes', 'auto', 'status'), show='tree headings', height=7, selectmode='extended')
        self.tree.heading('#0', text='Проект / чат')
        self.tree.column('#0', width=255, minwidth=180)
        for key, label, width in [('history', 'История', 75), ('memo', 'Мемо', 65),
                ('notes', 'Заметки', 75), ('auto', 'Автовозврат', 105), ('status', 'Состояние архива', 290)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(fill='x', pady=10)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self.show_selected())
        self.tree.bind('<ButtonRelease-1>', self.toggle_cell)
        ttk.Button(chats, text='Отключить всех, кроме выделенных…',
            command=lambda: self.guard(self.disconnect_others)).pack(anchor='w', pady=(0, 6))
        ttk.Label(chats, text='Флажки: История — целые закладки и выбранные результаты поиска; Мемо/Заметки — целый документ.\n'
            'Полный архив всегда сохраняется на диск. Флажки не отправляют весь архив модели.', wraplength=1030).pack(anchor='w')
        self.details = tk.StringVar(value='Выберите чат. Папка закреплена за ID и не переименовывается автоматически.')
        ttk.Label(chats, textvariable=self.details, wraplength=1030).pack(anchor='w', pady=7)
        actions = ttk.Frame(chats)
        actions.pack(fill='x')
        for label, fn in [('Папка', lambda: self.open_file(None)), ('История', lambda: self.open_file('history.md')),
            ('Мемо', lambda: self.open_file('memo.md')), ('Заметки', lambda: self.open_file('notes.md')),
            ('Импорт документа', self.import_doc), ('Закладки', self.bookmarks), ('Предпросмотр возврата', self.preview)]:
            ttk.Button(actions, text=label, command=lambda f=fn: self.guard(f)).pack(side='left', padx=(0, 5))
        search = ttk.LabelFrame(chats, text='Поиск по исходной переписке выбранного чата', padding=8)
        search.pack(fill='both', expand=True, pady=10)
        bar = ttk.Frame(search)
        bar.pack(fill='x')
        self.query = ttk.Entry(bar)
        self.query.pack(side='left', fill='x', expand=True)
        ttk.Button(bar, text='Найти', command=self.search).pack(side='left', padx=5)
        ttk.Button(bar, text='Показать выбранное', command=lambda: self.guard(self.view_result)).pack(side='left')
        ttk.Button(bar, text='Закрепить цитату', command=lambda: self.guard(self.pin_result)).pack(side='left', padx=4)
        ttk.Button(bar, text='В возврат выбранное', command=lambda: self.guard(lambda: self.preview(self.selected_results()))).pack(side='left', padx=5)
        self.results = ttk.Treeview(search, columns=('role', 'time', 'text'), show='headings', height=5, selectmode='extended')
        for key, label, width in [('role', 'Автор', 100), ('time', 'Время', 170), ('text', 'Фрагмент (полный текст по кнопке)', 640)]:
            self.results.heading(key, text=label)
            self.results.column(key, width=width)
        self.results.pack(fill='both', expand=True, pady=5)
        self.results.bind('<Double-1>', lambda _: self.guard(self.view_result))
        self.search_hits, self.search_thread = [], None

        ttk.Label(context, text='Эти два переопределения общие для Codex, включая непривязанные чаты.\n'
            'Сохранение не перезапускает и не сжимает чаты. Применение к существующей сессии не гарантируется.',
            wraplength=1020).pack(anchor='w', pady=(0, 18))
        fields = ttk.Frame(context)
        fields.pack(fill='x')
        self.window, self.compact = ttk.Entry(fields, width=25), ttk.Entry(fields, width=25)
        for i, (label, field) in enumerate([('Максимум контекста Codex (токены)', self.window), ('Автопорог Codex (токены)', self.compact)]):
            ttk.Label(fields, text=label).grid(row=i, column=0, sticky='w', pady=6)
            field.grid(row=i, column=1, padx=14, sticky='w')
        self.native_info = tk.StringVar(value='Настройки не прочитаны. Пустое поле = удалить переопределение, не нулевой лимит.')
        ttk.Label(context, textvariable=self.native_info, wraplength=1020).pack(anchor='w', pady=15)
        buttons = ttk.Frame(context)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Прочитать из Codex', command=self.read_native).pack(side='left')
        ttk.Button(buttons, text='Сохранить', command=lambda: self.guard(self.save_native)).pack(side='left', padx=8)
        ttk.Button(buttons, text='Вернуть стандартные', command=lambda: self.guard(self.reset_native)).pack(side='left')
        ttk.Label(live, text='Выбранный чат: живая метрика Codex', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.live_counter = tk.StringVar(value='Выберите подключённый чат на вкладке «Чаты и документы».')
        ttk.Label(live, textvariable=self.live_counter, wraplength=1000).pack(anchor='w', pady=12)
        ttk.Button(live, text='Обновить живые счётчики', command=self.warning_refresh.set).pack(anchor='w')
        warning = ttk.LabelFrame(live, text='Сообщение-напоминание в чат', padding=10)
        warning.pack(fill='x', pady=18)
        self.warning_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(warning, text='Напоминать перед заполнением контекста', variable=self.warning_enabled).pack(anchor='w')
        controls = ttk.Frame(warning)
        controls.pack(fill='x', pady=8)
        ttk.Label(controls, text='Порог, %:').pack(side='left')
        self.warning_percent = ttk.Entry(controls, width=6)
        self.warning_percent.insert(0, '90')
        self.warning_percent.pack(side='left', padx=8)
        ttk.Button(controls, text='Сохранить для чата', command=lambda: self.guard(self.save_context_warning)).pack(side='left')
        ttk.Button(controls, text='Применить ко всем подключённым…', command=lambda: self.guard(lambda: self.save_context_warning(all_chats=True))).pack(side='left', padx=8)
        ttk.Label(warning, text='Наблюдение работает, пока Keeper открыт, отдельно от обновления архивов.\n'
            'Напоминание отправляется, когда Codex сообщает, что чат свободен. Сжатие не запускается.\n'
            'Незагруженные чаты не открываются; при неизвестной метрике сообщение не отправляется.', wraplength=970).pack(anchor='w')
        ttk.Separator(live).pack(fill='x', pady=15)
        ttk.Label(live, text='Последняя запись в архиве (может устареть)').pack(anchor='w')
        self.observation = tk.StringVar(value='Нет выбранного чата / нет доступной метрики.')
        ttk.Label(live, textvariable=self.observation, wraplength=1020).pack(anchor='w', pady=10)
        prep = ttk.Frame(live)
        prep.pack(fill='x')
        ttk.Label(prep, text='Личный предупредительный порог (токены)').pack(side='left')
        self.threshold = ttk.Entry(prep, width=20)
        self.threshold.pack(side='left', padx=10)
        ttk.Button(prep, text='Сохранить для выбранного чата', command=lambda: self.guard(self.save_threshold)).pack(side='left')
        ttk.Label(live, text='Это только предупреждение по последней доступной метрике. Оно не изменяет окно модели и не запускает сжатие.\n'
            'Протокол допускает config у thread/start и thread/resume; изменение лимитов живого Desktop-чата не проверено.\n'
            'Большой максимум — вместимость. Расход зависит от реально передаваемого контекста.', wraplength=1020).pack(anchor='w', pady=15)

        ttk.Label(integrations, text='По умолчанию восстановление отключено. Для автоматического возврата нужны:\n'
            '1. Выбранные документы и «Автовозврат» у нужного чата.\n'
            '2. Явная установка хука и доверие к нему в Codex.\n'
            '3. Проверка фактического выполнения после естественного сжатия. Живая доставка пока не подтверждена.', wraplength=1010).pack(anchor='w')
        ttk.Label(integrations, text='Лимит возврата для выбранного чата (символы, 1000–32000)').pack(anchor='w', pady=(18, 4))
        self.restore_limit = ttk.Entry(integrations, width=20)
        self.restore_limit.pack(anchor='w')
        ttk.Button(integrations, text='Сохранить лимит', command=lambda: self.guard(self.save_restore_limit)).pack(anchor='w', pady=8)
        ttk.Label(integrations, text='Целые записи, без обрезания. Не вошедшие материалы перечисляются в предпросмотре.\n'
            'Хук получает developer-контекст; документы внутри явно обозначаются цитируемыми данными, а не инструкциями.\n'
            'Напоминания во включённых чатах — отдельная функция; сами они не запускают сжатие.', wraplength=1010).pack(anchor='w', pady=10)
        ttk.Label(integrations, text='Файл hooks.json (существующие обработчики сохраняются)').pack(anchor='w', pady=(14, 4))
        self.hook_path = ttk.Entry(integrations, width=105)
        self.hook_path.insert(0, str(self.store.home / 'hooks.json'))
        self.hook_path.pack(fill='x')
        self.hook_state = tk.StringVar(value='Подключение не проверено. Установка и доверие к хуку — разные шаги.')
        ttk.Label(integrations, textvariable=self.hook_state, wraplength=1010).pack(anchor='w', pady=4)
        hb = ttk.Frame(integrations)
        hb.pack(fill='x', pady=12)
        ttk.Button(hb, text='Установить хук…', command=lambda: self.guard(self.install)).pack(side='left')
        ttk.Button(hb, text='Проверить подключение (без сжатия)', command=lambda: self.guard(self.check_hook)).pack(side='left', padx=8)
        ttk.Button(hb, text='Удалить только наш хук…', command=lambda: self.guard(self.uninstall)).pack(side='left', padx=8)
        ttk.Label(integrations, text='Чаты можно исключить локальным списком защиты.\n'
            'Папки чатов изолированы по полному ID. Источники Codex открываются только для чтения.', wraplength=1010).pack(anchor='w', pady=10)
        cb = ttk.Frame(integrations)
        cb.pack(fill='x', pady=8)
        ttk.Button(cb, text='Самосжатие: разрешить / запретить запросы чату…', command=lambda: self.guard(self.toggle_compaction)).pack(side='left')
        ttk.Button(cb, text='Очередь сжатия', command=lambda: self.guard(self.show_queue)).pack(side='left', padx=8)
        ttk.Button(cb, text='MCP-конфигурация', command=lambda: self.guard(self.mcp_config)).pack(side='left')
        ttk.Button(integrations, text='Удалённое сжатие: разрешить / запретить запуск…',
            command=lambda: self.guard(self.toggle_unsafe)).pack(anchor='w', pady=6)
        ttk.Label(integrations, text='Самосжатие и удалённый запуск по умолчанию выключены.\n'
            'Для выполнения нужны оба разрешения; включаются отдельно для выбранного чата.',
            wraplength=1010).pack(anchor='w')
        activity = ttk.Frame(self)
        activity.pack(fill='x', padx=12)
        self.progress = ttk.Progressbar(activity, mode='indeterminate', length=150)
        self.progress.pack(side='left', padx=(0, 10))
        self.elapsed = tk.StringVar(value='')
        ttk.Label(activity, textvariable=self.elapsed).pack(side='left')
        ttk.Label(self, textvariable=self.status, wraplength=1090, padding=(12, 4)).pack(fill='x')

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror('Хранитель контекста', str(exc), parent=self)

    def request_close(self):
        self.stop_dispatch.set()
        self.warning_refresh.set()
        if not self.smoke:
            self.save_window_size()
        if self.busy:
            self.pending_close = True
            self.stop_dispatch.set()
            self.status.set('Закрытие после завершения текущей операции; уже отправленное сжатие не отменяется.')
        else:
            self.destroy()

    def restore_window_size(self):
        path = self.store.root / 'window.json'
        try:
            prefs = read_json(path) if path.exists() else {}
            width, height = prefs.get('width', 1120), prefs.get('height', 790)
            if type(width) is not int or type(height) is not int:
                raise ValueError('Invalid window dimensions')
            width = max(960, min(width, self.winfo_screenwidth()))
            height = max(680, min(height, self.winfo_screenheight()))
            self.normal_size = (width, height)
            maximized = prefs.get('maximized') is True
        except (OSError, ValueError, AttributeError, TypeError):
            maximized = False
        self.geometry(f'{self.normal_size[0]}x{self.normal_size[1]}')
        if maximized and os.name == 'nt':
            self.state('zoomed')

    def remember_window_size(self, event):
        if event.widget is self and self.state() == 'normal':
            size = (self.winfo_width(), self.winfo_height())
            if size[0] >= 960 and size[1] >= 680:
                self.normal_size = size

    def save_window_size(self):
        try:
            write_json(self.store.root / 'window.json', {'width': self.normal_size[0],
                'height': self.normal_size[1], 'maximized': self.state() == 'zoomed'})
        except OSError as exc:
            self.status.set('Не удалось сохранить размер окна: ' + str(exc))

    def run(self, fn, done=None):
        if self.busy:
            self.status.set('Дождитесь завершения текущей операции.')
            return
        self.busy = True
        self.started_at = time.monotonic()
        self.progress.start(20)
        self.status.set('Выполняется…')
        def worker():
            try:
                self.work.put((True, fn(), done))
            except Exception as exc:
                self.work.put((False, str(exc), None))
        threading.Thread(target=worker, daemon=True).start()

    def check_work(self):
        try:
            for _ in range(100):
                ok, value, done = self.work.get_nowait()
                if ok is None:
                    if not self.pending_close:
                        self.status.set(value['message'])
                        if value['connected_id']:
                            self.refresh()
                            self.reveal_chats([value['connected_id']])
                    continue
                self.busy = False
                self.progress.stop()
                self.elapsed.set(f'Завершено за {int(time.monotonic() - self.started_at)} с')
                if self.pending_close:
                    self.destroy()
                    return
                self.refresh()
                if ok:
                    self.status.set('Готово.')
                    if done:
                        self.guard(lambda: done(value))
                else:
                    self.status.set('Ошибка: ' + value)
        except queue.Empty:
            pass
        except Exception as exc:
            self.status.set('Ошибка обновления интерфейса: ' + str(exc))
        if not self.busy and not self.pending_close and self.pending_native_read is not None:
            self.read_native()
        if time.monotonic() - self.last_counter_update >= 1:
            self.last_counter_update = time.monotonic()
            self.refresh_live_counter()
        if self.busy:
            self.elapsed.set(f'Прошло {int(time.monotonic() - self.started_at)} с')
        try:
            if (self.store.root / 'registry.json').stat().st_mtime_ns != self.registry_stamp:
                self.refresh()
        except Exception as exc:
            self.status.set('Не удалось обновить список: ' + str(exc))
        self.after(100, self.check_work)

    def report_progress(self, index, total, title, stage, connected_id=None):
        self.work.put((None, {'message': f'{index}/{total}: {title} — {stage}', 'connected_id': connected_id}, None))

    def reveal_chats(self, ids):
        visible = [tid for tid in ids if self.tree.exists(tid)]
        for tid in visible:
            self.tree.item(self.tree.parent(tid), open=True)
        if visible:
            self.tree.selection_set(visible)
            self.tree.see(visible[0])

    def selected(self):
        ids = self.tree.selection()
        if not ids or ids[0] not in self.store.registry()['chats']:
            raise ValueError('Сначала выберите чат.')
        return ids[0]

    def refresh(self):
        stamp = (self.store.root / 'registry.json').stat().st_mtime_ns
        selected = self.tree.selection()
        rows = self.store.registry()['chats']
        opened = {iid: bool(self.tree.item(iid, 'open')) for iid in self.tree.get_children()}
        projects = self.store.projects()
        parents = {tid: 'project:' + pid for pid, project in projects.items() for tid in project['ids']}
        groups = sorted([('project:' + pid, project['name']) for pid, project in projects.items()], key=lambda x: x[1].casefold())
        groups.append(('project:outside', 'Чаты вне проекта'))
        self.tree.delete(*self.tree.get_children())
        for gid, title in groups:
            members = [tid for tid in rows if parents.get(tid, 'project:outside') == gid]
            if not members:
                continue
            self.tree.insert('', 'end', iid=gid, text=f'{title} ({len(members)})', open=opened.get(gid, True))
            for tid in sorted(members, key=lambda tid: (rows[tid]['title'].casefold(), tid)):
                row = rows[tid]
                vals = ['☑' if row['selected'][key] else '☐' for key in KEYS]
                vals += ['☑' if row['auto_restore'] else '☐', row['status']]
                self.tree.insert(gid, 'end', iid=tid, text=row['title'], values=vals)
        if selected and selected[0] in rows:
            self.tree.selection_set(selected[0])
        self.registry_stamp = stamp

    def show_selected(self):
        if not self.tree.selection() or self.tree.selection()[0] not in self.store.registry()['chats']:
            return
        tid = self.selected()
        row = self.store.registry()['chats'][tid]
        self.warning_enabled.set(row.get('context_warning_enabled', False))
        self.warning_percent.delete(0, 'end')
        self.warning_percent.insert(0, str(row.get('context_warning_percent', 90)))
        self.refresh_live_counter()
        self.details.set(f"ID: {tid}\nПапка: {self.store.folder(tid)}\n"
            f"Самосжатие (запросы): {'включено' if row.get('allow_compaction') else 'выключено'}; "
            f"удалённый запуск: {'включён' if row.get('unsafe_native_compaction') else 'выключен'}.")
        self.threshold.delete(0, 'end')
        if row.get('preparation_threshold'):
            self.threshold.insert(0, str(row['preparation_threshold']))
        self.restore_limit.delete(0, 'end')
        self.restore_limit.insert(0, str(row['limit']))
        obs = self.store.observed(tid)
        info = obs.get('info', {})
        window = info.get('model_context_window', 'нет точных данных')
        last = info.get('last_token_usage') or {}
        used = last.get('total_tokens', 'нет точных данных')
        self.observation.set(f"{row['title']}: окно {window}; использовано {used}; время измерения {obs.get('timestamp', 'неизвестно')}.\n"
            'Это последняя локальная запись, не гарантированно живое значение. Порог автосжатия сессии: нет подтверждённых данных.' +
            ('\nПредупредительный порог достигнут.' if type(used) is int and row.get('preparation_threshold') and used >= row['preparation_threshold'] else ''))
        if self.search_thread != tid:
            self.results.delete(*self.results.get_children())
            self.search_hits = []
            self.search_thread = None

    def toggle_cell(self, event):
        if self.busy:
            return
        tid, col = self.tree.identify_row(event.y), self.tree.identify_column(event.x)
        if tid not in self.store.registry()['chats'] or col not in ('#1', '#2', '#3', '#4'):
            return
        def change():
            row = self.store.registry()['chats'][tid]
            if col == '#4':
                if not row['auto_restore']:
                    if not any(row['selected'].values()):
                        raise ValueError('Сначала выберите хотя бы один документ для возврата.')
                    if not configured_hook(self.hook_path.get(), self.hook_command()) and not self.install():
                        return
                if not row['auto_restore'] and not messagebox.askyesno('Автовозврат', 'Разрешить возврат выбранных данных только этому чату после сжатия?\nХук должен быть доверен в Codex; эта галочка сама не подтверждает доставку.', parent=self):
                    return
                self.store.update(tid, auto_restore=not row['auto_restore'])
                if not row['auto_restore']:
                    self.hook_state.set('Хук найден; проверьте доверие через /hooks и затем статус подключения. Живая доставка не подтверждена.')
            else:
                chosen = dict(row['selected'])
                key = KEYS[int(col[1:]) - 1]
                chosen[key] = not chosen[key]
                self.store.update(tid, selected=chosen)
            self.refresh()
        self.guard(change)

    def disconnect_others(self):
        if self.busy:
            self.status.set('Дождитесь завершения текущей операции.')
            return
        rows = self.store.registry()['chats']
        keep = set()
        for iid in self.tree.selection():
            keep.update([iid] if iid in rows else self.tree.get_children(iid))
        if not keep:
            raise ValueError('Выберите чаты, которые нужно оставить. Ctrl — выбрать несколько.')
        ids = sorted(rows.keys() - keep)
        if not ids:
            self.status.set('Нет других подключённых чатов.')
            return
        if not messagebox.askyesno('Отключение чатов',
            f'Оставить: {", ".join(rows[tid]["title"] for tid in sorted(keep))}.\n'
            f'Отключить остальных: {len(ids)}?\nАрхивы и чаты Codex сохраняются.', parent=self):
            return
        self.run(lambda: self.store.disconnect(ids),
            lambda removed: self.status.set(f'Отключено: {len(removed)}. Архивы сохранены.'))

    def add_chat(self):
        value = self.add_id.get()
        from core import thread_id
        self.guard(lambda: self.connect_selected([thread_id(value)]))

    def sync_all(self):
        def sync():
            errors = []
            unavailable = 0
            for tid, row in self.store.registry()['chats'].items():
                if transcript_path(row.get('source')) is None:
                    unavailable += 1
                    continue
                try:
                    self.store.sync(tid)
                except Exception as exc:
                    errors.append(tid + ': ' + str(exc))
            from compaction import CompactionQueue
            queue = CompactionQueue(self.store)
            queue.shutdown_event = self.stop_dispatch
            queue.tick()
            return errors, unavailable
        self.run(sync, lambda result: self.status.set(('; '.join(result[0]) if result[0] else 'Архивы обновлены.')
            + f' Недоступных архивов пропущено: {result[1]}.'))

    def pick_chats(self):
        if self.busy:
            self.status.set('Дождитесь завершения текущей операции.')
            return
        picker = ChatPicker(self, self.store.connection_catalog())
        self.wait_window(picker)
        if picker.result:
            self.connect_selected(picker.result)

    def connect_selected(self, ids):
        if self.busy:
            self.status.set('Дождитесь завершения текущей операции.')
            return
        if not ids:
            return
        if not messagebox.askyesno('Подключить выбранные чаты',
            f'Подключить выбранные чаты: {len(ids)}?\n'
            'Чаты из списка защиты будут пропущены. Включатся История, Мемо, Заметки, '
            'Автовозврат. Самосжатие и удалённое сжатие будут выключены.\n'
            'Также включатся живой счётчик и сообщения-напоминания при 90% (порог можно изменить).\n'
            'Сама кнопка не запускает сжатие и не меняет глобальные лимиты. '
            'Недоступные архивы будут пропущены. Новые чаты потребуют повторного нажатия; '
            'отдельные MCP-привязки не создаются. Не выбранные чаты не меняются.\n\n'
            'Продолжить?', parent=self):
            return
        if not configured_hook(self.hook_path.get(), self.hook_command()) and not self.install():
            return
        def done(result):
            self.reveal_chats(result['connected'])
            self.status.set(f"Подключено: {len(result['connected'])}; защищённых пропущено: "
                f"{len(result['protected'])}; ошибок: {len(result['errors'])}. Доверие к хуку проверяется отдельно.")
            self.status.set(self.status.get() + f" Недоступных архивов пропущено: {len(result['unavailable'])}.")
            if result['errors']:
                self.text_window('Ошибки подключения — остальные чаты обработаны', '\n'.join(result['errors']))
        self.run(lambda: self.store.connect_selected(ids, progress=self.report_progress), done)

    def periodic(self):
        if not self.busy and self.store.registry()['chats']:
            self.sync_all()
        self.after(30000, self.periodic)

    def open_file(self, name):
        path = self.store.folder(self.selected())
        if name:
            path /= name
        if not path.exists():
            raise ValueError('Архив ещё не создан; обновите чат.')
        if name == 'history.md':
            self.text_window('История чата — UTF-8', path.read_text(encoding='utf-8-sig'))
            return
        open_document(path)

    def import_doc(self):
        tid = self.selected()
        which = simpledialog.askstring('Импорт', 'Куда сохранить: memo или notes?', parent=self)
        if which not in ('memo', 'notes'):
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[('Текст UTF-8', '*.md *.txt')])
        if not path:
            return
        content = Path(path).read_text(encoding='utf-8-sig')
        target = self.store.folder(tid) / (which + '.md')
        if target.stat().st_size and not messagebox.askyesno('Замена документа', 'Заменить документ выбранного чата? Сначала будет сохранена резервная копия.', parent=self):
            return
        with lock(self.store.root):
            if target.stat().st_size:
                import time
                atomic(target.with_name(target.name + '.backup-' + str(time.time_ns())), target.read_bytes())
            atomic(target, content)
        self.status.set('Документ импортирован только в выбранный чат.')

    def text_window(self, title, text, copy=True):
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry('900x620')
        box = tk.Text(window, wrap='word', padx=12, pady=12)
        box.pack(fill='both', expand=True)
        box.insert('1.0', text)
        box.configure(state='disabled')
        if copy:
            def clipboard():
                self.clipboard_clear()
                self.clipboard_append(text)
            ttk.Button(window, text='Копировать для ручной отправки', command=clipboard).pack(pady=8)
        return window

    def preview(self, retrieved=None):
        tid = self.selected()
        if retrieved and self.search_thread != tid:
            raise ValueError('Результаты относятся к другому чату.')
        text = self.store.render(tid, retrieved)
        self.text_window('Предпросмотр — ничего не отправлено', text)

    def search(self):
        def start():
            tid, query = self.selected(), self.query.get()
            def done(hits):
                self.search_hits, self.search_thread = hits, tid
                self.results.delete(*self.results.get_children())
                for i, m in enumerate(hits):
                    self.results.insert('', 'end', iid=str(i), values=(m['role'], m['timestamp'], m['text'][:160].replace('\n', ' ')))
                self.status.set(f'Найдено {len(hits)}; показаны первые 30 совпадений. Полные сообщения сохранены.')
            self.run(lambda: self.store.search(tid, query), done)
        self.guard(start)

    def selected_results(self):
        if self.search_thread != self.selected():
            raise ValueError('Поиск относится к другому чату.')
        return [self.search_hits[int(i)] for i in self.results.selection()]

    def view_result(self):
        selected = self.selected_results()
        if not selected:
            raise ValueError('Выберите результат поиска.')
        self.text_window('Полные исходные сообщения', '\n\n'.join(json.dumps(m, ensure_ascii=False, indent=2) for m in selected))

    def pin_result(self):
        selected = self.selected_results()
        if len(selected) != 1:
            raise ValueError('Выберите одно исходное сообщение.')
        message = selected[0]
        window = tk.Toplevel(self)
        window.title('Выделите точный фрагмент исходного сообщения')
        window.geometry('850x550')
        box = tk.Text(window, wrap='word')
        box.pack(fill='both', expand=True)
        box.insert('1.0', message['text'])
        box.configure(state='disabled')
        tid = self.selected()
        def pin():
            def operation():
                excerpt = box.get('sel.first', 'sel.last')
                key = simpledialog.askstring('Закладка', 'ID (повтор ID обновит его цитату):', parent=window)
                if key:
                    self.store.pin_excerpt(tid, key, message, excerpt)
                    self.status.set('Точная цитата закреплена с исходным авторством.')
                    window.destroy()
            self.guard(operation)
        ttk.Button(window, text='Закрепить выделенный фрагмент', command=pin).pack(pady=8)

    def bookmarks(self):
        tid = self.selected()
        state = read_json(self.store.folder(tid) / 'bookmarks.json')
        text = json.dumps(state['entries'], ensure_ascii=False, indent=2)
        window = self.text_window('Закладки: обновление через [[память:id]], снятие через [[забыть:id]]', text, False)
        def unpin():
            key = simpledialog.askstring('Снять закладку', 'ID закладки:', parent=window)
            if key:
                self.guard(lambda: self.store.unpin(tid, key))
        ttk.Button(window, text='Снять закладку по ID', command=unpin).pack(pady=6)

    def read_native(self):
        if self.pending_close:
            return
        inputs = self.pending_native_read
        if inputs is None:
            inputs = (self.window.get(), self.compact.get())
        if self.busy:
            self.pending_native_read = inputs
            self.status.set('Чтение настроек Codex ожидает завершения текущей операции.')
            return
        self.pending_native_read = None
        def read():
            if self.settings is None:
                self.settings = ContextSettings(Rpc(home=self.store.home))
            return self.settings.read()
        self.run(read, lambda value: self.show_native(value, preserve_edits=inputs))

    def show_native(self, value, *, preserve_edits=None):
        for index, (field, key) in enumerate([(self.window, 'model_context_window'), (self.compact, 'model_auto_compact_token_limit')]):
            if preserve_edits is not None and field.get() != preserve_edits[index]:
                continue
            field.delete(0, 'end')
            if value['configured'][key] is not None:
                field.insert(0, f"{value['configured'][key]:,}".replace(',', ' '))
        self.native_info.set(f"Файл: {value['file']}\nПользовательские переопределения: {value['configured']}\n"
            f"Эффективная конфигурация app-server: {value['effective']}. Это не измерение живого чата.")

    def save_native(self):
        if self.busy or self.pending_native_read is not None:
            raise ValueError('Дождитесь завершения операции и чтения настроек Codex перед сохранением.')
        if self.settings is None or self.settings.snapshot is None:
            raise ValueError('Сначала прочитайте настройки Codex.')
        window, compact = parse_limit(self.window.get()), parse_limit(self.compact.get())
        if not messagebox.askyesno('Общие настройки Codex', f'Записать общие значения: окно {window}, автопорог {compact}?\nЭто может повлиять на новые сессии всех чатов. Существующие чаты не перезапускаются.', parent=self):
            return
        self.run(lambda: self.settings.save(window, compact), self.show_native)

    def reset_native(self):
        if self.busy or self.pending_native_read is not None:
            raise ValueError('Дождитесь завершения операции и чтения настроек Codex перед сбросом.')
        if self.settings is None or self.settings.snapshot is None:
            raise ValueError('Сначала прочитайте настройки Codex.')
        if messagebox.askyesno('Стандартные настройки', 'Удалить только два пользовательских переопределения контекста? Другие настройки сохраняются.', parent=self):
            self.run(lambda: self.settings.save(None, None), self.show_native)

    def save_threshold(self):
        self.store.update(self.selected(), preparation_threshold=parse_limit(self.threshold.get()))
        self.show_selected()

    def refresh_live_counter(self):
        try:
            tid = self.selected()
            record = load_warning_state(self.store.root)['chats'].get(tid, {})
            text = counter_text(record.get('observation'))
            if record.get('status'):
                label = {'sent': 'отправлено', 'not_started': 'отложено', 'ambiguous': 'доставка не подтверждена',
                    'dispatching': 'отправляется'}.get(record['status'], record['status'])
                text += '\nНапоминание: ' + label + '. ' + record.get('detail', '')
            if self.warning_error:
                text += '\nОшибка наблюдения: ' + self.warning_error
            self.live_counter.set(text)
        except ValueError:
            self.live_counter.set('Выберите подключённый чат; живое наблюдение пока недоступно.')
        except Exception as exc:
            self.live_counter.set('Ошибка живого счётчика: ' + str(exc))

    def save_context_warning(self, all_chats=False):
        if self.busy:
            raise ValueError('Дождитесь завершения текущей операции перед изменением предупреждений.')
        percent = parse_limit(self.warning_percent.get())
        enabled = self.warning_enabled.get()
        ids = list(self.store.registry()['chats']) if all_chats else [self.selected()]
        if all_chats and not messagebox.askyesno('Предупреждения во всех чатах',
                f'Применить: включено={enabled}, порог={percent}% для {len(ids)} чатов?\nЭто разрешает сообщения-напоминания, но не сжатие.', parent=self):
            return
        self.store.configure_context_warnings(ids, enabled, percent)
        self.warning_refresh.set()
        self.status.set('Предупреждения настроены; метрика берётся из живого Codex.')

    def save_restore_limit(self):
        value = parse_limit(self.restore_limit.get())
        self.store.update(self.selected(), limit=value)
        self.status.set('Лимит возврата сохранён.')

    def hook_command(self):
        if not getattr(sys, 'frozen', False) and sys.platform == 'win32':
            raise ValueError('Установка хука доступна в собранном EXE. Исходный режим не меняет hooks.json.')
        exe = hook_executable()
        if not exe.exists():
            raise ValueError('Не найден launcher ContextKeeperHook; запустите установщик для своей платформы.')
        relocation = exe.parent / 'relocation.json'
        if relocation.exists():
            previous = Path(read_json(relocation)['previous_install'])
            try:
                if (previous / exe.name).samefile(exe) and (previous / 'data').samefile(self.store.root):
                    legacy = command(previous / exe.name, previous / 'data', self.store.home, resolve_paths=False)
                    if configured_hook(self.hook_path.get(), legacy):
                        return legacy
            except OSError:
                pass
        return command(exe, self.store.root, self.store.home)

    def install(self):
        cmd = self.hook_command()
        path = Path(self.hook_path.get())
        if messagebox.askyesno('Подключение хука', f'Установить или обновить наш обработчик в {path}?\nСуществующие записи сохраняются; будет резервная копия.\nАвтовозврат действует только на явно включённые чаты.', parent=self):
            self.status.set(install_hook(path, cmd))
            self.hook_state.set('Хук записан; проверьте доверие через /hooks. Живая доставка не подтверждена.')
            return True
        return False

    def check_hook(self):
        cmd = self.hook_command()
        if not configured_hook(self.hook_path.get(), cmd):
            self.hook_state.set('Хук отсутствует или требует обновления. Нажмите «Установить хук…»; сжатие не требуется.')
            return
        from native import Rpc
        self.run(lambda: runtime_hook_status(Rpc(home=self.store.home).call('hooks/list', {}), cmd),
            lambda state: self.hook_state.set('Codex: ' + state))

    def uninstall(self):
        cmd = self.hook_command()
        path = Path(self.hook_path.get())
        if messagebox.askyesno('Удаление хука', f'Удалить только обработчик этой установки из {path}?', parent=self):
            remove_hook(path, cmd)
            self.status.set('Наш обработчик удалён; другие сохранены.')

    def toggle_compaction(self):
        tid = self.selected()
        row = self.store.registry()['chats'][tid]
        enabled = row.get('allow_compaction', False)
        if not enabled and not messagebox.askyesno('Самосжатие', f"Разрешить чату «{row['title']}» запрашивать сжатие?\nДля выполнения также нужно разрешение удалённого запуска.", parent=self):
            return
        self.store.update(tid, allow_compaction=not enabled)
        self.status.set('MCP-запросы разрешены.' if not enabled else 'MCP-запросы запрещены.')

    def toggle_unsafe(self):
        tid = self.selected()
        row = self.store.registry()['chats'][tid]
        enabled = row.get('unsafe_native_compaction', False)
        if not enabled and not messagebox.askyesno('Удалённое сжатие',
            f"Разрешить внешний запуск сжатия для чата «{row['title']}»?", parent=self):
            return
        self.store.update(tid, unsafe_native_compaction=not enabled)
        self.show_selected()
        self.status.set('Удалённый запуск разрешён выбранному чату.' if not enabled else 'Удалённый запуск выключен.')

    def show_queue(self):
        from compaction import CompactionQueue
        queue = CompactionQueue(self.store)
        tid = self.selected()
        rows = {k: v for k, v in queue.load()['requests'].items() if v['thread_id'] == tid}
        window = self.text_window('Очередь выбранного чата', json.dumps(rows, ensure_ascii=False, indent=2), False)
        def cancel():
            rid = simpledialog.askstring('Отмена', 'ID запроса:', parent=window)
            if rid:
                self.guard(lambda: queue.cancel(rid, tid))
        ttk.Button(window, text='Отменить запрос по ID', command=cancel).pack(pady=5)

    def mcp_config(self):
        tid = self.selected()
        exe = hook_executable()
        args = ['--mcp', '--bound-thread', tid, '--data', str(self.store.root), '--codex-home', str(self.store.home)]
        name = 'context_keeper_' + tid.replace('-', '_')
        # JSON displayed as data, never writes native config automatically.
        text = ('MCP stdio configuration (JSON example; configure your client explicitly).\n'
            'Compaction requires BOTH per-chat request and external-launch permissions; both default off.\n\n' +
            json.dumps({'mcpServers': {name: {'command': str(exe), 'args': args}}}, ensure_ascii=False, indent=2) +
            '\n\nCodex config.toml equivalent:\n' + f'[mcp_servers.{name}]\ncommand = ' + json.dumps(str(exe)) + '\nargs = ' + json.dumps(args) + '\n')
        self.text_window('Явная MCP-привязка к выбранному чату', text)
