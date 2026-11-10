# Codex Context Keeper

Standalone desktop application with a Russian UI. Windows release and Debian13
desktop preview; see [LINUX.md](LINUX.md) and [VM_HANDOFF.md](VM_HANDOFF.md).
Local-only archives and
attributed bounded preservation, plus global native context configuration.
Public source distribution; no user archives or private test logs are included.

**License:** original application code is MIT; the bundled reference bridge
component is noncommercial under its own license. The complete build is **not
MIT-only**. Preserve both license files and the runtime notices in `licenses/`.

## Run

Version 0.3.7 adds live context counters and idle reminders, compressed transcript
support, project-grouped chat selection and crash-safe archive locking. Native
compaction remains explicitly unsafe and separately consented; context reminders
do not request compaction.
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
state. Registry/archive writes use atomic replacement and OS-held file leases;
process death releases the lease automatically. The small lock file remains
and must not be deleted to unlock an active process. Power-loss filesystem
durability is not established. Large changed files are read/regenerated in
memory; no cloud upload occurs and this is not a scalable archival server.
Unchanged warm archives use per-Store file signatures (identity, size and
modification/change timestamps), including derived files. A new Store always
validates cold sources. Source/derived changes invalidate the fast path. This
is local file-change detection, not protection against an adversary deliberately
rewriting files while preserving all metadata.

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

A context-read request made during archive work is queued once and starts when
the current operation finishes. Later edits to the input fields are preserved.
Save/reset clearly require the current operation/read to finish; they are never
silently queued or applied without their existing confirmation.

The per-thread preparation threshold is an advisory warning based on the latest
available measurement, not a true model window or an automatic compact trigger.
The installed protocol has `config` on `thread/start` and `thread/resume`, but
live Desktop-owner application of both limits has not been verified. The app
does not offer a misleading per-thread override button or rewrite globals when
selecting chats. No independent competing `thread/resume` owner is started.

## Optional restoration hook

Windows hook commands invoke the quoted executable with PowerShell's `&`
operator. A bare quoted executable followed by `--keeper-hook` is a parser
error in PowerShell, even when direct process invocation or `cmd.exe` succeeds.
The installer recognizes only this installation's exact older command and
updates it in place with a backup, preserving other handlers and chat settings.
After the definition changes, review/trust it again in Codex; a synthetic
SessionStart check does not establish delivery during real compaction.

Choose documents and check per-thread **Автовозврат**. If the hook is missing,
the checkbox offers the explicit one-time **Установить хук…** action. The
selected hooks.json is merged without changing existing handlers and backed up.
Then review/trust the hook in Codex (`/hooks`); the read-only **Проверить
подключение (без сжатия)** button reports Codex's enabled/trust state. Neither
installation nor that status proves live delivery after compaction. The app
defaults to a USER hooks.json, not the existing project hooks.json. Removing
only this exact command preserves other entries. Paths with shell interpolation
characters are rejected.

Hook routing requires `SessionStart`, `source=compact` and exact registered
session/thread ID; unknown/disabled/protected threads produce no context.
The hook never acquires the archive writer lease, scans full histories, or writes
the preview file. It restores saved bookmarks from the last successful archive
sync and reads current memo/notes at delivery. The envelope states this freshness
boundary and the archive sync timestamp. New message markers become eligible
after the next successful explicit/GUI archive sync; the hook does not process
unsynchronized markers. Unrelated long archive work cannot block memo delivery.
Actual invocation/trust and successful delivery still depend on Codex.

When Codex supplies a transcript path, the hook verifies that it identifies
the same file as the registered source; Windows extended and ordinary path
spellings of that file are accepted, while a different or missing file fails
closed.
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

## Management MCP

`ContextKeeperHook.exe --mcp-management --data <installed-data> --codex-home <home>`
exposes five stdio tools: `list_available_chats`, `list_connected_chats`,
`connect_chats(thread_ids)`, `disconnect_chats(thread_ids)` and
`organize_archives()`. This is explicit
local administration, without authenticated per-chat caller identity. Configure
it only for trusted clients. Lists contain metadata, not transcript contents.
Connection uses the same archive/registration code as the GUI and enables all
per-chat switches by default, including unsafe compaction consent. This process
does not create a compaction queue/worker or install/trust hooks. Another GUI or
compaction worker may later dispatch existing requests after connection consent.
Disconnect requires explicit IDs, backs up the registry and preserves original
Codex chats and archive folders. Reconnection restores the same archive folder
and notes, including after title changes, with an ownership check.

Archive layout is `data/archives/<project>/<chat>/`, with stable disambiguating
IDs in folder names and a separate unassigned-project folder. Registry paths
support both old flat folders and the new layout. `organize_archives` moves
existing active/disconnected archives after checking ownership, backs up the
registry, and commits each chat separately. A durable pending destination allows
recovery after an interruption between the folder move and registry publication.
It never deletes archive contents. The main registered-chat list is also grouped
by project. **Отключить всех, кроме выделенных…** supports Ctrl-selection or a
whole project folder and asks for confirmation; empty selection is rejected.
This button disconnects registrations while preserving their archive folders.
The History button reads the saved Markdown explicitly as UTF-8 and displays it
inside Keeper, without relying on Windows file associations or recoding the
archive. Folder/Memo/Notes actions retain their external-opening behavior.
Connection progress is delivered from the worker through the GUI queue: current
chat/count/stage, an animated activity bar and elapsed time remain visible.
Newly registered chats appear before archive synchronization completes, their
project folder opens, and completion reveals the connected selection. Registry
changes from another management client also refresh the open list. Manual ID
addition uses the same all-switches-enabled connection flow as the picker.
The window is resizable; normal dimensions and maximized state are remembered
in `data/window.json` on normal close and restored within screen limits.
When `relocation.json` names a previous installation, hook checks retain the
existing configured command only if both hook executable and data directory are
the same files/directories via a compatibility junction. Unrelated hooks are
not adopted or rewritten; the path-only move does not change the trust definition.

Codex setup (user-level `config.toml`; command paths must match the installation):

```toml
[mcp_servers.context_keeper_management]
command = "E:/Tools/ContextKeeper/ContextKeeperHook.exe"
args = ["--mcp-management", "--data", "E:/Tools/ContextKeeper/data"]
tool_timeout_sec = 180
```

Configuration is not proof of connection in an already-open chat. Verify the
actual tool inventory after the client's supported MCP reload/reconnect.
Official setup: https://learn.chatgpt.com/docs/extend/mcp (accessed 2026-11-01).

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
It stays `deferred` if unsafe opt-in is missing. There is no silent global auto-enable.

**Выбрать чаты…** opens a tree of local Codex projects with their member chats,
followed by **Чаты вне проекта**. Clicking a project selects/deselects all its
available members; individual chats can also be selected, including chats outside
projects. A partial project selection has a distinct marker. Nothing is written
until **ОК** and the explicit bulk-consent warning, including the same unsafe
compaction risk, are confirmed. Cancel/closing the picker changes nothing.
Chats without a nonblank title or an existing transcript file are omitted from
the picker and its group counts. Protected chats with available metadata/files
remain visible but disabled. Membership uses explicit
Desktop assignments and native database project IDs;
projectless/remote-host chats are excluded, and cwd alone is never membership.
Legacy project aliases are resolved through Desktop's local migration map.
It enables History, Memo, Notes,
auto-restore, MCP requests and unsafe native compaction per chat. Existing notes,
bookmarks, folders, thresholds and restoration limits are preserved. Archives
are synchronized before enabling permissions; missing rollout files are skipped
without creating empty registrations, with a summary count. Per-chat failures are reported and
do not prevent other chats from connecting. A failed new chat may remain
registered with its permissions off; retrying the button completes setup.
The hook is checked/installed through the existing flow; Codex trust remains a
separate requirement. The button does not compact, enqueue requests, change
global limits or create per-chat MCP server attachments. Existing queued requests
may dispatch on subsequent worker updates after consent. Future chats require
another click. Remote-host and ChatGPT chats are not supported by the local store.
Only selected IDs are connected; other existing registrations and archives remain
unchanged. Already dispatched native compaction cannot be undone. Periodic/manual
bulk refresh summarizes unavailable sources rather than listing every missing old
rollout. Sources disappearing after selection are skipped during connection too.

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
# Compressed Codex transcripts (2026-11-10)

## Live context counter and reminders

The **Живой контекст и предупреждения** tab displays used/window/remaining tokens,
percentage and observation time from the existing Codex Desktop owner. It never
substitutes configured limits, cumulative expenditure or a stale rollout record.
The metric is a point-in-time observation, not a prediction of the next request
or an independent measurement of model capacity. Unknown/unloaded chats remain
explicitly unavailable and are never resumed for monitoring.

Per-chat reminders default to 90% when connecting a chat and may be disabled or
reconfigured in the GUI or `configure_context_warnings` MCP tool. The GUI also
offers explicit bulk configuration. `get_context_status` returns a fresh counter
for one registered chat without sending messages or compacting. The connected
list includes reminder settings and last monitoring state.

Monitoring runs only while Keeper GUI is open, independently from archive work.
At most four bounded native probes run at once, followed by a 30-second wait.
Reminders require a live idle owner and a known occupied-context/window ratio;
the sender rechecks owner, idle, threshold and context epoch immediately before
submitting. Like existing native IPC, this is not an atomic guarantee against
concurrent human input. The reminder is a normal chat message and may receive a
model reply. It asks to update the memo and explicitly does not request compaction.

`context-warnings.json` stores bounded per-registered-chat observations and the
latest send intent. A single OS lease prevents competing watchers. An intent is
committed before sending; accepted/uncertain intents are not repeated in the same
observed context/threshold, including after restart. A confirmed no-send can retry.
A changed completed-compaction fingerprint/window re-arms the reminder. A usage
dip or the reminder's own reply does not re-arm it. Uncertain delivery is reported,
not automatically retried. Removing registrations prunes monitoring state.

Keeper accepts local `.jsonl` and `.jsonl.zst` rollouts. It prefers an existing
plain rollout, otherwise checks its exact compressed sibling. Decompression
supports multiple frames and validates frame completion before replacing any
archive; offsets and hashes refer to decompressed JSONL bytes. Corrupt, truncated
or foreign-thread input leaves the previous archive intact. Source files are
never decompressed in place or modified. Source/build installs require
`zstandard==0.25.0`; packaged executables include it and its license notices.

The picker shows named, nonarchived local chats with available transcripts and
excludes subagents and automation/internal service threads. Explicit native/legacy project assignments take precedence;
an unambiguous saved sidebar project membership is a fallback only when there
is no assignment. Explicit projectless and remote-host markings remain binding.
No project is inferred from a working directory. ChatGPT/cloud-only conversations
without a local Codex transcript remain unsupported.

