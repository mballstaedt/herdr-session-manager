# Context diagnostics and guided repair

Use this workflow when the engine reports no verified context or the user asks
why Herdr pane mapping is unavailable. Diagnose first; do not infer that a project,
agent or server stopped merely because an inspection failed.

## Bounded read-only diagnosis

Run from the existing process, without replacing its environment:

```sh
python3 -B <skill-dir>/scripts/inventory.py diagnose-context --json
```

The diagnostic may run only `herdr status --json` and
`herdr pane current --current`, with bounded timeouts and the inherited endpoint.
It never starts or selects a server, enumerates panes, reads terminal output,
opens transcripts, changes layout, creates caches, prunes history or writes files.
If `HERDR_ENV` is not `1`, it reports outside-Herdr context without CLI calls.

Output contains allowlisted identity/status fields and diagnostic findings, not
raw environment/configuration, process arguments, credentials or arbitrary error
text. Unknown errors remain unknown. A timeout is not proof that anything stopped.

If the user supplies an independently verified intended socket path, compare it:

```sh
python3 -B <skill-dir>/scripts/inventory.py diagnose-context --json --expected-socket /verified/path/to/herdr.sock
```

Use a path obtained from the intended live Herdr environment or an authoritative
user-provided record. Do not infer it from a screenshot, select another socket,
scan sockets, or treat an inherited environment value as independent confirmation.
The option compares only; it cannot retarget the client or grant access.

## Interpret evidence and recommend the next step

| Finding | What it establishes | Recommended next step |
| --- | --- | --- |
| Outside Herdr | The process lacks the required managed-pane environment | Run the diagnostic from an existing Herdr shell. Do not set identity variables to simulate membership. |
| Access denied | A local operation was denied; the reason may be sandbox, OS policy or permissions | Report the exact failing command category. If permitted, use the host approval mechanism for one scoped read-only diagnostic. Never escalate automatically or bypass an approval rejection. |
| Pane not found | The contacted server did not resolve this caller | Keep stale caller identity versus wrong endpoint unresolved until independent evidence distinguishes them. Obtain the fresh-shell evidence below. |
| Expected endpoint mismatch | The reported socket differs from the independently supplied expected socket | Ask the operator to verify the intended Herdr server/session. Do not switch endpoints or assume that a matching pane ID on another server is the same agent. |
| Unavailable, incompatible, malformed or unknown reply | Reliable caller verification was not established | Report the observed category without inventing a cause. Preserve sessions and inspect the documented client/server compatibility only as authorized. |
| Verified current pane | A valid live reply identifies the caller under the checked endpoint | Use server-returned current workspace information, then rerun the normal guarded mode. Do not assume unrelated sessions or recovery actions are authorized. |

The server can resolve a moved process through an inherited alias. An old-looking
workspace-qualified environment ID does not itself prove stale context. Prefer
the valid current-pane reply; do not overwrite environment IDs to make them agree.
A verified caller on a reachable endpoint is not proof it is the intended server
when an independently verified expectation conflicts.

## Fresh-shell evidence and guided recovery

Ask for one useful operator action at a time. First, in a fresh ordinary shell
inside the intended existing Herdr environment, run:

```sh
herdr pane current --current
herdr status --json
```

These are read-only metadata checks. Share only the pane/tab/workspace identity,
server version/compatibility and socket/session identity needed for comparison.
Do not paste credentials, configuration files, terminal histories or process args.

A fresh shell identifies that shell, not the existing supervisor. Never assign its
pane ID to the supervisor or use focused-pane/sidebar position as proof.
Use independent stable Codex/Claude conversation identity and an authorized
metadata inventory from a verified context before proposing a rebind/relaunch.

If recovery is needed, prepare the exact conversation, owning process, destination,
checkpoint, active-work/dependency state and rollback/stop plan. Verify the current
writer has explicitly released ownership before any separately approved resume.
Do not stop another writer, duplicate a conversation, delete locks, restart Herdr,
or use a different API as a shortcut around a failed context/approval gate.
The [existing recovery workflow](recovery.md) remains responsible for selected
restores and duplicate prevention; this diagnostic never performs them.

Success means the actual caller is server-verified and the original guarded
inventory can run. Keep a diagnosis, a proposed recovery and a verified completed
repair distinct. This workflow installs no monitor or scheduler.
