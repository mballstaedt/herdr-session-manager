#!/usr/bin/env python3
"""Generate sanitized Herdr fixtures (shape captured from herdr 0.9.5 `pane list` / `agent list` /
`tab list` / `pane process-info` JSON; titles, paths and ids are synthetic).

Run:  python3 tests/make_fixtures.py   (rewrites tests/fixtures/*.json)
"""
import json
import os

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
EMP = "/work/employer"          # tests map this prefix to boundary "employer"
FIRM = "/work/firm"

CLAUDE_IDLE = """⏺ Opened the PR and pushed the branch. CI is green on all
  required checks.

✻ Cogitated for 1m 12s · done 8:40 AM
─────────────────────────────────────────
❯
─────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)"""
CLAUDE_ASKS = """⏺ The migration plan is ready. Two options: apply now
  during business hours, or wait for the 6pm window.
  Want me to apply it now or wait?
─────────────────────────────────────────
❯
─────────────────────────────────────────"""
CLAUDE_ERROR = """⏺ Running the export again…
  API Error: 529 overloaded_error · Retrying in 30s
  API Error: Request timed out
─────────────────────────────────────────
❯ """
CLAUDE_DONE = """⏺ All tasks are complete. Summary: 3 files updated,
  tests pass (42/42), PR #88 is ready for review.
─────────────────────────────────────────
❯ """
CLAUDE_SECRET = """⏺ Set the variable: export STRIPE_API_KEY=sk_live_XXXXdummyXXXX
  and the token ghp_DUMMYdummyDUMMYdummy00 is configured.
❯ """
CODEX_WORKING = """• Working (12s • esc to interrupt)
› Explain this codebase"""
LITTLE = "⠋ Thinking…"


def pane(ws, n, tab, title, kind: "str | None" = "claude", status="idle", cwd=f"{EMP}/project-a", sess=None, focused=False):
    p = {"pane_id": f"{ws}:p{n}", "tab_id": f"{ws}:t{tab}", "workspace_id": ws, "terminal_id": f"term_{ws}{n:04d}",
         "terminal_title": title, "terminal_title_stripped": title, "cwd": cwd, "foreground_cwd": cwd, "focused": focused,
         "revision": 3, "scroll": {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 26}}
    if kind:
        p["agent"] = kind
        p["agent_status"] = status
        p["agent_session"] = {"agent": kind, "kind": "id", "source": f"herdr:{kind}", "value": sess or f"0000{ws}-{n:04d}-4000-8000-00000000{n:04d}"}
    else:
        p["agent_status"] = "unknown"
    return p


def proc(p, leader="claude", shell_pid=None):
    base = int(p["terminal_id"][-4:]) * 10 + 1000
    sp = shell_pid or base
    fg = [{"pid": base + 5, "name": "node", "argv0": "mcp-server"}, {"pid": base + 1, "name": "2.1.316", "argv0": leader}]
    if leader in ("zsh", "bash"):
        return {"shell_pid": sp, "foreground_process_group_id": sp, "foreground_processes": [{"pid": sp, "name": leader, "argv0": "-" + leader}]}
    return {"shell_pid": sp, "foreground_process_group_id": base + 1, "foreground_processes": fg}


def agent_for(p, name):
    a = {k: p[k] for k in ("pane_id", "tab_id", "workspace_id", "terminal_id", "cwd", "foreground_cwd", "focused",
                           "terminal_title", "terminal_title_stripped", "agent_session", "agent_status")}
    a.update(agent=p["agent"], name=name, interactive_ready=True, revision=3, state_change_seq=10)
    return a


def tab(ws, n, label, count, focused=False, status="idle"):
    return {"tab_id": f"{ws}:t{n}", "workspace_id": ws, "label": label, "number": n, "pane_count": count, "focused": focused, "agent_status": status}


def ws(wid, label, panes, tabs, focused=False, n=1):
    return {"workspace_id": wid, "label": label, "number": n, "pane_count": panes, "tab_count": tabs, "focused": focused,
            "active_tab_id": f"{wid}:t1", "agent_status": "idle"}


def build(name, workspaces, tabs, panes, names, reads, current, extra=None):
    st = {"env": {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/fixture/herdr.sock"}, "current_pane": current,
          "workspaces": workspaces, "tabs": tabs, "panes": panes,
          "agents": [agent_for(p, names[p["pane_id"]]) for p in panes if p.get("agent") and names.get(p["pane_id"])],
          "process_info": {}, "reads": {}}
    for p in panes:
        leader = names.get("_leader_" + p["pane_id"]) or (p.get("agent") or "zsh")
        st["process_info"][p["terminal_id"]] = proc(p, leader)
        if p["pane_id"] in reads:
            st["reads"][p["terminal_id"]] = reads[p["pane_id"]]
    st.update(extra or {})
    with open(os.path.join(HERE, name + ".json"), "w") as fh:
        json.dump(st, fh, indent=1, ensure_ascii=False)


def main():
    os.makedirs(HERE, exist_ok=True)
    # 1. The Operator's real case, sanitized: 9 panes / 3 tabs, 7 crowded in t1, caller in t3.
    titles = ["Database migration review", "Herdr session manager skill", "Logging cleanup",
              "Service health check", "Feature PR review", "[Untracked] Backlog audit", "Dashboard PR QA"]
    P = [pane("w6", i + 1, 1, t, focused=(i == 2)) for i, t in enumerate(titles)]
    P.append(pane("w6", 9, 2, "Data access request", status="blocked"))
    P.append(pane("w6", 10, 3, "Workspace organizer", cwd=f"{FIRM}/shared"))
    names = {f"w6:p{i}": n for i, n in zip(range(1, 11), ["db", "hsm", "alerting", "sync", "relay", "audit", "partner-qa", None, "warehouse", "organizer"])}
    names["w6:p9"] = "warehouse"
    reads = {"w6:p1": CLAUDE_IDLE, "w6:p5": CLAUDE_IDLE, "w6:p9": CLAUDE_ASKS, "w6:p3": CLAUDE_IDLE, "w6:p2": CODEX_WORKING}
    build("w6_before", [ws("w6", "platform", 9, 3, True), ws("w7", "websites", 1, 1, n=2)],
          [tab("w6", 1, "1", 7, focused=True), tab("w6", 2, "data", 1, status="blocked"), tab("w6", 3, "Workspace organizer", 1),
           tab("w7", 1, "site", 1)],
          P + [pane("w7", 1, 1, "Website deploy", cwd=f"{EMP}/site")], names | {"w7:p1": "deploy"}, reads, "w6:p10")
    # w6_before has 10 panes total in w6? keep exactly 9: drop p8 slot (unused) — p1..p7, p9, p10

    # 2. Mixed Claude + Codex, questionable titles, multiple workspaces, caller INSIDE the crowded tab.
    Q = [pane("w1", 1, 1, "Claude Code", status="working"),                           # generic
         pane("w1", 2, 1, "", kind="codex", status="idle", cwd=f"{EMP}/billing"),       # missing title
         pane("w1", 3, 1, "billing", kind="codex", status="done", cwd=f"{EMP}/billing"),  # cwd basename
         pane("w1", 4, 1, "Fix webhook retries and also refactor the whole notification pipeline plus docs", status="blocked"),  # very long
         pane("w1", 5, 2, "API", status="unknown"),                                     # short but meaningful
         pane("w1", 6, 2, "Release notes", status="idle"),
         pane("w1", 7, 3, "Release notes", status="idle"),                              # duplicate → ambiguous
         pane("w1", 8, 3, "", kind=None, cwd=f"{FIRM}/scratch"),                       # plain shell
         pane("w1", 9, 1, "Organizer", cwd=f"{FIRM}/shared")]                          # caller in crowded t1
    qn = {"w1:p1": "lead", "w1:p2": "codex-a", "w1:p3": None, "w1:p4": "webhooks", "w1:p5": "api", "w1:p6": "notes", "w1:p7": "notes2",
          "w1:p9": "organizer", "_leader_w1:p8": "zsh", "_leader_w1:p2": "codex", "_leader_w1:p3": "codex", "w2:p1": "other"}
    qr = {"w1:p1": LITTLE, "w1:p2": CLAUDE_DONE, "w1:p3": CLAUDE_ERROR, "w1:p4": CLAUDE_ASKS, "w1:p5": CLAUDE_SECRET, "w1:p6": CLAUDE_IDLE,
          "w1:p7": CLAUDE_IDLE}
    build("mixed", [ws("w1", "billing", 9, 3, True), ws("w2", "personal", 1, 1, n=2)],
          [tab("w1", 1, "Agents", 5, focused=True), tab("w1", 2, "2", 2), tab("w1", 3, "misc", 2), tab("w2", 1, "Notes", 1)],
          Q + [pane("w2", 1, 1, "Personal notes", cwd=f"{FIRM}/notes")], qn, qr, "w1:p9")

    # 3. Single pane; 4. empty workspace; 5. no-context
    build("single", [ws("w3", "solo", 1, 1, True)], [tab("w3", 1, "Solo task", 1, focused=True)],
          [pane("w3", 1, 1, "Solo task", cwd=f"{FIRM}/solo")], {"w3:p1": "solo"}, {"w3:p1": CLAUDE_IDLE}, "w3:p1")
    build("empty", [ws("w4", "home", 1, 1, True), ws("w5", "empty", 0, 0, n=2)], [tab("w4", 1, "Home", 1, focused=True)],
          [pane("w4", 1, 1, "Home", cwd=f"{FIRM}/home")], {"w4:p1": "home"}, {}, "w4:p1")
    with open(os.path.join(HERE, "single.json")) as fh:
        st = json.load(fh)
    st["env"] = {}
    with open(os.path.join(HERE, "no_context.json"), "w") as fh:
        json.dump(st, fh, indent=1)


if __name__ == "__main__":
    main()
