"""Collect upstream redistribution notices without copying local configuration."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    parser.add_argument('--runtime-pack', type=Path, required=True)
    args = parser.parse_args()
    target = args.dist / 'licenses'
    target.mkdir(parents=True, exist_ok=True)
    python = Path(sys.base_prefix)
    pyinstaller = importlib.metadata.distribution('pyinstaller')
    sources = {
        'PYTHON-LICENSE.txt': python / 'LICENSE.txt',
        'TCL-TK-LICENSE.txt': python / 'tcl' / 'tk8.6' / 'license.terms',
        'PYINSTALLER-COPYING.txt': Path(pyinstaller.locate_file('pyinstaller-' + pyinstaller.version + '.dist-info/licenses/COPYING.txt')),
        'DOTNET-LICENSE.txt': args.runtime_pack / 'LICENSE.TXT',
        'DOTNET-THIRD-PARTY-NOTICES.txt': args.runtime_pack / 'THIRD-PARTY-NOTICES.TXT',
    }
    for name, source in sources.items():
        if not source.is_file():
            raise RuntimeError('Required runtime notice missing: ' + name)
        shutil.copyfile(source, target / name)
    manifest = {'version': '0.2.1', 'license': 'MIT for original application; noncommercial reference component', 'files': {}}
    for name in ('ContextKeeper.exe', 'ContextKeeperHook.exe', 'ContextKeeperBridge.exe'):
        path = args.dist / name
        manifest['files'][name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (args.dist / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Runtime notices and path-free release manifest prepared.')


if __name__ == '__main__':
    main()
