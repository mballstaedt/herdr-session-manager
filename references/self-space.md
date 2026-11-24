# Reconcile and move this Codex pane

Use only for the user's explicit request to identify or move **this conversation's
pane**. This is a self-only exception to the ordinary caller guard, not a general
inventory or recovery admission. It neither repairs Herdr aliases nor persists a
new identity. The default `verify_context` guard remains unchanged.

## Required trusted inputs

- An existing Herdr-managed process (`HERDR_ENV=1`).
- The exact current conversation ID supplied by the Codex host as
  `CODEX_THREAD_ID`. Do not set, replace or synthesize this variable. Do not take
  the ID from a title, cwd, focus, pasted inventory or a guessed session. If this
  host does not supply it, stop; this implementation does not support that host.
- An independently confirmed intended server socket, provided as
  `--expected-socket`. Obtain it from the operator or an already verified owner
  on the intended server. Do not read the inherited socket and relabel it as an
  independent expectation. This argument compares only: it never changes routing.
  If the expectation is unavailable, ask only for that missing endpoint evidence.

## Preview and apply

### Shared-daemon native-client integration

When Codex uses a shared daemon, require `--native-client` on both commands below.
This requires experimental native Codex frontend and Herdr receiver patches that are
not included in this repository; stock Herdr 0.9.5 does not implement these methods,
so without them use the hook-metadata or operator-selected paths. Installation is separate from skill
discovery. Do not call an installed skill proof that the native integration is live.

```sh
python3 -B <skill-dir>/scripts/inventory.py reconcile-context --native-client --expected-socket <CONFIRMED_SOCKET> --json
python3 -B <skill-dir>/scripts/inventory.py move-self-space --native-client --expected-socket <CONFIRMED_SOCKET> --label "HERDR SKILL WORK" --json
```

Preview reads only `agent.resolve_native_client` on the compared endpoint. The
pane-local native frontend registers its displayed root directly; the receiver
derives the connecting process identity from the kernel and verifies its live
terminal foreground ownership. Claims expire after five seconds, duplicate live
claims are refused, and the returned native client identity must survive the move.
The skill never registers ownership, changes inherited variables, or falls back
to legacy metadata when this path fails. Missing, expired, ambiguous, unsupported
or malformed native identity stops the operation. General inventory
admission retain their existing guards.

Preview is read-only. Apply resolves afresh, snapshots all workspace topology,
checks native process continuity, performs one move, and resolves automatically
again afterward. Legacy hook session metadata may be absent; the native binding
is measured separately and is never written into that legacy field. Unrelated
pane/tab/workspace topology must remain unchanged apart from source counts and
removal of an emptied source tab, or the emptied source space when its last pane
moves. Source-space removal must match the native receipt; the helper issues no
separate close command. Existing CLI dispatch race limitations below
apply only to the legacy path. Native apply uses `agent.move_native_client`,
which revalidates the expected thread/client/terminal and executes the existing
move in the same server handler. This removes the separate resolution/CLI-dispatch
gap; the claim still represents the frontend's most recently reported state.

### Existing hook-metadata path

```sh
python3 -B <skill-dir>/scripts/inventory.py reconcile-context --expected-socket <CONFIRMED_SOCKET> --json
python3 -B <skill-dir>/scripts/inventory.py move-self-space --expected-socket <CONFIRMED_SOCKET> --label "PERSONAL AI INSTRUCTIONS" --json
```

Both are read-only. `move-self-space` defaults to preview. For an explicitly
requested move, add `--apply` to the second command. Routine preview does not
require another approval; task authorization and host execution permissions are
separate. A preview is not a reusable identity token: apply resolves afresh.

The helper checks status on the inherited server, attempts the ordinary current
pane lookup, and allows fallback only for a structured `pane_not_found`. On that
exception it may list pane metadata on that same confirmed server, match the
trusted conversation ID exactly once, and confirm it with direct pane metadata.
Access denial, unknown errors, wrong endpoint, missing/duplicate identities, or
contradictory current-pane identity stop the operation. It never reads terminal
output, transcripts or process arguments, and never rewrites environment IDs.

`move-self-space --apply` moves only the just-verified pane with
`pane move --new-workspace --no-focus`. It uses ALL CAPS labels and verifies the
returned source identity, unchanged terminal/conversation/cwd, fresh live identity,
and a one-tab, one-pane destination. The pane's public ID changes; use the returned
identity afterward. No close, restart, prompt, session resume or automatic rollback
is performed. Unknown or unverified move outcomes must not be retried blindly.

The helper is currently Codex-only because it relies on a host-provided current
conversation identity. Claude and other hosts retain the normal verified-context
workflow. A skill cannot grant OS access, credentials or server capabilities.

## Verification status

Synthetic tests cover stale IDs, wrong/missing endpoints, missing/duplicate exact
matches, conflicting current identity, mapping races, preview without mutation,
move continuity and changed terminal/session refusal. Live reconciliation and
automatic self-only helper moves require separate live acceptance. The distinct
[operator-selected path](operator-selection.md#live-acceptance) has one live move
verified; this does not promote automatic self-only fixture tests into live proof.

## CLI evidence and the dispatch boundary

The installed `herdr --skill` documents that closed pane/tab IDs are not reused,
and that cross-workspace moves return a new pane ID; inherited old IDs are aliases
only for the moved process. `herdr pane move --help` exposes no expected-session
or expected-terminal compare-and-move option. These are installed CLI documentation
checks, not a claim about undocumented server internals.

The reduced operator-provided metadata/receipt fixture in
`../tests/fixtures/self-space-cli-2026-11-12.json` preserves the actual `result.panes`
and `result.type: pane_list` envelope, `agent_session` fields, terminal handle and
move receipt structure. The cwd is sanitized. Terminal IDs are treated as opaque
bounded handles; no `term_` prefix is required. Direct `pane get` extraction follows
the existing inventory engine's `result.pane` schema; no new live capture was made.

Immediately before dispatch the helper directly rechecks the selected pane's
terminal, conversation, cwd and topology. A replacement observed there aborts
without issuing a move. Herdr's nonreuse promise prevents a closed ID selecting a
new pane, but a different agent could still occupy the same open pane between
that final read and move. The CLI provides no documented atomic identity guard;
post-move verification can detect that race, not undo it. Do not describe this as
atomic admission. Live acceptance must account for that residual concurrent-input
risk; the current review/live hold remains until the coordinator releases it.

If exact native conversation metadata is unavailable, stop this automatic path.
The separate [explicit operator-selected workflow](operator-selection.md) may be
used only with a fresh human selection and verified layout binding; it does not
repair this path or identify other affected callers.


## Bounded native live verification — November 14, 2026

One real native self move was verified for an exact host-provided Codex thread:
automatic identity before and after, same native client/terminal/cwd, a one-pane
new space, unchanged unrelated topology and all24kernel process identities.
The move requested focus=false, but independent snapshots observed focus change;
its cause remains unknown. A later read-only preview preserved focus/layout.
Do not claim full no-focus acceptance or repeat the completed move to clear this
limitation. This is one-client live proof, not multi-client/background/side-thread
acceptance or automatic rollout to existing stock clients.

The patched frontend and receiver are prerequisites. Running sessions must reread
this skill; stock frontends do not gain reporting through skill discovery alone.
Do not reconnect other sessions, restart shared services or deploy to another host
without their applicable authority and owner coordination. Ordinary inventory
admission retains its guard; native self identity does not grant general control.
