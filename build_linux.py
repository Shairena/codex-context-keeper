"""Cross-publish a Linux bridge and collect its exact runtime pack notices."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', choices=('linux-x64', 'linux-arm64'), default='linux-x64')
    parser.add_argument('--output', type=Path, default=Path('dist'))
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    output = args.output.resolve()
    cache = Path(os.environ.get('NUGET_PACKAGES', source / '.native-build/packages')).resolve()
    subprocess.run(['dotnet', 'publish', str(source / 'bridge/ContextKeeperBridge.csproj'),
        '-c', 'Release', '-r', args.runtime, '-o', str(output), '--nologo',
        '-p:DebugType=None', '-p:DebugSymbols=false', '-p:PathMap=' + str(source) + '=/_/ContextKeeper',
        '-p:RestorePackagesPath=' + str(cache)], check=True)
    assets = json.loads((source / 'bridge/obj/project.assets.json').read_text(encoding='utf-8'))
    name = 'Microsoft.NETCore.App.Runtime.' + args.runtime
    versions = {row['version'].strip('[]').split(',')[0].strip()
        for framework in assets['project']['frameworks'].values()
        for row in framework.get('downloadDependencies', []) if row['name'].lower() == name.lower()}
    if len(versions) != 1:
        raise ValueError('Could not identify one restored native runtime pack version')
    pack = cache / name.lower() / versions.pop()
    notices = output / 'licenses'
    notices.mkdir(parents=True, exist_ok=True)
    for original, target in (('LICENSE.TXT', 'DOTNET-LICENSE.txt'),
            ('THIRD-PARTY-NOTICES.TXT', 'DOTNET-THIRD-PARTY-NOTICES.txt')):
        shutil.copyfile(pack / original, notices / target)
    print(json.dumps({'runtime': args.runtime, 'output': str(output), 'notices_copied': True,
        'runtime_execution_verified': False}))


if __name__ == '__main__':
    main()
