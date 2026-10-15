from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path)
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--keeper-hook', action='store_true')
    parser.add_argument('--mcp', action='store_true')
    parser.add_argument('--bound-thread')
    parser.add_argument('--smoke-ui', action='store_true')
    args = parser.parse_args()
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    data = args.data or base / 'data'
    from core import Store
    if args.mcp:
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
        app = Application(Store(data, args.codex_home), smoke=args.smoke_ui)
        app.mainloop()


if __name__ == '__main__':
    main()
