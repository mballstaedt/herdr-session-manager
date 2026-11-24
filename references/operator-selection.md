# Explicit operator-selected pane move

Use this workflow when the operator explicitly identifies a pane, including a
fresh screenshot showing this conversation in the right-hand leaf of a two-pane
tab, and automatic current-conversation metadata is unavailable. This is **manual
operator selection**, not automatic thread identification or an alias repair.
It grants no general inventory, recovery or monitoring admission.

## Establish the selected binding

Require an independently confirmed intended server endpoint, exactly as for
[self-only reconciliation](self-space.md). Keep the inherited endpoint unchanged.
Verify supported `pane list`, `tab list` and `pane layout --pane <ID>` metadata.
For the supported screenshot case, require a unique named source tab, exactly two
side-by-side equal-height leaves, and the operator-selected right-hand position.
Resolve pane IDs through layout leaf IDs, then terminal IDs through pane metadata.
Do not select a conversation from its title, cwd, focus or an old inherited ID.
If the operator's selection or fresh topology is ambiguous, ask one clarification
and stop. Never fabricate operator confirmation or inject a replacement thread ID.

Show the selected pane, terminal and preserved left-hand neighbor. A fresh explicit
operator selection plus matching metadata authorizes only this selected-pane path;
it does not make the ordinary caller guard or automatic self check pass. Preserve
any session-specific move holds and exclusive desktop-custody requirements.

## Preview and apply

```sh
python3 -B <skill-dir>/scripts/inventory.py move-selected-space \
  --pane <SELECTED_PANE> --terminal <SELECTED_TERMINAL> \
  --right-of <LEFT_NEIGHBOR_PANE> --source-tab-label "<SOURCE TAB>" \
  --label "<NEW SPACE>" --expected-socket <CONFIRMED_SOCKET> \
  --operator-selected --json
```

This previews without mutation. Add `--apply` only for an explicitly requested,
reviewed move when applicable custody/approval holds are clear. The flags are
explicit operator-selected inputs, not overrides for `move-self-space`.
The helper rechecks all workspace membership, source layout, selected terminal
and PID-only process continuity immediately before its sole move dispatch.
Checks use explicit raises and remain active under Python optimization.

The helper enumerates each server-returned workspace with scoped pane/tab lists
and checks counts, so coverage does not depend on unscoped-list defaults. Apply
verifies a new space and tab with the requested ALL CAPS labels, exactly one pane,
the source tab's label and remaining neighbor count, and unchanged topology for
all other panes/tabs/workspaces (apart from the source workspace's expected count).
It measures shell and foreground PID continuity for the **selected pane only**.
It does not claim process continuity for unrelated panes.

Missing native `agent_session` metadata is reported as **unavailable**, never
`None == None` proof of a preserved conversation. A host thread environment value
is not native server proof and is not used to admit this operator-selected path.
`--no-focus` is requested; native UI focus preservation is not independently
verified. No keys, terminal input, restarts, resume, environment rewrites,
metadata reports, new server routing or automatic rollback are performed.

## Limits and unknown outcomes

There is no documented atomic compare-and-move identity condition in Herdr 0.9.5.
Final reads narrow but do not eliminate replacement between verification and
mutation; retain the [documented dispatch limit](self-space.md#cli-evidence-and-the-dispatch-boundary).
A timeout or missing receipt yields an unknown outcome; inspect fresh metadata
before any separately considered retry. Never blindly issue another move.
If a post-move check fails, report it without claiming success or moving back.

The operator-selected helper is a fallback for an explicit human selection. It
does not repair detached app-server identity or other callers whose automatic
conversation metadata is absent. A supported host-to-Herdr identity integration
would be needed for fully automatic identification in those contexts.

## Live acceptance

See the owning [package acceptance record](../README.md#live-acceptance-2026-11-12):
one real operator-selected move passed all 12 measured checks on Herdr 0.9.5.
Automatic conversation identification was unavailable and is not claimed.
