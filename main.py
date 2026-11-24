from __future__ import annotations
import argparse
import gc
import json
import os
from pathlib import Path
import sys
from version import VERSION


def gui_home(data, explicit):
    """GUI preference never overrides CLI source isolation or external bindings."""
    if explicit is not None:
        return explicit, ''
    preference = Path(data) / 'gui-source.json'
    if not preference.exists():
        return None, ''
    try:
        document = json.loads(preference.read_text(encoding='utf-8-sig'))
        value = document.get('codex_home')
        if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
            raise ValueError('Требуется абсолютный путь к папке Codex.')
        return Path(value), ''
    except (OSError, ValueError, AttributeError) as error:
        return None, f'Не удалось прочитать сохранённую папку ({preference}): {error}. Выберите папку заново.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--keeper-hook', action='store_true')
    parser.add_argument('--mcp', action='store_true')
    parser.add_argument('--mcp-management', action='store_true')
    parser.add_argument('--bound-thread')
    parser.add_argument('--smoke-ui', action='store_true')
    args = parser.parse_args()
    if sum((args.mcp, args.mcp_management, args.keeper_hook)) > 1:
        parser.error('Choose only one MCP or hook mode')
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    data = args.data or base / 'data'
    from core import Store
    if args.mcp_management:
        from management_mcp import serve_management
        serve_management(Store(data, args.codex_home))
    elif args.mcp:
        if not args.bound_thread:
            parser.error('--mcp requires --bound-thread (explicit identity binding)')
        from mcp_server import serve
        serve(Store(data, args.codex_home), args.bound_thread)
    elif args.keeper_hook:
        # Do not create data for unrelated/disabled hooks.
        if not (data / 'registry.json').exists():
            print('{}')
            return
        from hooks import hook_response
        try:
            event = json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))
            response = hook_response(event, Store(data, args.codex_home))
        except Exception:
            response = {'systemMessage': 'Context Keeper: некорректное событие/хранилище; восстановление не выполнено.'}
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(response, ensure_ascii=False))
    else:
        from gui import Application
        home, source_error = gui_home(data, args.codex_home)
        while True:
            app = Application(Store(data, home), smoke=args.smoke_ui, source_config_error=source_error)
            app.mainloop()
            if app.restart_home is None:
                break
            if app.monitor_thread is not None:
                app.monitor_thread.join(timeout=40)
                if app.monitor_thread.is_alive():
                    from tkinter import messagebox
                    messagebox.showerror('Папка пока не изменена', 'Предыдущий запрос ещё завершается. Перезапустите Keeper позднее.')
                    break
            home, source_error = app.restart_home, ''
            # Release the old Tcl interpreter before constructing another Tk root.
            app = None
            gc.collect()


if __name__ == '__main__':
    main()
