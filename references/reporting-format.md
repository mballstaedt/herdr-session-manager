# Reporting formats

These outputs are for the Operator. Keep them short. Use tables over paragraphs, put
items that need the Operator first, and drop history and generic advice. A normal
workspace should come out at roughly 100–200 words.

## list / organize (Level 1: rendered by the engine; pass it through as-is)

The engine prints the table, the `?` notes and the footer. Don't add anything except,
at most, one line pointing to `review --unknown` when titles are marked `?`.
`organize` always ends with:

    Organized: X panes moved, Y tabs renamed, Z sessions preserved.

## review (Level 2: written by the agent from the engine's evidence packet)

One block per selected session:

    Session: <current title> (`p4`)
    Identity: session `<full agent_session_id>` · agent `<agent_name>` · cwd `<cwd>` · pane `<pane_id>` · tab `<tab_id>` · workspace `<workspace_id>`
    Suggested title: <short task title, or "keep">
    Goal: <one sentence>
    Done: <brief verified progress>
    Remaining: <next outstanding work, or "Unknown — insufficient evidence">
    Needs me: <specific action, or "None">

Copy `agent_session_id` in full. Never print only the first characters. Do not run
`open_hints`. A needs-me line is a card only when the evidence says
`needs_me_actionable` and `card_gate` is `fresh`. Say "already answered" when
`card_gate` is `human_acked`, and "not actionable — re-inspect" when it is
`stale_fingerprint`.

Tag anything that rests on inference with "(inferred)". Facts that came straight from
the terminal or the transcript need no tag. Finish with the exact rename commands for
any suggested titles, for example `/herdr-session-manager rename p4 "Better Title"`.
Do not run them unless `--apply` was given.

## remaining (Level 2)

    **Remaining work — <workspace>** (as of HH:MM)

    **Needs my approval or decision**
    - `p7` Warehouse access — approve the dataset grant (PR 42).
      session `<full agent_session_id>` · agent `<agent_name>` · cwd `<cwd>` · pane `<pane_id>` · tab `<tab_id>` · workspace `<workspace_id>`
    **Blocked or failed**
    - …
    **Active work**
    - …
    **Idle — unfinished or unknown**
    - `p5` Asana task audit — Unknown: no outstanding work visible in recent output.
    **Completed (agent says so)** — omit unless relevant

One sentence per session, and omit empty headings. "Idle" does not mean finished. A
completed agent turn does not mean the project is complete. Never invent tasks; write
"Unknown" instead.

## status (Level 2: rendered by the engine; the agent may tighten cells)

    **Workspace: platform** (`w6`, as of HH:MM)
    Sessions: 9 | Working: 1 | Blocked: 1 | Needs me: 1

    | Session | State | Progress / outstanding | Next |
    |---|---|---|---|

    **Needs me**
    - `p4` … — approval or question pending
      session `<full agent_session_id>` · agent `<agent_name>` · cwd `<cwd>` · pane `<pane_id>` · tab `<tab_id>` · workspace `<workspace_id>`

The Needs me list is only `needs_me_actionable` rows (`card_gate: fresh`). Human-acked
rows go under **Already answered** and are not re-carded until the pane fingerprint
changes. A cached needs-me whose fingerprint does not match the read goes under
**Not actionable** until a fresh `status` or `watch`. If nothing needs the Operator,
end with "No action required." When cached summaries are stale, the engine says so.
Don't present stale content as current. Don't shorten session ids.

## Footer (every mode)

`**Outstanding / Needs Attention**` lists only what current metadata or the cache
already shows reliably. Blocked agents and unviewed finished turns come from metadata;
outstanding work comes from cached summaries, each with its timestamp. When outstanding
work is unknown, the footer says so and points to `remaining`. `list` and `organize`
never read transcripts to fill the footer.

## JSON (for supervisors and automation)

Every mode accepts `--json`. Schema tag: `herdr-session-manager/v1`.
`status --json` returns `{inventory, evidence, card_gate_rule}`. `remaining` returns
grouped evidence by default. `review` always returns JSON, because its consumer is the
agent. `watch --json` events include the same identity fields as evidence.

Before a needs-Operator card, run a fresh `status --json` or `watch --json`. Treat
`needs_me` as non-actionable unless `needs_me_actionable` is true and `card_gate` is
`fresh` for that fingerprint. `human_acked` and `stale_fingerprint` wait for a new
inspect. `open_hints.do_not_run` is true; the hints are not commands for the agent to
execute.

`ack <pane>` stores `human_acked_at` and the fingerprint under the session key. The
next fresh inspect suppresses the card while the fingerprint is unchanged.
