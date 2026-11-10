# Public release 0.3.7 verification

2026-11-10. Public source and Windows release contain no user chat archives,
personal configuration, private acceptance reports or real-chat test logs.

This release includes project-grouped chat selection, compressed JSONL/Zstandard
transcripts, crash-safe leases, archive-independent memo delivery, unchanged-file
sync caching, queued context-setting reads, and live context counters/reminders.
Release labels in CLI, MCP, native-client initialization and build manifest share
one version constant. Source examples and fixtures use generic paths/names.

## Verified scope

- Windows-hosted source suite:29 tests,27 passed and2 explicitly skipped because
  they require real Linux (POSIX execution and Unix-socket IPC). Synthetic smoke
  passed. Installer preservation and release-version regression checks passed.
- Rebuilt Windows package: CLI/MCP version0.3.7, seven MCP tools, isolated GUI
  smoke, protected synthetic input rejection and manifest hashes verified.
- The rebuilt Windows native bridge successfully read an existing Desktop owner
  counter without sending a message or compacting. No real-chat logs are included.
- Bounded known-private-marker scans of source, raw Windows executables and
  extracted CArchive/PYZ contents found no known target markers. This is not a
  proof that every possible sensitive string is absent.
- Linux native bridge: cross-published ELF64 x86-64 with runtime notices;
  header and bounded marker checks passed. Actual Debian installation, GUI,
  Unix peer validation and live Codex integration have not run on this host.
  Linux is a preview for target-VM acceptance using `VM_HANDOFF.md`.

No real compaction is used as part of public release verification. Release
archives use an explicit file allowlist; runtime/user data and private review
receipts are excluded. Public binary hashes are shipped with the release.

## Limits and consent

- The live counter uses the existing Desktop owner's latest observed metric;
  it does not infer capacity from global config or cumulative expenditure.
- Monitoring requires the GUI to be open and does not resume unloaded chats.
- A connected chat enables the advertised switches after the GUI's explicit
  confirmation, including reminders and the known-unsafe compaction permission.
  Direct low-level registration starts with switches disabled.
- A native submission is not atomic against concurrent human input. Ambiguous
  delivery is not blindly retried. A reminder is a normal chat message, may
  receive a reply, and does not request compaction.
- The restoration hook reads saved bookmarks and current memo/notes without
  scanning or locking full archives. Bookmarks reflect the last successful sync.
- Private, versioned Desktop IPC may change after app updates. No universal hook
  invocation, delivery, visual/DPI compatibility or large-file RAM bound is claimed.
- Original source is MIT; the reference bridge component has a separate
  noncommercial license. The complete package is not MIT-only. Preserve all
  source, reference and bundled runtime notices.
