# Debian 13 desktop installation

Linux support in this release is a preview. The code and linux-x64 native helper
are prepared for Debian13 (including13.6), but actual Debian GUI/live Desktop
acceptance must be performed on the target VM. Windows tests and cross-publishing
are not a Debian runtime pass. No system upgrade or compaction is required.

## Install as the desktop user

Download `ContextKeeper-0.3.8-linux-x64.tar.gz` from this repository's v0.3.8
release, rather than GitHub's automatic source-code zip. Extract it in a writable
working directory. The Linux archive includes Python source and a self-contained
Linux native bridge; Python/Tkinter are supplied by Debian, not bundled.

Install prerequisites using the VM's normal administrator process:

```sh
sudo apt-get install python3-venv python3-tk xdg-utils
```

The bridge additionally needs the standard .NET9 native runtime dependencies
(including libc, libgcc, libstdc++, OpenSSL and ICU); the .NET SDK is not required
for the prebuilt binary. Run the installer as the same Unix user as Codex:

```sh
python3 install_linux.py --check-only
python3 install_linux.py --register-mcp --verify-gui
```

`codex` must be on PATH. Otherwise set `CODEX_CLI_PATH` to the actual installed
Codex CLI executable; do not guess a Windows path. The installer creates a
private user-local prefix at `${XDG_DATA_HOME:-$HOME/.local/share}/context-keeper`
with a venv, runtime source, launchers and a separate data directory. An existing
Codex config is backed up privately before the CLI registers the MCP entry.
Existing Keeper archives are retained. No chats are connected automatically.
An existing nonempty prefix must carry this installer's ownership marker;
otherwise installation stops. Choose a new empty prefix instead of overwriting
an unrelated directory. Interrupted owned installations may be retried.

The final JSON prints `gui_launcher`, `mcp_launcher`, `data`, version and checks.
Run the reported GUI launcher. It requires an accessible graphical session and
DISPLAY (Tk on Wayland normally uses XWayland). A successful import is not a
verified display; `--verify-gui` runs an isolated temporary GUI smoke.

## Verify the active Codex chat

The management server must advertise seven tools and version0.3.8. Reload its
MCP entry in the current client if necessary. Verify actual tool availability in
the chat; standalone handshake success alone does not establish that binding.

Connect only the explicitly selected chat ID. A connection enables the
preservation/reminder switches. Both compaction permissions remain off and
require separate per-chat opt-in. Do not
connect unrelated VM chats as an installation side effect.

Call `get_context_status` for the selected registered ID and record whether the
owner returns a live metric. Unloaded owners or an incompatible Desktop protocol
must return unavailable, never guessed values. Opening the Keeper GUI starts
monitoring; the default warning threshold is90%, configurable per chat.

The Linux bridge uses the Codex home `ipc/ipc.sock`, with the current app's
long-path indirection, and checks the connected peer's Unix UID. Both processes
must run as the same user. `CONTEXT_KEEPER_IPC_SOCKET` can specify an observed
socket path if the installed app uses another location; peer authentication is
still mandatory. The UID check authenticates a Unix user, not a specific
executable. This is private versioned IPC and remains experimental until
tested with the VM's actual Desktop build. Codex CLI alone has no Desktop owner.

## Hook and final acceptance

The GUI can install its POSIX-quoted launcher command into the selected hooks
file, preserving other handlers and creating a backup. Review/trust the hook
using Codex's own supported controls. Synthetic tests or a trusted hook entry
do not prove delivery during actual compaction.

Do not trigger real compaction or send a real reminder during installation.
Those checks require a separate user-selected test chat and explicit user
authorization. Report these statuses separately: installed, GUI smoke, stdio
handshake, active-chat MCP visibility, live counter, hook configuration/trust,
live hook delivery. Do not call an untested category successful.

## Build from source instead

With a .NET9 SDK matching the target environment:

```sh
python3 -m venv .buildenv
.buildenv/bin/python -m pip install -r requirements.txt
python3 build_linux.py --runtime linux-x64 --output dist
.buildenv/bin/python -m unittest discover
```

Run GUI tests in the desktop session or under `xvfb-run` if explicitly installing
a disposable test display. The Windows PowerShell parser test is Windows-only;
the Linux suite has a real POSIX argv equivalent and a synthetic Unix-socket
counter check when `dist/ContextKeeperBridge` is present. For ARM64, build with
`--runtime linux-arm64`; the installer verifies the ELF matches the running architecture.
The prebuilt release asset is x64.
