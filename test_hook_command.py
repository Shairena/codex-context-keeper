"""Disposable Windows shell-boundary and exact legacy-migration regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from hooks import command, configured_hook, install_hook


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell hook contract')
class HookCommandTests(unittest.TestCase):
    def test_powershell_boundary_and_exact_legacy_migration(self):
        pwsh = shutil.which('pwsh')
        if not pwsh:
            candidate = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
            pwsh = str(candidate) if candidate.exists() else None
        self.assertIsNotNone(pwsh, 'PowerShell is required to verify the actual parser boundary')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            sentinel = root / "hook's argv probe.ps1"
            sentinel.write_text('ConvertTo-Json -InputObject @($args) -Compress', encoding='utf-8')
            data, home = root / 'data with spaces', root / "home's path"
            cmd = command(sentinel, data, home)
            prefix = [pwsh, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command']
            old = subprocess.run(prefix + [cmd[2:]], capture_output=True, text=True, timeout=15)
            self.assertNotEqual(old.returncode, 0)
            result = subprocess.run(prefix + [cmd], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ['--keeper-hook', '--data', str(data), '--codex-home', str(home)])
            foreign = {'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'echo unrelated'}]}
            handler = {'type': 'command', 'command': cmd[2:], 'timeout': 17, 'statusMessage': 'preserve'}
            config = {'hooks': {'SessionStart': [foreign, {'matcher': '^compact$', 'hooks': [handler]}]}}
            path = root / 'hooks.json'
            original = json.dumps(config).encode()
            path.write_bytes(original)
            self.assertFalse(configured_hook(path, cmd))
            install_hook(path, cmd)
            self.assertTrue(configured_hook(path, cmd))
            actual = json.loads(path.read_text(encoding='utf-8'))
            handler['command'] = cmd
            self.assertEqual(actual, config)
            backups = list(root.glob('hooks.json.keeper-backup-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            installed = path.read_bytes()
            install_hook(path, cmd)
            self.assertEqual(path.read_bytes(), installed)
            for variant in ('override', 'duplicate', 'foreign'):
                broken = json.loads(installed)
                target = broken['hooks']['SessionStart'][1]['hooks'][0]
                if variant == 'override':
                    target['commandWindows'] = cmd[2:]
                elif variant == 'duplicate':
                    broken['hooks']['SessionStart'][1]['hooks'].append(dict(target))
                else:
                    target['command'] = cmd + ' --foreign'
                before = json.dumps(broken).encode()
                path.write_bytes(before)
                with self.assertRaises(ValueError):
                    install_hook(path, cmd)
                self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
