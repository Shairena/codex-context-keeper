"""Portable selectors plus a real POSIX argv probe when running on Linux."""
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hooks import command
from install_linux import launcher, main as install_main, RUNTIME_FILES
from native import resolve_cli
from platform_support import bridge_executable, hook_executable, open_document


class LinuxPortabilityTests(unittest.TestCase):
    def test_linux_paths_cli_and_hook_preserve_quoted_arguments(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            with patch('sys.platform', 'linux'), patch.dict(os.environ, {'CODEX_CLI_PATH': ''}):
                with patch('native.shutil.which', return_value='/usr/bin/codex'):
                    self.assertEqual(resolve_cli(), '/usr/bin/codex')
                self.assertEqual(bridge_executable().name, 'ContextKeeperBridge')
                self.assertEqual(hook_executable().name, 'ContextKeeperHook')
                exe, data, home = root/"hook's launcher", root/'data $value', root/'home with spaces'
                value = command(exe, data, home)
                self.assertEqual(shlex.split(value), [str(exe.resolve()), '--keeper-hook', '--data', str(data.resolve()), '--codex-home', str(home.resolve())])
                with patch('platform_support.subprocess.Popen') as start:
                    open_document(data)
                    self.assertEqual(start.call_args.args[0], ['xdg-open', str(data)])

    def test_installed_launcher_uses_argument_safe_exec_and_selected_home(self):
        value = launcher(PurePosixPath('/prefix with space/python'), PurePosixPath("/app's root"), PurePosixPath('/data $x'), PurePosixPath('/codex home'), '/usr/bin/codex')
        self.assertTrue(value.startswith('#!/bin/sh\n'))
        self.assertTrue(value.endswith(' "$@"\n'))
        self.assertIn('export CODEX_CLI_PATH=/usr/bin/codex', value)
        args = shlex.split(value.split('exec ', 1)[1].removesuffix(' "$@"\n'))
        self.assertEqual(args, ['/prefix with space/python', "/app's root/main.py", '--data', '/data $x', '--codex-home', '/codex home'])

    def test_source_only_update_refuses_stale_native_before_changing_installation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            source, prefix = root / 'source', root / 'installed'
            for name in (*RUNTIME_FILES, 'requirements.txt'):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'fixture')
            old = prefix / 'app/dist/ContextKeeperBridge'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'old native must remain untouched')
            with patch('sys.platform', 'linux'), patch('sys.argv', ['install_linux.py', '--prefix', str(prefix)]), \
                    patch('install_linux.__file__', str(source / 'install_linux.py')), \
                    patch('install_linux.shutil.which', return_value='/usr/bin/codex'), \
                    patch('install_linux.importlib.util.find_spec', return_value=object()):
                with self.assertRaisesRegex(ValueError, 'existing native'):
                    install_main()
            self.assertEqual(old.read_bytes(), b'old native must remain untouched')
            self.assertFalse((prefix / 'app/main.py').exists())
            self.assertFalse((prefix / '.venv').exists())

    def test_unmanaged_nonempty_prefix_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            source, prefix = root / 'source', root / 'unrelated'
            for name in (*RUNTIME_FILES, 'requirements.txt'):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'fixture')
            existing = prefix / 'app/main.py'
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b'unrelated application')
            with patch('sys.platform', 'linux'), patch('sys.argv', ['install_linux.py', '--prefix', str(prefix)]), \
                    patch('install_linux.__file__', str(source / 'install_linux.py')), \
                    patch('install_linux.shutil.which', return_value='/usr/bin/codex'), \
                    patch('install_linux.importlib.util.find_spec', return_value=object()):
                with self.assertRaisesRegex(ValueError, 'unmanaged'):
                    install_main()
            self.assertEqual(existing.read_bytes(), b'unrelated application')

    @unittest.skipUnless(sys.platform == 'linux', 'Requires real Linux shell and Unix executable semantics')
    def test_posix_hook_command_executes_without_argument_expansion(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            exe = root / "hook's executable"
            exe.write_text('#!/bin/sh\nprintf "%s\\000" "$@"\n', encoding='utf-8')
            exe.chmod(0o700)
            data, home = root/'data $HOME', root/'home with spaces'
            result = subprocess.run(['/bin/sh', '-c', command(exe, data, home)], capture_output=True, check=True)
            self.assertEqual(result.stdout.decode().split('\0')[:-1], ['--keeper-hook', '--data', str(data), '--codex-home', str(home)])


if __name__ == '__main__':
    unittest.main()
