"""User-local Debian desktop install. No sudo, compaction or chat registration."""
import argparse
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from version import VERSION

RUNTIME_FILES = ('main.py', 'core.py', 'gui.py', 'native.py', 'native_driver.py',
    'platform_support.py', 'hooks.py', 'compaction.py', 'mcp_server.py',
    'management_mcp.py', 'context_warnings.py', 'version.py', 'LICENSE', 'README.md',
    'PUBLIC_REVIEW.md', 'LINUX.md', 'VM_HANDOFF.md', 'bridge/REFERENCE-LICENSE.txt')


def launcher(python, app, data, codex_home, codex_cli=None):
    env = 'export CODEX_CLI_PATH=' + shlex.quote(str(codex_cli)) + '\n' if codex_cli else ''
    return '#!/bin/sh\n' + env + 'exec ' + shlex.join([str(python), str(app / 'main.py'),
        '--data', str(data), '--codex-home', str(codex_home)]) + ' "$@"\n'


def self_check(python, app, *, gui=False):
    with tempfile.TemporaryDirectory(prefix='keeper-self-check-') as temp:
        root = Path(temp)
        home = root / 'home'
        home.mkdir()
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as db:
            db.execute('create table threads(id text,title text,rollout_path text)')
            db.commit()
        argv = [str(python), str(app / 'main.py'), '--data', str(root / 'data'), '--codex-home', str(home)]
        requests = [{'id': 1, 'method': 'initialize'}, {'id': 2, 'method': 'tools/list'}]
        result = subprocess.run([*argv, '--mcp-management'], input=''.join(json.dumps(r) + '\n' for r in requests),
            capture_output=True, text=True, encoding='utf-8', timeout=20, check=True)
        replies = [json.loads(line)['result'] for line in result.stdout.splitlines()]
        if replies[0]['serverInfo']['version'] != VERSION or len(replies[1]['tools']) != 7:
            raise ValueError('Installed MCP version/tool contract does not match the release')
        if gui:
            subprocess.run([*argv, '--smoke-ui'], timeout=20, check=True)
        return {'stdio_handshake': True, 'tools': 7, 'gui_smoke': gui,
            'active_chat_tool_visibility': 'must be checked by the calling Codex chat after MCP reload'}


def main():
    parser = argparse.ArgumentParser()
    default = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'context-keeper'
    parser.add_argument('--prefix', type=Path, default=default)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--register-mcp', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--verify-gui', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('This installer is for Linux; use the Windows release on Windows')
    cli = os.environ.get('CODEX_CLI_PATH') or shutil.which('codex')
    missing = []
    if importlib.util.find_spec('tkinter') is None:
        missing.append('python3-tk')
    if not shutil.which('xdg-open'):
        missing.append('xdg-utils')
    if missing:
        raise ValueError('Install desktop prerequisites: sudo apt-get install python3-venv python3-tk xdg-utils')
    if args.register_mcp and (not cli or not Path(cli).is_file()):
        raise ValueError('Codex CLI not found; add codex to PATH or set CODEX_CLI_PATH before --register-mcp')
    if args.check_only:
        print(json.dumps({'version': VERSION, 'python': sys.version.split()[0], 'platform': sys.platform,
            'codex_cli_found': bool(cli), 'display_available': bool(os.environ.get('DISPLAY')),
            'prerequisites': 'module discovery only; installation/GUI/native IPC not verified'}, indent=2))
        return
    source = Path(__file__).resolve().parent
    for name in (*RUNTIME_FILES, 'requirements.txt'):
        if not (source / name).is_file():
            raise ValueError('Incomplete release package: ' + name)
    native = source / 'dist/ContextKeeperBridge'
    if native.is_file():
        with native.open('rb') as stream:
            header = stream.read(20)
        expected_machine = {'x86_64': 62, 'aarch64': 183}.get(os.uname().machine)
        if expected_machine is None or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != expected_machine:
            raise ValueError('The packaged native bridge does not match this Linux architecture')
        for name in ('DOTNET-LICENSE.txt', 'DOTNET-THIRD-PARTY-NOTICES.txt'):
            if not (source / 'dist/licenses' / name).is_file():
                raise ValueError('Native runtime notice missing: ' + name)
    prefix, home = args.prefix.expanduser().resolve(), args.codex_home.expanduser().resolve()
    if prefix == source:
        raise ValueError('Choose an installation prefix separate from the extracted source')
    old_native = prefix / 'app/dist/ContextKeeperBridge'
    if not native.is_file() and (old_native.exists() or old_native.is_symlink()):
        raise ValueError('Updating an existing native installation requires a matching complete Linux release package')
    marker = prefix / 'install-state.json'
    if prefix.exists() and any(prefix.iterdir()):
        if marker.is_symlink() or not marker.is_file():
            raise ValueError('Refusing a nonempty unmanaged installation prefix; choose a new empty directory')
        previous = json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('application') != 'codex-context-keeper' or previous.get('schema') != 1:
            raise ValueError('Installation ownership marker does not match this application')
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    if prefix.stat().st_uid != os.geteuid() or prefix.stat().st_mode & 0o022:
        raise ValueError('Installation prefix must be owned by you and not group/world writable')
    marker.write_text(json.dumps({'application': 'codex-context-keeper', 'schema': 1,
        'version': VERSION, 'state': 'installing'}), encoding='utf-8')
    app, data, venv = prefix / 'app', prefix / 'data', prefix / '.venv'
    for path in (app, data, venv):
        if path.is_symlink() or prefix not in path.resolve().parents:
            raise ValueError('Installation paths must remain inside the selected prefix')
    app.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True, mode=0o700)
    data.chmod(0o700)
    # Repeating venv creation also repairs a prior interrupted ensurepip setup.
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    python = venv / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(source / 'requirements.txt')], check=True)
    for name in RUNTIME_FILES:
        target = app / name
        if target.is_symlink() or prefix not in target.resolve().parents:
            raise ValueError('Refusing an installation path outside the prefix: ' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    native_target = app / 'dist/ContextKeeperBridge'
    if native.is_file():
        if native_target.is_symlink() or prefix not in native_target.resolve().parents:
            raise ValueError('Refusing a native target outside the prefix')
        native_target.parent.mkdir(exist_ok=True)
        shutil.copy2(native, native_target)
        native_target.chmod(0o755)
        notices = source / 'dist/licenses'
        for name in ('DOTNET-LICENSE.txt', 'DOTNET-THIRD-PARTY-NOTICES.txt'):
            if not (notices / name).is_file():
                raise ValueError('Native runtime notice missing: ' + name)
            target = app / 'licenses' / name
            if target.is_symlink() or prefix not in target.resolve().parents:
                raise ValueError('Runtime notices must remain inside the prefix')
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(notices / name, target)
    for name in ('ContextKeeper', 'ContextKeeperHook'):
        path = app / name
        if path.is_symlink():
            raise ValueError('Refusing to replace a symlink launcher')
        path.write_text(launcher(python, app, data, home, cli), encoding='utf-8')
        path.chmod(0o755)
    checks = self_check(python, app, gui=args.verify_gui)
    if args.register_mcp:
        if (home / 'config.toml').exists():
            backups = prefix / 'private-backups'
            if backups.is_symlink() or prefix not in backups.resolve().parents:
                raise ValueError('Backup directory must remain inside the prefix')
            backups.mkdir(exist_ok=True, mode=0o700)
            backups.chmod(0o700)
            backup = backups / ('config.toml.' + str(time.time_ns()))
            shutil.copy2(home / 'config.toml', backup)
            backup.chmod(0o600)
        subprocess.run([str(cli), 'mcp', 'add', 'context_keeper_management', '--',
            str(app / 'ContextKeeperHook'), '--mcp-management'],
            env=dict(os.environ, CODEX_HOME=str(home)), check=True)
    marker.write_text(json.dumps({'application': 'codex-context-keeper', 'schema': 1,
        'version': VERSION, 'state': 'installed'}), encoding='utf-8')
    print(json.dumps({'version': VERSION, 'installed': True, 'gui_launcher': str(app / 'ContextKeeper'),
        'mcp_launcher': str(app / 'ContextKeeperHook'), 'data': str(data),
        'mcp_configured': args.register_mcp, 'native_bridge_present': native_target.is_file(),
        'live_desktop_ipc_verified': False, 'checks': checks}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'installed': False, 'error': str(exc)}), file=sys.stderr)
        sys.exit(1)
