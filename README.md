# herdr-session-manager

This is a provider-neutral agent skill, usable from Claude Code and Codex, for running
Herdr as the primary agent workspace. It can:

- organize crowded panes into tabs with ALL CAPS labels;
- move an explicitly selected live pane into its own new space through the native CLI;
- list live and recent saved sessions, and resume selected sessions after a restart;
- report status and remaining work;
- diagnose unavailable caller context and guide repair without changing sessions;
- review unclear sessions;
- browse project files with Yazi;
- open a pane's project in VS Code.

It contains one canonical implementation, `scripts/inventory.py`, which uses only the
Python standard library.

## Requirements

- [Herdr](https://github.com/herdrdev/herdr) ≥ 0.9.5 (the version these commands were verified against)
- Python ≥ 3.10 (standard library only)
- Claude Code and/or Codex
- Optional: [Yazi](https://yazi-rs.github.io/) for `files`, VS Code's `code` CLI for `open-project`
- Developed and tested on macOS; Linux should work but is less exercised

## Quick install

```bash
git clone https://github.com/mballstaedt/herdr-session-manager.git
cd herdr-session-manager
scripts/install.sh --dry-run   # preview
scripts/install.sh             # Claude copy + Codex symlink; idempotent
scripts/validate.sh            # front matter, unit tests, install check
```

Then, inside a Herdr pane, run `/herdr-session-manager` in Claude Code or
`$herdr-session-manager` in Codex.

## Invoke

The `--native-client` options described in [self-space.md](references/self-space.md)
are experimental and need patched Codex and Herdr builds that are not included here;
stock Herdr 0.9.5 does not support them. Everything else works with stock Herdr.

| Claude Code | Codex | What it does |
|---|---|---|
| `/herdr-session-manager organize` | `$herdr-session-manager organize` | One pane per tab, named from session titles. Verified and idempotent |
| `/herdr-session-manager list` (`--all`) | `$herdr-session-manager list` | Fastest view. Metadata only; `?` marks unclear titles |
| `/herdr-session-manager status` | … | Counts plus a compact table and what needs you |
| `/herdr-session-manager remaining` | … | What is left to do, ordered by what needs you |
| `/herdr-session-manager review --unknown` (`--unnamed`) | … | Reviews only sessions with unclear titles and suggests names |
| `/herdr-session-manager review p4 p6` / `review "Asana task audit"` | … | Reviews the sessions you pick |
| `/herdr-session-manager review --unknown --apply` | … | The same, then renames only the unclear titles |
| `/herdr-session-manager rename p4 "Better Title"` | … | Renames the tab title only |
| `/herdr-session-manager flag p4` | … | Includes a title in `review --unknown` |
| `/herdr-session-manager watch` | … | One read-only monitoring pass |
| `/herdr-session-manager ack p4` | … | Record that the human already answered p4's current ask |
| `/herdr-session-manager files` (`--persistent`) | … | Yazi file browser in a `Files` tab |
| `/herdr-session-manager open-project [p4]` | … | `code <pane cwd>` |
| `/herdr-session-manager open-gui p4` | … | Opens the project and reports the GUI session capabilities |

In Codex you can also just say "use herdr-session-manager to organize". With no mode,
the default is `status`.

You can call the engine directly as `scripts/hsm <mode>`, or
`python3 -B scripts/inventory.py <mode> --json` for automation.

## Reconcile a stale caller and move this pane

For an explicit “put this current pane in its own space” request, the
[self-only workflow](references/self-space.md) provides `reconcile-context` and
`move-self-space`. It requires Codex’s host-provided `CODEX_THREAD_ID` and an
independently confirmed `--expected-socket`, matches exactly one live conversation,
and verifies direct metadata. `move-self-space` previews by default; `--apply`
rechecks identity and moves that pane, then checks terminal/session continuity and
the single-pane destination. It does not rewrite environment IDs, repair aliases,
or relax other modes’ caller guard. No cache, transcript or process-argument reads.

Other modes retain their original caller guard.

## Operator-selected fallback

When native conversation metadata is absent, an explicit human selection can use
[move-selected-space](references/operator-selection.md). It binds a right-hand pane
and terminal to fresh two-leaf layout metadata, previews by default, and rechecks
geometry, neighbor and all workspace coverage before applying an authorized move.
It verifies requested labels, one-pane destination, selected process continuity and
unrelated topology afterward. Missing session metadata remains unavailable; this
path never claims automatic conversation identity or native focus verification.

## Move a pane to its own space

Say “use herdr-session-manager to move MY TAB to its own space.”
The [selected-pane workflow](SKILL.md#move-a-selected-pane-into-its-own-space)
resolves fresh pane/tab metadata, uses native `herdr pane move --new-workspace`
with `--no-focus`, and checks the returned terminal/session identity and single-pane
workspace. Named-pane moves use that instruction-guided native CLI workflow. Current-pane
moves can use the self-only helper described above.
Explicit task authorization does not grant sandbox or server access.

## Diagnose caller context

When inventory reports no verified context, run:

```sh
python3 -B scripts/inventory.py diagnose-context --json
```

This read-only exception works without an already verified pane. It uses only
`status --json` and `pane current --current` under the inherited context, never
inventory, terminal reads, cache writes, other servers or automatic repair.
Outside Herdr it reports that condition without contacting the CLI.
An optional `--expected-socket` compares an independently verified intended endpoint;
it never routes commands to that endpoint.

Access denial does not prove a sandbox cause. A missing pane alone cannot
distinguish stale identity from the wrong server. Guided steps preserve this
uncertainty, require evidence, and never restart Herdr or duplicate a session.
See [context diagnostics and guided repair](references/context-diagnostics.md).
Unresolved context exits 3; diagnosis does not relax the normal context guard.

## Restore by date, repository or title

```text
$herdr-session-manager restore 2026-11-07 --timezone Europe/London
$herdr-session-manager restore 2026-11-07 --until 2026-11-09 --timezone Europe/London
$herdr-session-manager restore --repo ~/code/my-project
$herdr-session-manager restore --title "release notes"
```

Date means **last recorded activity since midnight**, not an exact historical
layout or proof of prior Herdr membership. Main-conversation candidates exclude
Guardian reviews and unnamed entries; `recent` still exposes those entries.
Timezone defaults to UTC; provide your IANA timezone for local date boundaries.

After choosing exact IDs, rerun the same filters with `--select codex:<uuid> ...`
to preview destinations, then add `--apply` to start them. Optionally add
`--approve-for-me` for Codex automatic approval review in workspace-write.
Restores reuse a workspace whose label matches the repository basename, or create
one; `--workspace wN` selects a specific destination instead. Live panes and
unresolved same-project identities block starting another writer.

`titles --all` previews ALL CAPS labels across current workspaces; add `--apply`
to normalize them. Organize, rename, files and recovery also use ALL CAPS labels.
Original conversation titles and session IDs remain unchanged.


## Before a restart or Herdr shutdown

```bash
python3 -B scripts/inventory.py pre-restart      # --json, --keep N, --session-dir DIR, --no-live
```

Say "restarting computer", "shutting down herdr", "rebooting" or similar. The skill copies
Herdr's `session.json` (workspaces, tabs, panes, cwd, layout, agent session references) to
`<session dir>/pre-restart-backups/session-<YYYYMMDDTHHMMSS>.json` (0600, sha256-verified,
newest 10 kept), then warns about agents still mid-turn, a disabled
`[session] resume_agents_on_restore`, or a Claude integration older than v6.

Herdr already restores layout and resumes supported agent conversations on its own after a
server restart (Herdr docs: *Session state and restore*). On macOS there is no shutdown
hook, so the safety copy is the only point-in-time copy taken deliberately before you go.
The command never stops Herdr. To roll back after a bad restore: `herdr server stop`,
keep the current `session.json`, copy the chosen backup over it, run `herdr`.

## Grid rendering

`scripts/grid.py` renders a wrapped, box-drawing text table from a JSON spec on stdin
(`{"columns": [["Header", width], ...], "rows": [...]}`). The skill uses it for
multi-session reports so columns stay aligned. Standard library only; no other I/O.

## Recover by exact ID

`$herdr-session-manager recent` lists the newest 30 local Codex and Claude sessions,
including titles, directories, last recorded activity (UTC), and whether each is open
on the verified Herdr server. Use `recent --limit 100` for a longer list. Without a
verified Herdr pane it still lists history, with running state marked unknown.

Select exact identifiers from that list:

```text
$herdr-session-manager resume codex:<session-uuid> claude:<session-uuid>
$herdr-session-manager resume codex:<session-uuid> claude:<session-uuid> --apply
```

The first command previews; the second creates named tabs in the current (or explicit
`--workspace`) workspace and resumes existing conversations without a new prompt.
See [recovery contract and limits](references/recovery.md).

## Paths

| What | Where |
|---|---|
| Source | this checkout |
| Claude install | `~/.claude/skills/herdr-session-manager/` (a copy; re-run `install.sh` after updating) |
| Codex install | `~/.agents/skills/herdr-session-manager` → symlink to this checkout |
| Cache | `~/.cache/herdr-session-manager/<server>/`, mode 0700 (see below) |
| Boundary rules | `~/.config/herdr-session-manager/boundaries.json` (optional) |

## Install, update, uninstall

```bash
scripts/install.sh --dry-run     # preview (writes nothing)
scripts/install.sh               # install or update both; idempotent
scripts/install.sh --uninstall   # remove both installs; this checkout stays
```

Options: `--claude-only`, `--codex-only`. The installer refuses to overwrite an
existing Claude skill directory or Codex entry that it did not create.

## Test

```bash
scripts/validate.sh            # front matter + unit tests + Yazi guard + both installs
scripts/validate.sh --no-install   # skip the install check
scripts/validate.sh --live     # + read-only live check inside Herdr
python3 -B -m unittest discover -s tests -v
```

The fixtures in `tests/fixtures/` are sanitized, synthetic data. They have the exact
JSON shape of herdr 0.9.5 output. Run `tests/make_fixtures.py` to regenerate them.

## How it stays fast and cheap

- **Level 1** (`list`, `organize`, `rename`, `flag`, `files`, `open-*`) uses Herdr
  metadata only. It never reads terminal output or transcripts.
- **Level 2** (`review`, `status`, `remaining`, `watch`, `ack`) reads at most 25 lines per
  session, plus a content fingerprint. It reuses cached summaries while the fingerprint
  is unchanged. Only stale or never-summarized sessions get a bounded transcript peek:
  first prompt and last reply, at most 600 characters, redacted.
- Agent-written summaries are cached against the stable agent session id. Pane numbers
  are not used.

## Cache, boundaries and retention

- **What is cached:** only redacted summaries, content hashes, and metadata. Raw terminal
  output and credentials are never cached.
- **Boundary partitions:** summaries and explicit renames are stored in one file per
  trust boundary (for example `summaries-work.json`, `summaries-personal.json`). The
  boundary is assigned from the pane's working directory, longest prefix wins:
  - there are **no defaults**, so nothing is cached until you opt in;
  - define rules in `~/.config/herdr-session-manager/boundaries.json`, e.g.
    `[{"prefix": "~/work/", "boundary": "work"}, {"prefix": "~/code/", "boundary": "personal"}]`;
  - sessions with no assigned boundary are never persisted.
- **Retention:** entries older than `HSM_RETENTION_DAYS` (default 7) are deleted on every
  run.
- **Purge:** `scripts/hsm purge` (optionally `--boundary work` or `--everything`).

## Safety

- Layout modes use same-workspace pane moves and tab renames. Explicit selected
  restores may create repository workspaces and named tabs; Files may create/focus
  its own tab.
- Recovery can start selected saved agents in new tabs. Other modes never restart agents; no mode closes agents. The explicitly requested selected-pane workflow may move one live pane into a new workspace.
- Terminal output and transcripts are treated as untrusted data.
- File previews are disabled for `.env*`, keys, credentials and tokens.

`references/vscode-sessions.md` explains why `open-gui` can't attach to a live session.
In short, both VS Code extensions *resume* sessions; they don't attach to them.

## File browser (Yazi)

`files --persistent` creates one `Files` tab per workspace, or reuses it, and runs
`YAZI_CONFIG_HOME=<skill>/config/yazi yazi <root>`. The config (`config/yazi/yazi.toml`)
sets the following:

| Setting | Effect |
|---|---|
| `show_hidden = true` | `.env`, `.github`, `.claude` and `.codex` are listed |
| Mouse: click, scroll, touch, drag | Left-click selects, right-click opens, wheel scrolls. Pointer movement doesn't change the selection |
| Enter / open → VS Code | Opens the file with the absolute `code` path in a detached process, so Yazi stays usable |
| `noop` previewer, preloader and spotter, prepended | No preview for `.env*`, `*.pem/key/p12/pfx/keystore/jks`, `id_rsa*`/`id_ed25519*`/`id_ecdsa*`, `*credentials*`, `*secret*`, `*token*`, `.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, `*.tfvars*`, `*.tfstate*`, `service-account*.json`, `kubeconfig*`, `.aws/credentials`, `.kube/config`, `.docker/config.json`, or anything under a `*secret*`/`*credential*` directory |

Known limitation: matching uses the entry's own name and path. A symlink with a harmless
name that points at a secret, such as `config -> ~/.aws/credentials`, still previews.
Don't keep such links in repos you browse.

The guard deliberately over-blocks: `.env.example` and `id_rsa.pub` lose their preview
too, but you can still open them. Yazi allows at most 16 preloaders and its built-ins use
8, so keep the guard to the three packed rules per list.

**Optional shortcut (not installed by this skill).** Herdr 0.9.5 supports
`[[keys.command]]`. The snippet below passed `herdr config check` in a scratch config. To
use it, add it to `~/.config/herdr/config.toml` yourself:

```toml
[[keys.command]]
key = "prefix+f"
type = "popup"          # or "pane" for a temporary zoomed pane
command = "YAZI_CONFIG_HOME=\"$HOME/.claude/skills/herdr-session-manager/config/yazi\" exec yazi \"${HERDR_ACTIVE_PANE_CWD:-$PWD}\""
description = "browse files (Yazi)"
width = "90%"
height = "90%"
```

Popups don't receive `HERDR_PANE_ID`, so this binding runs Yazi directly instead of
`hsm files`. The skill's verified-context check needs a real pane.

## More than one space later

- **Status-type modes:** `list`, `status`, `remaining` and `watch` accept `--all` today.
- **Layout modes:** organize, rename and files are per-workspace by design. Pass
  `--workspace wN` explicitly to target another workspace you can see in
  `herdr workspace list`.
- **Other machines:** remote Herdr machines need `herdr --machine <label>` support. That
  belongs in `LiveHerdr` (prefix every call). It is not implemented, because inherited
  pane IDs do not apply remotely.

## Supervisor integration

A supervising agent should call the engine in JSON mode and avoid parsing prose:

```bash
python3 -B scripts/inventory.py status --json      # {inventory, evidence, card_gate_rule}
python3 -B scripts/inventory.py remaining --json   # grouped: needs_me, blocked_failed, active, idle_unfinished, completed
python3 -B scripts/inventory.py watch --json       # {events:[{type, pane, message, identity…}]}
python3 -B scripts/inventory.py ack p4 --json      # human_acked_at for the live fingerprint
```

Every session, evidence row, and watch event includes the full identity. Session ids
are not shortened:

- `agent_session_id`, `agent_name`, `cwd`
- `pane_id`, `tab_id`, `workspace_id`, `terminal_id`, `session_key`

`open_hints` is optional and always has `do_not_run: true`. It may contain:

- `herdr_focus` — `herdr tab focus <tab_id>`
- `resume_command` — `claude --resume <uuid>` for a Claude session
- `resume_uri` / `codex_thread_open` — `vscode://openai.chatgpt/local/<uuid>` for Codex
- `herdr_attach` — `herdr agent attach <pane_id>`, or `herdr terminal attach <terminal_id>` when the pane has no agent

The engine never runs those commands.

**Card gate.** Before any needs-Operator card, run a fresh `status --json` or
`watch --json`. Card only when `needs_me_actionable` is true and `card_gate` is
`fresh` for that payload's `fingerprint`. `stale_fingerprint` (the cached ask does
not match the pane, or the read failed) and `human_acked` (this fingerprint was
acked) are non-actionable until a new inspect shows new evidence.
`ack <pane>`, or `remember` with `"human_acked": true`, stores `human_acked_at`
against the current fingerprint and the stable `session_key`. Clearing `needs_me`
in a summary does not ack a live ask. A respawn that keeps `agent_session_id` does
not clear the ack and is not a new session.

**Watch identity.** Snapshots are keyed by `session_key` (`agent:<id>`, else
`term:<terminal_id>`). `terminal_id` is the secondary key, so an older snapshot
still matches and a pane respawn is `respawned` rather than `pane-gone` plus
`new-session`. A different `agent_session_id` in the same terminal is `new-session`
and is not reported as a gone pane.

- **Exit codes:** 0 = ok, 1 = Herdr/operation error, 2 = usage or ambiguity,
  3 = no verified Herdr context.
- **Scheduling:** `watch` must run inside a Herdr pane, because the verified-context
  check needs `HERDR_ENV=1` and a pane the server recognizes. A plain cron or launchd job
  does not qualify. If you approve scheduling later, use one of these:
  - a dedicated monitor pane running
    `while sleep 900; do scripts/hsm watch --json >> ~/.cache/herdr-session-manager/watch.log; done`;
  - an approved scheduler that calls `herdr pane run <monitor-pane> "scripts/hsm watch"`.

  Nothing in this skill creates schedules, daemons or launch agents.

## Security

The skill reads local Herdr metadata, bounded terminal excerpts and Claude/Codex
session metadata on your machine. It sends nothing over the network. Terminal output
and transcripts are treated as untrusted data, and cached summaries are redacted.
Please report security issues privately via GitHub's "Report a vulnerability" link
rather than opening a public issue.

## License

See [LICENSE](LICENSE).
