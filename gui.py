"""Russian desktop UI; automatic work is read-only archive synchronization."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from core import KEYS, atomic, lock, read_json, write_json
from native import ContextSettings, Rpc, parse_limit
from hooks import command, install_hook, remove_hook


class Application(tk.Tk):
    def __init__(self, store, smoke=False):
        super().__init__()
        self.store, self.smoke = store, smoke
        self.settings = None
        self.work = queue.Queue()
        self.busy = False
        self.pending_close = False
        self.stop_dispatch = threading.Event()
        self.protocol('WM_DELETE_WINDOW', self.request_close)
        self.title('Хранитель контекста Codex — 0.2 — небезопасное сжатие по согласию')
        self.geometry('1120x790')
        self.minsize(960, 680)
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

    def build(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=12, pady=10)
        chats, context, integrations = (ttk.Frame(notebook, padding=12) for _ in range(3))
        notebook.add(chats, text='Чаты и документы')
        notebook.add(context, text='Контекст Codex — общие настройки')
        notebook.add(integrations, text='Восстановление после сжатия')
        top = ttk.Frame(chats)
        top.pack(fill='x')
        ttk.Label(top, text='ID чата / codex://threads/…').pack(side='left')
        self.add_id = ttk.Entry(top, width=43)
        self.add_id.pack(side='left', padx=8)
        ttk.Button(top, text='Добавить чат', command=self.add_chat).pack(side='left')
        ttk.Button(top, text='Обновить архивы', command=self.sync_all).pack(side='left', padx=8)
        self.tree = ttk.Treeview(chats, columns=('title', 'history', 'memo', 'notes', 'auto', 'status'), show='headings', height=7)
        for key, label, width in [('title', 'Название чата', 255), ('history', 'История', 75), ('memo', 'Мемо', 65),
                ('notes', 'Заметки', 75), ('auto', 'Автовозврат', 105), ('status', 'Состояние архива', 290)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(fill='x', pady=10)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self.show_selected())
        self.tree.bind('<ButtonRelease-1>', self.toggle_cell)
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
        ttk.Separator(context).pack(fill='x', pady=25)
        ttk.Label(context, text='Выбранный чат: фактически наблюдаемая сессия', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.observation = tk.StringVar(value='Нет выбранного чата / нет доступной метрики.')
        ttk.Label(context, textvariable=self.observation, wraplength=1020).pack(anchor='w', pady=10)
        prep = ttk.Frame(context)
        prep.pack(fill='x')
        ttk.Label(prep, text='Личный предупредительный порог (токены)').pack(side='left')
        self.threshold = ttk.Entry(prep, width=20)
        self.threshold.pack(side='left', padx=10)
        ttk.Button(prep, text='Сохранить для выбранного чата', command=lambda: self.guard(self.save_threshold)).pack(side='left')
        ttk.Label(context, text='Это только предупреждение по последней доступной метрике. Оно не изменяет окно модели и не запускает сжатие.\n'
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
            'Приложение не создаёт чаты, не посылает задания и не запускает сжатие.', wraplength=1010).pack(anchor='w', pady=10)
        ttk.Label(integrations, text='Файл hooks.json (существующие обработчики сохраняются)').pack(anchor='w', pady=(14, 4))
        self.hook_path = ttk.Entry(integrations, width=105)
        self.hook_path.insert(0, str(self.store.home / 'hooks.json'))
        self.hook_path.pack(fill='x')
        hb = ttk.Frame(integrations)
        hb.pack(fill='x', pady=12)
        ttk.Button(hb, text='Установить хук…', command=lambda: self.guard(self.install)).pack(side='left')
        ttk.Button(hb, text='Удалить только наш хук…', command=lambda: self.guard(self.uninstall)).pack(side='left', padx=8)
        ttk.Label(integrations, text='Чаты можно исключить локальным списком защиты.\n'
            'Папки чатов изолированы по полному ID. Источники Codex открываются только для чтения.', wraplength=1010).pack(anchor='w', pady=10)
        cb = ttk.Frame(integrations)
        cb.pack(fill='x', pady=8)
        ttk.Button(cb, text='Разрешить / запретить MCP-запросы выбранному чату', command=lambda: self.guard(self.toggle_compaction)).pack(side='left')
        ttk.Button(cb, text='Очередь сжатия', command=lambda: self.guard(self.show_queue)).pack(side='left', padx=8)
        ttk.Button(cb, text='MCP-конфигурация', command=lambda: self.guard(self.mcp_config)).pack(side='left')
        ttk.Button(integrations, text='Небезопасный реальный запуск: включить / выключить для чата…',
            command=lambda: self.guard(self.toggle_unsafe)).pack(anchor='w', pady=6)
        ttk.Label(integrations, text='НЕБЕЗОПАСНО: сжатие может прервать уже начавшийся ответ на новое сообщение.\n'
            'Новое сообщение во время активного сжатия может быть отклонено. Простое ожидание idle не устраняет гонку.\n'
            'По умолчанию реальный запуск выключен; нужно отдельное согласие для каждого чата.',
            foreground='#a00000', wraplength=1010).pack(anchor='w')
        ttk.Label(self, textvariable=self.status, wraplength=1090, padding=(12, 4)).pack(fill='x')

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror('Хранитель контекста', str(exc), parent=self)

    def request_close(self):
        if self.busy:
            self.pending_close = True
            self.stop_dispatch.set()
            self.status.set('Закрытие после завершения текущей операции; уже отправленное сжатие не отменяется.')
        else:
            self.destroy()

    def run(self, fn, done=None):
        if self.busy:
            self.status.set('Дождитесь завершения текущей операции.')
            return
        self.busy = True
        self.status.set('Выполняется…')
        def worker():
            try:
                self.work.put((True, fn(), done))
            except Exception as exc:
                self.work.put((False, str(exc), None))
        threading.Thread(target=worker, daemon=True).start()

    def check_work(self):
        try:
            ok, value, done = self.work.get_nowait()
            self.busy = False
            if self.pending_close:
                self.destroy()
                return
            if ok:
                self.status.set('Готово.')
                if done:
                    self.guard(lambda: done(value))
            else:
                self.status.set('Ошибка: ' + value)
            self.refresh()
        except queue.Empty:
            pass
        self.after(100, self.check_work)

    def selected(self):
        ids = self.tree.selection()
        if not ids:
            raise ValueError('Сначала выберите чат.')
        return ids[0]

    def refresh(self):
        selected = self.tree.selection()
        rows = self.store.registry()['chats']
        self.tree.delete(*self.tree.get_children())
        for tid, row in rows.items():
            vals = [row['title']] + ['☑' if row['selected'][key] else '☐' for key in KEYS]
            vals += ['☑' if row['auto_restore'] else '☐', row['status']]
            self.tree.insert('', 'end', iid=tid, values=vals)
        if selected and selected[0] in rows:
            self.tree.selection_set(selected[0])

    def show_selected(self):
        if not self.tree.selection():
            return
        tid = self.selected()
        row = self.store.registry()['chats'][tid]
        self.details.set(f"ID: {tid}\nПапка: {self.store.folder(tid)}\nНебезопасное сжатие: " +
            ('ВКЛЮЧЕНО — новое сообщение может привести к прерыванию ответа.' if row.get('unsafe_native_compaction') else 'выключено.'))
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
        if not tid or col not in ('#2', '#3', '#4', '#5'):
            return
        def change():
            row = self.store.registry()['chats'][tid]
            if col == '#5':
                if not row['auto_restore'] and not messagebox.askyesno('Автовозврат', 'Разрешить возврат выбранных данных только этому чату после сжатия?\nНужен отдельно установленный хук; сейчас ничего не отправляется.', parent=self):
                    return
                self.store.update(tid, auto_restore=not row['auto_restore'])
            else:
                chosen = dict(row['selected'])
                key = KEYS[int(col[1:]) - 2]
                chosen[key] = not chosen[key]
                self.store.update(tid, selected=chosen)
            self.refresh()
        self.guard(change)

    def add_chat(self):
        value = self.add_id.get()
        def add():
            tid = self.store.add(value)
            self.store.sync(tid)
            return tid
        self.run(add, lambda tid: (self.refresh(), self.tree.selection_set(tid), self.add_id.delete(0, 'end')))

    def sync_all(self):
        def sync():
            errors = []
            for tid in self.store.registry()['chats']:
                try:
                    self.store.sync(tid)
                except Exception as exc:
                    errors.append(tid + ': ' + str(exc))
            from compaction import CompactionQueue
            queue = CompactionQueue(self.store)
            queue.shutdown_event = self.stop_dispatch
            queue.tick()
            return errors
        self.run(sync, lambda errors: self.status.set('; '.join(errors) if errors else 'Архивы обновлены.'))

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
        os.startfile(str(path))

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
        def read():
            if self.settings is None:
                self.settings = ContextSettings(Rpc(home=self.store.home))
            return self.settings.read()
        self.run(read, self.show_native)

    def show_native(self, value):
        for field, key in [(self.window, 'model_context_window'), (self.compact, 'model_auto_compact_token_limit')]:
            field.delete(0, 'end')
            if value['configured'][key] is not None:
                field.insert(0, f"{value['configured'][key]:,}".replace(',', ' '))
        self.native_info.set(f"Файл: {value['file']}\nПользовательские переопределения: {value['configured']}\n"
            f"Эффективная конфигурация app-server: {value['effective']}. Это не измерение живого чата.")

    def save_native(self):
        if self.settings is None or self.settings.snapshot is None:
            raise ValueError('Сначала прочитайте настройки Codex.')
        window, compact = parse_limit(self.window.get()), parse_limit(self.compact.get())
        if not messagebox.askyesno('Общие настройки Codex', f'Записать общие значения: окно {window}, автопорог {compact}?\nЭто может повлиять на новые сессии всех чатов. Существующие чаты не перезапускаются.', parent=self):
            return
        self.run(lambda: self.settings.save(window, compact), self.show_native)

    def reset_native(self):
        if self.settings is None or self.settings.snapshot is None:
            raise ValueError('Сначала прочитайте настройки Codex.')
        if messagebox.askyesno('Стандартные настройки', 'Удалить только два пользовательских переопределения контекста? Другие настройки сохраняются.', parent=self):
            self.run(lambda: self.settings.save(None, None), self.show_native)

    def save_threshold(self):
        self.store.update(self.selected(), preparation_threshold=parse_limit(self.threshold.get()))
        self.show_selected()

    def save_restore_limit(self):
        value = parse_limit(self.restore_limit.get())
        self.store.update(self.selected(), limit=value)
        self.status.set('Лимит возврата сохранён.')

    def hook_command(self):
        if not getattr(sys, 'frozen', False):
            raise ValueError('Установка хука доступна в собранном EXE. Исходный режим не меняет hooks.json.')
        exe = Path(sys.executable).with_name('ContextKeeperHook.exe')
        if not exe.exists():
            raise ValueError('Рядом отсутствует ContextKeeperHook.exe.')
        return command(exe, self.store.root, self.store.home)

    def install(self):
        cmd = self.hook_command()
        path = Path(self.hook_path.get())
        if messagebox.askyesno('Подключение хука', f'Добавить только наш обработчик в {path}?\nСуществующие записи сохраняются; будет резервная копия.\nАвтовозврат действует только на явно включённые чаты.', parent=self):
            self.status.set(install_hook(path, cmd))

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
        if not enabled and not messagebox.askyesno('MCP-сжатие', 'Разрешить запросы только этому чату?\nРеальный запуск требует отдельного согласия на известный риск.\nУ stdio MCP нет достоверного автоматического caller ID; используется явная привязка.', parent=self):
            return
        self.store.update(tid, allow_compaction=not enabled)
        self.status.set('MCP-запросы разрешены.' if not enabled else 'MCP-запросы запрещены.')

    def toggle_unsafe(self):
        tid = self.selected()
        row = self.store.registry()['chats'][tid]
        enabled = row.get('unsafe_native_compaction', False)
        if not enabled and not messagebox.askyesno('НЕБЕЗОПАСНОЕ сжатие',
            f"Включить реальное сжатие для чата «{row['title']}»?\n\n"
            'Подтверждено тестом: если новое сообщение запустит ответ между проверкой idle и командой сжатия, этот ответ может быть прерван. '
            'Сообщение во время сжатия может быть отклонено.\n\n'
            'Приложение дождётся завершения исходного ответа и повторно проверит idle, но атомарной защиты НЕТ. Продолжить с этим риском?', parent=self):
            return
        self.store.update(tid, unsafe_native_compaction=not enabled)
        self.show_selected()
        self.status.set('Небезопасный реальный запуск разрешён только этому чату.' if not enabled else 'Реальный запуск выключен.')

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
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent / 'dist'
        exe = base / 'ContextKeeperHook.exe'
        args = ['--mcp', '--bound-thread', tid, '--data', str(self.store.root), '--codex-home', str(self.store.home)]
        name = 'context_keeper_' + tid.replace('-', '_')
        # JSON displayed as data, never writes native config automatically.
        text = ('MCP stdio configuration (JSON example; configure your client explicitly).\n'
            'Bound ID is not authenticated caller metadata. Real compact requires BOTH request permission and separate known-risk consent for this chat.\n\n' +
            json.dumps({'mcpServers': {name: {'command': str(exe), 'args': args}}}, ensure_ascii=False, indent=2) +
            '\n\nCodex config.toml equivalent:\n' + f'[mcp_servers.{name}]\ncommand = ' + json.dumps(str(exe)) + '\nargs = ' + json.dumps(args) + '\n')
        self.text_window('Явная MCP-привязка к выбранному чату', text)
