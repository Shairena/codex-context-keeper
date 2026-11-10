"""Platform paths and file launching shared by GUI and native driver."""
import os
from pathlib import Path
import subprocess
import sys


def app_root():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent


def hook_executable():
    base = app_root()
    if sys.platform == 'win32':
        return (base if getattr(sys, 'frozen', False) else base / 'dist') / 'ContextKeeperHook.exe'
    return base / 'ContextKeeperHook'


def bridge_executable():
    base = app_root()
    if not getattr(sys, 'frozen', False):
        base /= 'dist'
    return base / ('ContextKeeperBridge.exe' if sys.platform == 'win32' else 'ContextKeeperBridge')


def open_document(path):
    if sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
