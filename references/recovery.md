# Saved-session recovery

Use `recent [--limit N] [--json]` after a restart or to find a missing conversation.
It reads local Codex `state_*.sqlite` thread metadata read-only (nonarchived threads
with existing rollout files), and Claude top-level transcript metadata (bounded head
and tail, no message/tool contents). CODEX_HOME and CLAUDE_CONFIG_DIR are respected.
Claude titles fall back from customTitle to summary, slug, then session ID.
Codex uses updated_at; Claude uses transcript file modification time. These are last
recorded activity, not proof of the last human interaction. Missing or unsupported
metadata cannot be recovered by guessing. Other agent providers are not supported.

With verified Herdr context, matching stable provider/session IDs are checked across
every workspace on that server. A missing match means "not open on this Herdr server",
not globally stopped. Without context, state is unknown and resume is refused.
Listing does not persist recovered history or delete transcripts. Results can include
multiple projects; do not redistribute private session names or directories.

Show the list and let the Operator choose exact `codex:<uuid>` / `claude:<uuid>` IDs.
`resume <ids>` previews the selected titles, directories and destination workspace.
`resume <ids> --apply` starts only those IDs using `herdr tab create --workspace …
--cwd … --label … --no-focus`, then `herdr agent start <title> --kind <kind> --pane
<new-pane> -- resume <uuid>` for Codex, or `-- --resume <uuid>` for Claude.
Never add a prompt, bypass flags, or replay historic process arguments/credentials.
The destination is the caller's verified workspace unless explicitly supplied.

Restore refuses known live sessions, missing directories, duplicate selections and
missing saved IDs. It serializes manager restores and rechecks live panes, local
explicit resume processes and native writer markers. These checks cannot detect all
writers on other machines, other Herdr servers or interfaces; resolve known external
writers before selecting a session. Tab titles use the current live title when
available, otherwise the native saved title. Exact old workspace layout and custom
Herdr-only labels are not recoverable unless native history also saved them.

A start admission is not proof that the resumed agent reached its prompt. Report
admission separately; inspect the returned pane/session identity before claiming
success. On uncertain start or error, stop and preserve created tabs; do not retry or
close them automatically. Review partial results before another restore.

Compatibility: command syntax checked against local Herdr 0.9.5 and Codex 0.167.0-alpha.3.2 / Claude Code 2.1.309 help on 2026-11-09. Synthetic recovery tests cover metadata and resume
behavior. No real conversation was resumed during development. Codex's existing
symlink exposes canonical changes; Claude's managed copy requires installer update.

## Restore filters and workflow

`restore [YYYY-MM-DD] [--until YYYY-MM-DD] [--timezone IANA] [--repo PATH]
[--title TEXT]` lists candidates. At least a date, repository, or title is required.
The start date is inclusive local midnight; the optional end date includes the entire
day in that timezone (including daylight-saving transitions). Default timezone: UTC.
These filters operate on last recorded activity, not every historic activity event.
Archived Codex threads and absent rollout files remain excluded. Guardian reviews
and entries titled only with their IDs are omitted from this shortlist; use `recent`
and direct `resume` for those. A candidate is not necessarily a historic Herdr pane.

`--select kind:uuid ...` restricts the plan to exact IDs within those filters.
Without `--apply` it is a preview. Applying without explicit selection is refused.
Selection requires verified Herdr context, even for preview. `--approve-for-me`
adds the installed Codex automatic-review option; Claude selections refuse that flag.
No stored permission, provider credential or prompt is replayed.

Destinations default to repository-named workspaces, found case-insensitively by
label. Repository root is resolved with local `git rev-parse`; a non-Git directory
uses its directory name. Multiple matching workspace labels require explicit
`--workspace`. Existing live tabs are not regrouped by this command. A new workspace
uses its root pane, preventing an extra unused shell. All new/renamed tab labels use
ALL CAPS, including direct resume; native conversation titles are unchanged.

Identity checks prefer exact resume UUIDs from foreground provider argv over stale
Herdr metadata, without printing/storing raw arguments. Unidentified agent panes in
the same directory block a second writer. Matching provider/session identities are
reported with all matching panes. Process checks match an actual provider executable
and exact resume ID, avoiding the Python/shell-wrapper false positive encountered
in the earlier restore. Cross-machine writers still cannot be established locally.

New restore application is synthetic-tested; admission to a newly created workspace
has not been tested by resuming a real conversation. Stop and inspect any partial
workspace/tab/start receipt before retrying. Workspace grouping is reversible using
Herdr pane moves, but it never authorizes closing any session or empty workspace.
