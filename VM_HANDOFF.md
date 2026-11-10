# Instructions for the VM's Codex chat

User request: install Context Keeper from this repository on the user's Debian
13.6 desktop VM, so the selected Codex chat can use its GUI and management MCP.
Use `LINUX.md` and report each acceptance category truthfully.

1. Confirm `/etc/os-release`, `uname -m`, current Unix user, Python version,
   accessible desktop session, Codex CLI executable and Codex home. Work in a
   separate writable directory. Do not modify unrelated projects or VMs.
2. Obtain the matching Linux release asset and verify its published SHA-256.
   Do not run the Windows EXEs or transfer Windows user state/archives.
3. Inspect/run the Linux installer preflight. Install missing Debian dependencies
   through the user's normal admin mechanism, not by changing security settings.
4. Run `python3 install_linux.py --register-mcp --verify-gui` as the same user as
   Codex. Keep the private config backup and existing archives.
5. Confirm seven tools/version0.3.7 in the isolated stdio check, then verify those
   tools are actually callable in this chat after MCP reload. Do not mistake a
   successful separate process for a refreshed active-chat tool binding.
6. Register only the chat ID explicitly selected by the user. Read its live
   counter and report exact denominator/remaining/observation time or unavailable.
7. Inspect the POSIX hook command and Codex trust state. Do not claim live
   restoration based only on configuration or synthetic checks.
8. Never compact, change model/context limits, send an unsolicited test message,
   connect unrelated chats, delete archives or alter another VM during setup.

Final report: installed version/path; OS/Python/architecture; GUI smoke;
configured MCP and actual active-chat tools; live counter; hook configured/trusted
versus delivery tested; exact errors and next actions. Preserve negative results.
Linux native runtime acceptance is pending until this report exists.
