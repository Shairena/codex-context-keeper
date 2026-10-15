# Codex Context Keeper

Standalone Windows application with a Russian UI. Local-only archives and
attributed bounded preservation, plus global native context configuration.
Public source distribution; no user archives or private test logs are included.

**License:** original application code is MIT; the bundled reference bridge
component is noncommercial under its own license. The complete build is **not
MIT-only**. Preserve both license files and the runtime notices in `licenses/`.

## Run

Version 0.2.1 includes an explicitly unsafe, separately consented native compact route.
Run `ContextKeeper.exe` from the extracted release (or `dist/ContextKeeper.exe`
after a source build). Keep `ContextKeeperHook.exe` and
`ContextKeeperBridge.exe` beside it, with `REFERENCE-LICENSE.txt`.
Python is bundled in these executables. Codex Desktop/CLI must be installed for
native configuration operations. The default writable data directory is `data`
beside the executable. To choose another isolated store, pass `--data PATH`.
`--codex-home PATH` selects a different local metadata/configuration home both
for archive reading and for the configuration RPC child environment. This is
also used to isolate synthetic verification from actual user settings.

1. Paste an existing local Codex thread UUID or `codex://threads/UUID` and click
   **Добавить чат**. The actual metadata title is resolved read-only from the
   newest `state_*.sqlite`. Remote/cloud/ChatGPT threads are not supported.
2. Each thread gets `sanitized original title -- FULL UUID`. Later title changes
   update the displayed actual title but deliberately keep the directory stable.
   Duplicate titles cannot mix data. Memo and notes start genuinely empty.
3. **Обновить архивы** exports available originals. Automatic updates occur every
   30 seconds while the GUI is open. The source itself is never modified.
4. Open/edit memo and notes through the document buttons or import a UTF-8 file.
   Replacing an existing imported document creates a local backup.
5. Check **История**, **Мемо**, **Заметки** to choose restoration inputs.
   History means bookmarks and explicitly selected search results, NEVER the
   whole archive. Memo/notes mean whole documents; oversized ones are omitted,
   not truncated. Preview shows the exact bounded data without sending it.
6. Search returns the first 30 matching complete messages. The table excerpt is
   only a display abbreviation; **Показать выбранное** opens the whole message.
   **Закрепить цитату** preserves the exact selected substring with attribution.
   Reusing its ID updates it; **Закладки** allows unpinning. Message markers
   `[[память:id]]` and `[[забыть:id]]` are also recognized outside Markdown fences,
   quotes and indented examples, only in user/positively-final assistant messages.

## Files and completeness

- `source.jsonl`: byte-identical complete source records, including opaque
  records. Partial writer tails are deferred until their newline arrives.
- `history.jsonl` and `history.md`: available `response_item/message` originals
  in source order, including roles/timestamps/IDs/channel when available and
  attachment references. Analysis messages are not rendered or decrypted.
- `compacted-view.json`: replacement/summarized records stored separately;
  NEVER relabelled as original messages or used to create bookmarks.
- `memo.md`, `notes.md`: per-thread user-maintained documents, initially empty.
- `bookmarks.json`: attributed active entries/tombstones and parser warnings.
- `observed.json`: latest rollout token observation, explicitly possibly stale.
- `restoration-preview.txt`: whole-entry bounded selection with omission count.

The application cannot recover missing/deleted/nonlocal messages, decrypt opaque
reasoning, or copy unavailable attachment contents. Records of other types are
retained verbatim in `source.jsonl` rather than invented as conversation text.
This is a full archive of the available rollout, not a claim to possess all
historical server data. Source truncation/replacement fails closed and keeps the
old archive. Malformed complete records stop synchronization without resetting
state. Registry/archive writes use atomic replacement and exclusive lock files;
an interrupted process can leave a lock requiring manual inspection. Power-loss
filesystem durability is not established. Large files are read/regenerated in
memory; no cloud upload occurs and this is not a scalable archival server.

## Context controls

**Прочитать из Codex**, **Сохранить**, **Вернуть стандартные** operate on the
two native user-layer keys `model_context_window` and
`model_auto_compact_token_limit` through `config/read` / `config/batchWrite`.
Writes use `expectedVersion`, touch only changed keys and reread confirmation.
Reset sends explicit removals only for existing overrides. No writes happen
at startup, row selection, synchronization or during development smoke.
These overrides are GLOBAL. Effective app-server configuration is separate
from an observed chat's last measurement. Applying to already-live sessions is
not established. A larger configured window cannot expand model capacity and
does not imply that all available tokens are consumed.

The per-thread preparation threshold is an advisory warning based on the latest
available measurement, not a true model window or an automatic compact trigger.
The installed protocol has `config` on `thread/start` and `thread/resume`, but
live Desktop-owner application of both limits has not been verified. The app
does not offer a misleading per-thread override button or rewrite globals when
selecting chats. No independent competing `thread/resume` owner is started.

## Optional restoration hook

Check per-thread **Автовозврат**, choose documents, then explicitly use
**Установить хук…**. The selected hooks.json is merged without changing existing
handlers and backed up. Installation does not establish trust or live delivery;
Codex may require review/reload. The app defaults to a USER hooks.json, not the
existing project hooks.json. Removing only this exact command preserves other
entries. Paths with shell interpolation characters are rejected.

Hook routing requires `SessionStart`, `source=compact` and exact registered
session/thread ID; unknown/disabled/protected threads produce no context.
Optional private exclusions are loaded from
`context-keeper-protected-threads.json` in the selected Codex home: a JSON array
of thread UUID strings. Excluded chats cannot be registered, synced or compacted.
This local file must never be published. Malformed protection settings stop the
operation rather than silently disabling protection. No personal IDs are built in.
Quotes are JSON data with original attribution and an explicit untrusted-data
wrapper. Codex still transports hook output as developer context: the program
cannot change that transport's privilege; this is why stored text is explicitly
data, not trusted instructions. Selected search hits are manual-preview-only;
automatic history restoration contains active bookmarks, never entire history.
Default restoration budget is 6000 characters, configurable 1000–32000. Whole
entries are omitted visibly, never cut. Hooks are off by default, and no actual
hook configuration was edited during development.

## MCP after-turn compaction — KNOWN UNSAFE, separate opt-in

The app exposes a small stdio MCP server via `ContextKeeperHook.exe --mcp
--bound-thread UUID --data PATH --codex-home PATH`. The **MCP-конфигурация** button
shows JSON and Codex TOML examples; it does not write native client config.
Explicitly permit requests for that registered chat first. Configure the server
only for the intended chat/client use. Stdio MCP does not authenticate an actual
chat caller; the fixed server binding and validated source turn are explicit
limitations, not automatic identity proof.

- `request_compaction_after_turn(origin_turn_id)` returns a durable request ID
  and queued status without compacting or interrupting the turn. Origin turn
  must exist in the bound thread's available original source metadata.
- `get_compaction_request(request_id)` reports status.
- `cancel_compaction_request(request_id)` cancels before dispatch.

**Confirmed risk:** native compact can interrupt a newly started user answer if
input arrives between an external idle check and compact RPC. Native input
during an actual compact was explicitly rejected with ActiveTurnNotSteerable.
These findings remain valid; user explicitly requested connection despite this
risk. This is NOT an atomic or safe driver.

Real dispatch defaults OFF for every chat. Existing queue-only permission is
insufficient. In **Восстановление после сжатия**, permit MCP requests AND use
**Небезопасный реальный запуск: включить / выключить для чата…** to accept the
explicit warning for that one chat. The same warning appears in selected-chat
details and MCP tool descriptions. Without either permission, no real compact.
It stays `deferred` if unsafe opt-in is missing. There is no global auto-enable.

The worker waits for native completed originating turn and live idle, syncs
archives/selected preservation, commits durable dispatch intent, then the native
bridge rechecks the same current owner, originating completion and idle. The
reference stream checks idle again before RPC. These checks REDUCE but DO NOT
ELIMINATE the proven concurrent-input race. Never send a new message while
relying on uninterrupted work during this unsafe external compaction.

Native completion requires a new real GUID contextCompaction item, completed
turn, same owner and idle. ACK alone is never completed. Known not-started checks
return to waiting; timeout/malformed/ambiguous possible dispatch becomes terminal
`ambiguous` with no automatic retry. Cancellation before dispatch and withdrawal
of consent are rechecked at intent commit. Disabling consent cannot undo an
already sent RPC. Crash/restart intent is ambiguous, never resent. An OS-held
cooperating-worker lease prevents GUI/MCP processes dispatching concurrently and
releases on process death. This does not lock the user's Desktop input.
Normal MCP EOF and GUI close drain the current operation before exit to avoid
stranding an archive lock. Forced termination/power loss may still require
manual lock inspection; an already dispatched native operation is not undone.

The bridge discovers/follows the existing Desktop owner and never resumes a
second app-server owner. It uses private versioned IPC, which can break on app
updates. Run the app or its MCP worker to process the durable queue. Selected
document restoration still requires the separate optional hook/trust setup;
enabling unsafe compact does not silently install hooks or prove delivery.

## Build and verification

`build.ps1` uses .NET 9 SDK to publish a self-contained native bridge and a local
venv with pinned PyInstaller 6.22.3 for the GUI and console hook/MCP EXEs.
End users do not need Python or .NET installed. `python -B smoke.py` is only a narrow synthetic local
smoke. Private review artifacts and real-chat logs are intentionally excluded
from the public repository. Synthetic smoke checks do not prove concurrent-input
safety; the known unsafe behavior described above remains a release limitation.

## Provenance and sources

Global configuration RPC is a fresh implementation. Version 0.2 native bridge
includes UNMODIFIED Task Overseer `CodexContextStream.cs` and
`CodexContextSnapshot.cs` under `bridge/Reference`, copyrighted by WSSAWER and
governed by LicenseRef-WSSAWER-Noncommercial-1.0. This local personal-use package
preserves that license as REFERENCE-LICENSE.txt; those components are NOT offered
as unrestricted commercial code. Keeper bridge wrapper/queue/UI changes are
identified separately; existing Task Overseer installation is not modified.
The marker algorithm was adapted from an earlier local prototype by the same
project owner; no private prototype files or state are distributed.

Official sources accessed 2026-10-14:
- https://learn.chatgpt.com/docs/app-server — config RPC and compaction lifecycle.
- https://learn.chatgpt.com/docs/hooks — SessionStart routing, trust, developer
  context transport and additionalContextLimit.
- https://learn.chatgpt.com/docs/config-file/config-reference — native limits.

Installed `codex.exe app-server generate-ts --experimental` output in ignored
`review_artifacts/protocol` establishes this version's request shapes only.
Only the explicitly user-authorized disposable test chat is used for live native
checks. Other real conversations, private notes/store and Task Overseer
secrets are excluded. Actual global values and existing hooks were not changed.
