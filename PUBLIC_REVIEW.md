# Public release 0.2.1 verification

2026-10-15. This public summary intentionally contains no private chat IDs,
names, user paths, archives or real-chat test logs.

- Independent synthetic privacy/protection checks: **5 passed, 0 failed**.
- Narrow archive/bookmark/config-read smoke and Python compilation passed.
- Native bridge build passed with no warnings/errors.
- Fresh packaged bridge: a protected synthetic UUID was rejected before IPC;
  an unprotected synthetic UUID reported no client found in the isolated home.
- Generic exclusions, missing/default configuration, malformed fail-closed
  behavior, Python protection gates, selected-home propagation and license
  separation were checked without accessing real chat content.
- Release source allowlist excludes private history, review artifacts, data,
  build environments and prior binary packages. Bundled executable archives
  were scanned for the removed private markers; no exact marker was found.

An invalid initial test invocation used a reserved PowerShell variable and
created a synthetic file outside its intended test directory. That invocation
was excluded; the accidental new file was removed and corrected isolated tests
passed. The real protection configuration was untouched.

This is bounded synthetic acceptance, not a visual/DPI matrix or a live
compaction rerun. External native compaction remains explicitly unsafe against
concurrent user input. Per-chat consent defaults off. Private versioned Desktop
IPC may break after app updates. Source and reference/runtime licenses must be
preserved; the complete package is not MIT-only.
