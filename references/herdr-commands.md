# Herdr commands this skill relies on (verified against herdr 0.9.5)

The installed binary is the authority. Herdr's own skill — `herdr --skill` — documents the
full surface. Re-check with `herdr --version` and `herdr <group>` (bare group prints usage)
after any Herdr upgrade. This file lists only what the engine calls and why.

| Purpose | Command | Mutates? |
|---|---|---|
| Verify the caller is in Herdr | `test "$HERDR_ENV" = 1` + `herdr pane current --current` | no |
| Workspaces | `herdr workspace list` | no |
| Tabs | `herdr tab list --workspace <ws>`, `herdr tab get <tab>` | no |
| Panes | `herdr pane list --workspace <ws>`, `herdr pane get <pane>` | no |
| Agents (name ↔ pane, by `pane_id`) | `herdr agent list` | no |
| Process continuity | `herdr pane process-info --pane <pane>` | no |
| Recent output (Level 2 only) | `herdr pane read <pane> --source recent-unwrapped --lines N` | no |
| Move a pane to its own tab | `herdr pane move <pane> --new-tab --workspace <SAME ws> --label "<title>" --no-focus` | layout |
| Rename a tab | `herdr tab rename <tab> "<label>"` | layout |
| Files tab (files mode) | `herdr tab create --workspace <ws> --cwd <dir> --label Files --focus`, `herdr pane run <own new pane> "<yazi cmd>"`, `herdr tab focus <tab>` | layout + a command in a pane this skill created |

Facts the engine depends on (verified live 2026-11-04):

- `pane list` gives `pane_id`, `tab_id`, `workspace_id`, `terminal_id`, `cwd`,
  `foreground_cwd`, `agent`, `agent_status`, `agent_session.value`,
  `terminal_title_stripped`. Claude Code sets the terminal title to its session topic,
  which is why that title is the default tab name.
- `agent list` adds the agent `name`; join it by `pane_id` and never by sidebar order.
- A move **within a workspace** keeps the `pane_id`, `terminal_id`, shell PID and the
  running agent. A move **across workspaces** changes the `pane_id`. The engine never
  moves across workspaces during organize. The explicit selected-pane/self-only workflows
  may do so; always read `.result.move_result.pane.pane_id`.
- `agent_session.value` is the stable identity (`session_key` `agent:<id>`). `terminal_id`
  is the fallback when no agent session is present, and the secondary key when matching
  an older watch snapshot. Pane numbers are not stable. A respawn keeps the agent
  session id and changes `terminal_id`; watch reports that as `respawned`.
- `process-info` returns `argv` arrays, which can contain MCP arguments and secrets.
  The engine keeps only the PID and `argv0`.
- `agent_status` is one of `idle`, `working`, `blocked`, `done` or `unknown`. `unknown`
  is Herdr's state classification. It is not title quality and it is not completion.
- CLI errors are JSON on stderr with exit 1; syntax errors exit 2.

**Never probe a mutating command by running it without arguments.** `herdr tab create`,
`herdr workspace create` and similar commands run with defaults. They do not print usage,
and they act on the *UI-focused* workspace rather than the caller's. Learned 2026-11-04,
when a bare `herdr tab create` opened a stray tab in another workspace. To see a group's
usage, run the bare group (`herdr tab`), and always pass `--workspace` explicitly.

Outside the explicitly selected recovery flow, never used by this skill: `pane close`, `tab close`, `workspace create/close`,
`pane send-text`, `pane send-keys`, `agent prompt`, `agent send-keys`, `agent start`,
`agent attach`, `agent focus`, `terminal attach`, `server stop`. The one exception to
"no input" is the `files` mode. It runs `yazi` only in a tab and pane that the skill
itself created, after checking that the pane is a plain shell.

`open_hints` quotes commands this skill does **not** run, so a supervisor can show the
Operator how to open a session:

| Hint | Documented command | Runs? |
|---|---|---|
| Focus the tab | `herdr tab focus <tab_id>` | no |
| Resume a Claude session | `claude --resume <uuid>` and `vscode://anthropic.claude-code/open?session=<uuid>` | no |
| Open a Codex thread | `vscode://openai.chatgpt/local/<uuid>` | no |
| Attach | `herdr agent attach <pane_id>`, or `herdr terminal attach <terminal_id>` for a non-agent pane | no |

`herdr tab focus` is a real command (the Files flow uses it on the skill's own tab).
Listing it as a hint does not focus an agent's tab. Attach and resume can take over a
live session; they stay text.

Recovery additionally uses `agent start` only in a newly created named tab, with
explicit saved-session resume arguments. See [recovery.md](recovery.md).

## Explicit self-only reconciliation and move

`reconcile-context` / `move-self-space` use `herdr status --json`, current-pane
lookup, `pane list`, and direct `pane get` metadata on the inherited server. Apply
uses `pane move <verified-self> --new-workspace --label ... --tab-label ... --no-focus`
and checks the receipt plus destination `pane list --workspace` / `tab list --workspace`.
These modes bypass neither failed access nor endpoint/identity checks; see
[self-only admission and dispatch limits](self-space.md).

Installed CLI documentation checked 2026-11-12: `herdr --skill` states closed tab
and pane IDs are never reused, and cross-workspace moves change public pane IDs.
`herdr pane move --help` has no expected-terminal/session condition. The helper's
final direct metadata read narrows the occupant-replacement race but does not
provide an atomic identity guarantee. Operator receipt fixtures establish the
known JSON envelope; live helper acceptance remains separate.

`move-selected-space` is a separate explicitly operator-selected path. It uses
workspace-scoped pane/tab coverage, layout leaf coordinates and PID-only selected
process snapshots. It never treats that selection as automatic thread proof; see
[operator selection](operator-selection.md).
