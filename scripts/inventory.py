#!/usr/bin/env python3
"""herdr-session-manager engine — deterministic Herdr inventory, layout and evidence tool.

Stdlib only. Two evidence levels:

  LEVEL 1 (metadata only — no terminal output, no transcripts):
    list [--all]                 compact inventory, questionable titles marked "?"
    organize [--dry-run]         one pane per tab, tabs named from session titles, verified
    rename <pane> "Title"        rename ONLY the tab title of a pane's tab
    flag|unflag <pane>           manually mark a title for `review --unknown`
    reconcile-context           explicit Codex self-only identity check
    move-self-space [--apply]    preview/move this Codex pane into its own space
    move-selected-space         explicit operator-selected right-hand pane (preview default)

  LEVEL 2 (bounded session evidence, only for the sessions that need it):
    review <pane|name>... | --unknown (alias --unnamed)
                                 evidence packet for the selected sessions only
    remaining                    outstanding-work packet (cached summaries first)
    status                       concise progress/attention report
    watch                        one monitoring pass vs. the previous snapshot
    remember <pane> --summary J  cache an agent-written summary (keyed by agent session)
    ack <pane>                   record that the human already answered the current needs-me ask

  Housekeeping: context, purge.

Safety: the only mutations are `herdr pane move <id> --new-tab --workspace <SAME> --no-focus`
and `herdr tab rename` in ordinary layout modes. Explicit self-only `move-self-space
--apply` may create a workspace by moving the verified current Codex pane. It sends no
input and never closes or restarts the agent. Selected recovery has its separate contract.
`ack` writes a local cache record only.
`open_hints` are text and are never executed.

Testing: ordinary modes accept --fixture FILE; self-only reconciliation/move and
diagnose-context refuse that flag before any live call and use injected-client unit tests.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any

SCHEMA = "herdr-session-manager/v1"
EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_NO_CONTEXT = 0, 1, 2, 3
TAIL_LINES = 25          # remaining/status/watch: fingerprint + a short excerpt
REVIEW_LINES = 80        # review: selected sessions only
LONG_TITLE = 60
NO_REVIEWS = "All session titles are meaningful. No reviews needed."

GENERIC_TITLES = {
    "", "claude", "claude code", "codex", "codex cli", "terminal", "new session", "untitled", "untitled session",
    "shell", "zsh", "bash", "fish", "sh", "agent", "session", "new tab", "tab", "herdr", "default", "main",
}
GENERIC_PATTERN = re.compile(r"^\s*(\d+|tab\s*\d+|claude\s*\d+|codex\s*\d+|agent\s*\d+|shell\s*\d+|session\s*\d+|pane\s*\d+)\s*$", re.I)


class HerdrError(RuntimeError):
    def __init__(self, msg: str, payload: Any = None):
        super().__init__(msg)
        self.payload = payload


class NoContext(RuntimeError):
    pass


# =========================================================================== redaction

_REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED:private-key]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.S), "[REDACTED:private-key]"),          # cut off by the tail window
    (re.compile(r"\A.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED:private-key]"),         # started above the window
    (re.compile(r"(?:^[ \t]*[A-Za-z0-9+/=]{56,}[ \t]*\n?){3,}", re.M), "[REDACTED:base64-block]\n"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github-token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED:github-token]"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}\b"), "[REDACTED:api-key]"),
    (re.compile(r"\b[rsp]k_(?:live|test)_[A-Za-z0-9]{10,}\b"), "[REDACTED:api-key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED:slack-token]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "[REDACTED:google-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{16,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)[\"']?\b([A-Z0-9_\-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIAL|AUTH)[A-Z0-9_\-]*)[\"']?\s*[=:]\s*(\"[^\"]*\"|'[^']*'|[^\s,}]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
]


def redact(text: str | None) -> str:
    if not text:
        return text or ""
    for pat, repl in _REDACTIONS:
        text = pat.sub(repl, text)
    return text


# =========================================================================== clients

class LiveHerdr:
    """Thin wrapper over the installed `herdr` CLI (JSON responses)."""

    def __init__(self, binary: str | None = None, timeout: float = 20.0):
        self.binary = binary or os.environ.get("HERDR_BIN_PATH") or "herdr"
        self.timeout = timeout
        self.env = os.environ

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError as e:
            raise NoContext(f"herdr binary not found ({self.binary})") from e
        except subprocess.TimeoutExpired as e:
            raise HerdrError(f"herdr {' '.join(args[:3])} timed out") from e

    def call(self, *args: str) -> dict:
        cp = self._run(list(args))
        if cp.returncode != 0:
            payload = None
            try:
                payload = json.loads(cp.stderr)
            except Exception:
                pass
            raise HerdrError(f"herdr {' '.join(args[:3])} failed (exit {cp.returncode}): {cp.stderr.strip()[:300]}", payload)
        if not cp.stdout.strip():
            return {"result": {}}                    # e.g. `pane run` acknowledges admission with no body
        try:
            return json.loads(cp.stdout)
        except json.JSONDecodeError:
            return {"result": {"raw": cp.stdout.strip()[:500]}}

    def read(self, pane_id: str, lines: int) -> str:
        cp = self._run(["pane", "read", pane_id, "--source", "recent-unwrapped", "--lines", str(lines)])
        if cp.returncode != 0:
            raise HerdrError(f"pane read {pane_id} failed: {cp.stderr.strip()[:200]}")
        return cp.stdout

    def native_identity(self, endpoint: str, thread: str) -> dict:
        from native_identity import request
        return request(endpoint, thread)

    def native_move(self, endpoint, thread, client, terminal, label):
        from native_identity import move
        return move(endpoint, thread, client, terminal, label)


class FixtureHerdr:
    """In-memory Herdr double driven by a JSON fixture. Simulates moves and renames."""

    def __init__(self, path: str):
        with open(path) as fh:
            self.state = json.load(fh)
        self.env = dict(self.state.get("env", {"HERDR_ENV": "1"}))
        self.calls: list[list[str]] = []
        self._next_tab = self.state.get("next_tab_seq", 100)

    def _pane(self, pid: str) -> dict:
        for p in self.state["panes"]:
            if p["pane_id"] == pid or pid in p.get("_aliases", []):
                return p
        raise HerdrError(f"pane_not_found: {pid}", {"error": {"code": "pane_not_found"}})

    def _tab(self, tid: str) -> dict:
        for t in self.state["tabs"]:
            if t["tab_id"] == tid:
                return t
        raise HerdrError(f"tab_not_found: {tid}", {"error": {"code": "tab_not_found"}})

    def _recount(self):
        for t in self.state["tabs"]:
            t["pane_count"] = sum(1 for p in self.state["panes"] if p["tab_id"] == t["tab_id"])
        self.state["tabs"] = [t for t in self.state["tabs"] if t["pane_count"] > 0]   # herdr closes emptied tabs
        for w in self.state["workspaces"]:
            w["pane_count"] = sum(1 for p in self.state["panes"] if p["workspace_id"] == w["workspace_id"])
            w["tab_count"] = sum(1 for t in self.state["tabs"] if t["workspace_id"] == w["workspace_id"])

    def _public(self, p: dict) -> dict:
        return {k: copy.deepcopy(v) for k, v in p.items() if not k.startswith("_")}

    def call(self, *args: str) -> dict:
        a = list(args)
        self.calls.append(a)
        if not self.state.get("server_up", True):
            raise HerdrError("herdr server not reachable (fixture)")

        def opt(name):
            return a[a.index(name) + 1] if name in a else None

        if a[:2] == ["workspace", "list"]:
            return {"result": {"type": "workspace_list", "workspaces": copy.deepcopy(self.state["workspaces"])}}
        if a[:2] == ["tab", "list"]:
            ws = opt("--workspace")
            return {"result": {"type": "tab_list", "tabs": [copy.deepcopy(t) for t in self.state["tabs"] if not ws or t["workspace_id"] == ws]}}
        if a[:2] == ["tab", "get"]:
            return {"result": {"type": "tab_info", "tab": copy.deepcopy(self._tab(a[2]))}}
        if a[:2] == ["pane", "list"]:
            ws = opt("--workspace")
            return {"result": {"type": "pane_list", "panes": [self._public(p) for p in self.state["panes"] if not ws or p["workspace_id"] == ws]}}
        if a[:2] == ["agent", "list"]:
            return {"result": {"type": "agent_list", "agents": copy.deepcopy(self.state.get("agents", []))}}
        if a[:2] == ["pane", "current"]:
            cur = self.state.get("current_pane")
            if not cur:
                raise HerdrError("no current pane")
            return {"result": {"type": "pane_current", "pane": self._public(self._pane(cur))}}
        if a[:2] == ["pane", "get"]:
            return {"result": {"type": "pane_info", "pane": self._public(self._pane(a[2]))}}
        if a[:2] == ["pane", "process-info"]:
            p = self._pane(opt("--pane") or self.state.get("current_pane") or "")
            info = copy.deepcopy(self.state.get("process_info", {}).get(p["terminal_id"].replace("-respawned", "")))
            if info is None:
                info = {"shell_pid": None, "foreground_process_group_id": None, "foreground_processes": []}
            if p["terminal_id"].endswith("-respawned") and info.get("shell_pid"):
                info["shell_pid"] += 1
            info["pane_id"] = p["pane_id"]
            return {"result": {"type": "pane_process_info", "process_info": info}}
        if a[:2] == ["pane", "move"]:
            return self._move(a)
        if a[:2] == ["tab", "create"]:
            ws = opt("--workspace")
            self._next_tab += 1
            tab = {"tab_id": f"{ws}:t{self._next_tab}", "workspace_id": ws, "label": opt("--label") or str(self._next_tab),
                   "focused": "--focus" in a, "number": self._next_tab, "agent_status": "unknown", "pane_count": 1}
            if "--focus" in a:
                for t in self.state["tabs"]:
                    t["focused"] = False
            self.state["tabs"].append(tab)
            n = 500 + self._next_tab
            pane = {"pane_id": f"{ws}:p{n}", "tab_id": tab["tab_id"], "workspace_id": ws, "terminal_id": f"term_new{n}",
                    "cwd": opt("--cwd"), "foreground_cwd": opt("--cwd"), "terminal_title_stripped": "", "focused": True, "agent_status": "unknown"}
            self.state["panes"].append(pane)
            self.state.setdefault("process_info", {})[pane["terminal_id"]] = {"shell_pid": n, "foreground_process_group_id": n,
                                                                             "foreground_processes": [{"pid": n, "name": "zsh", "argv0": "-zsh"}]}
            self._recount()
            return {"result": {"type": "tab_created", "tab": copy.deepcopy(tab), "root_pane": self._public(pane)}}
        if a[:2] == ["pane", "run"]:
            p = self._pane(a[2])
            self.state.setdefault("ran", []).append([p["pane_id"], a[3]])
            if "yazi" in a[3]:
                info = self.state["process_info"][p["terminal_id"]]
                info["foreground_processes"].append({"pid": info["shell_pid"] + 1, "name": "yazi", "argv0": "yazi"})
                info["foreground_process_group_id"] = info["shell_pid"] + 1
            return {"result": {"type": "ok"}}
        if a[:2] == ["tab", "focus"]:
            for t in self.state["tabs"]:
                t["focused"] = t["tab_id"] == a[2]
            return {"result": {"type": "ok"}}
        if a[:2] == ["tab", "rename"]:
            if a[2] in self.state.get("fail_renames", []):
                raise HerdrError(f"tab rename {a[2]} failed (fixture)")
            t = self._tab(a[2])
            t["label"] = a[3]
            return {"result": {"type": "tab_info", "tab": copy.deepcopy(t)}}
        raise HerdrError(f"fixture does not implement: herdr {' '.join(a)}")

    def _move(self, a: list[str]) -> dict:
        pid = a[2]
        p = self._pane(pid)
        if pid in self.state.get("fail_moves", []):
            raise HerdrError(f"herdr pane move failed (exit 1): {{\"error\":{{\"code\":\"move_failed\",\"pane\":\"{pid}\"}}}}")
        if "--new-tab" not in a:
            raise HerdrError("fixture only supports --new-tab moves")
        ws = a[a.index("--workspace") + 1] if "--workspace" in a else p["workspace_id"]
        label = a[a.index("--label") + 1] if "--label" in a else None
        self._next_tab += 1
        new_tab = {"tab_id": f"{ws}:t{self._next_tab}", "workspace_id": ws, "label": label or str(self._next_tab),
                   "focused": "--focus" in a, "number": self._next_tab, "agent_status": p.get("agent_status", "unknown"), "pane_count": 1}
        if "--focus" in a:
            for t in self.state["tabs"]:
                t["focused"] = False
        self.state["tabs"].append(new_tab)
        prev = {"pane_id": p["pane_id"], "tab_id": p["tab_id"], "workspace_id": p["workspace_id"]}
        p["tab_id"] = new_tab["tab_id"]
        if ws != prev["workspace_id"] or pid in self.state.get("renumber_on_move", []):
            p.setdefault("_aliases", []).append(p["pane_id"])
            p["pane_id"] = f"{ws}:p{900 + self._next_tab}"
            p["workspace_id"] = ws
        if pid in self.state.get("break_continuity", []):
            p["terminal_id"] = p["terminal_id"] + "-respawned"
        for ag in self.state.get("agents", []):
            if ag["pane_id"] == prev["pane_id"]:
                ag["pane_id"], ag["tab_id"], ag["workspace_id"] = p["pane_id"], p["tab_id"], p["workspace_id"]
        self._recount()
        return {"result": {"type": "pane_move", "move_result": {
            "type": "pane_moved", "pane": self._public(p), "created_tab": copy.deepcopy(new_tab),
            "previous_pane_id": prev["pane_id"], "previous_tab_id": prev["tab_id"], "previous_workspace_id": prev["workspace_id"]}}}

    def read(self, pane_id: str, lines: int) -> str:
        self.calls.append(["pane", "read", pane_id, "--lines", str(lines)])
        p = self._pane(pane_id)
        text = self.state.get("reads", {}).get(p["terminal_id"], "")
        return "\n".join(text.splitlines()[-lines:])


# =========================================================================== context

def verify_context(h, workspace: str | None = None, all_workspaces: bool = False) -> dict:
    """HERDR_ENV=1 AND a live server answer for the calling pane, or NoContext.

    The default target is the caller's workspace as the SERVER reports it now (the
    launch-time HERDR_WORKSPACE_ID goes stale after a cross-workspace move).
    """
    if h.env.get("HERDR_ENV") != "1":
        raise NoContext("not running inside a Herdr-managed pane (HERDR_ENV != 1); refusing to guess which Herdr server or workspace to control")
    try:
        cur = h.call("pane", "current", "--current")["result"]["pane"]
        wl = h.call("workspace", "list")["result"]["workspaces"]
    except HerdrError as e:
        raise NoContext(f"the Herdr server did not confirm the calling pane: {e}") from e
    ws_ids = {w["workspace_id"]: w for w in wl}
    target = workspace or cur["workspace_id"]
    if target not in ws_ids:
        raise NoContext(f"workspace {target!r} not found on this Herdr server (have: {', '.join(ws_ids)})")
    return {"self_pane": cur["pane_id"], "self_terminal": cur.get("terminal_id"), "self_workspace": cur["workspace_id"],
            "workspace_id": None if all_workspaces else target,
            "workspace_label": None if all_workspaces else ws_ids[target].get("label"), "workspaces": wl}


# =========================================================================== boundaries

# No defaults: until boundaries.json exists, every session is "unassigned" and never persisted.
DEFAULT_BOUNDARY_RULES: list[dict] = []


def boundary_rules() -> list[dict]:
    """Operator-owned: ~/.config/herdr-session-manager/boundaries.json overrides the defaults."""
    path = os.environ.get("HSM_BOUNDARIES_FILE") or os.path.join(os.path.expanduser("~"), ".config", "herdr-session-manager", "boundaries.json")
    try:
        with open(path) as fh:
            rules = json.load(fh)
        if isinstance(rules, list):
            return rules
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_BOUNDARY_RULES


def boundary_for(cwd: str | None, rules: list[dict] | None = None) -> str:
    """Assign a Trust Boundary by SOURCE (pane cwd); longest prefix wins; else 'unassigned'."""
    if not cwd:
        return "unassigned"
    path = os.path.realpath(os.path.expanduser(cwd)).rstrip("/") + "/"
    best, best_len = "unassigned", -1
    for r in rules if rules is not None else boundary_rules():
        pre = os.path.realpath(os.path.expanduser(r.get("prefix", ""))).rstrip("/") + "/"
        if path.startswith(pre) and len(pre) > best_len:
            best, best_len = r.get("boundary", "unassigned"), len(pre)
    return best


# =========================================================================== title quality (lightweight rules)

def title_issue(name: str | None, pane: dict, agent_name: str | None = None) -> str | None:
    """Return why a session name fails to identify a task, or None if it is meaningful.

    Rules only — no LLM, no transcript. Short is NOT bad by itself. agent_status is irrelevant.
    """
    if name is None or not name.strip():
        return "missing title"
    t = name.strip()
    low = t.lower()
    if low in GENERIC_TITLES or GENERIC_PATTERN.match(t):
        return "generic title"
    cwd_base = os.path.basename((pane.get("cwd") or "").rstrip("/")).lower()
    if cwd_base and low == cwd_base:
        return "repository/directory name, not a task"
    if (pane.get("agent") or "").lower() == low:
        return "agent kind, not a task"
    if t.startswith(("/", "~")) or re.match(r"^[\w.\-]+@[\w.\-]+:", t):
        return "path or host prompt, not a task"
    if len(t) < 3:
        return "too short to identify a task"
    if len(t) > LONG_TITLE:
        return f"very long title (>{LONG_TITLE} chars)"
    return None


# =========================================================================== level 1 inventory

def session_key(p: dict) -> str:
    sess = (p.get("agent_session") or {}).get("value") or p.get("agent_session_id")
    return f"agent:{sess}" if sess else f"term:{p.get('terminal_id')}"


def short_id(pane_or_tab_id: str) -> str:
    return pane_or_tab_id.split(":", 1)[-1]


# Hint tokens are interpolated into command strings a person might copy.
# Reject anything that is not a Herdr id or session id so a hostile pane
# cannot smuggle shell metacharacters into the hint.
_HINT_TOKEN = re.compile(r"^[A-Za-z0-9:._-]{1,200}$")
_HINT_SESSION = re.compile(r"^[A-Za-z0-9-]{8,128}$")

CARD_GATE_RULE = (
    "Before any needs-Operator card, run a fresh status --json or watch --json. "
    "needs_me is actionable only when needs_me_actionable is true and card_gate is \"fresh\" "
    "for that payload's fingerprint. A stale fingerprint, a human ack (card_gate human_acked), "
    "or any older payload is non-actionable until you re-inspect."
)


def _hint_token(value: str | None, pattern: re.Pattern = _HINT_TOKEN) -> str | None:
    if value and pattern.match(value):
        return value
    return None


def session_identity(s: dict) -> dict:
    """Full supervisor identity. Session ids are never shortened."""
    pane_id = s.get("pane_id")
    tab_id = s.get("tab_id")
    return {
        "agent_session_id": s.get("agent_session_id"),
        "agent_name": s.get("agent_name"),
        "cwd": s.get("cwd"),
        "pane_id": pane_id,
        "pane": s.get("pane") or (short_id(pane_id) if pane_id else None),
        "tab_id": tab_id,
        "tab": s.get("tab") or (short_id(tab_id) if tab_id else None),
        "workspace_id": s.get("workspace_id"),
        "terminal_id": s.get("terminal_id"),
        "session_key": s.get("session_key"),
    }


def format_identity(s: dict) -> str:
    """One prose line. The agent session id is the full value."""
    ident = session_identity(s)
    sid = ident.get("agent_session_id") or "—"
    return (f"session `{sid}` · agent `{ident.get('agent_name') or '—'}` · "
            f"cwd `{ident.get('cwd') or '—'}` · pane `{ident.get('pane_id') or ident.get('pane') or '—'}` · "
            f"tab `{ident.get('tab_id') or '—'}` · workspace `{ident.get('workspace_id') or '—'}`")


def open_hints(s: dict) -> dict:
    """Commands and URIs the Operator can run. This function only returns strings.

    It never calls Herdr, never launches a process, and never resumes a session.
    Focus is `herdr tab focus` (the command this skill already verified). Resume
    hints are the documented Claude CLI, the Codex thread URI, and Herdr attach.
    """
    hints: dict[str, Any] = {"do_not_run": True}
    tab_id = _hint_token(s.get("tab_id"))
    pane_id = _hint_token(s.get("pane_id"))
    terminal_id = _hint_token(s.get("terminal_id"))
    sid = _hint_token(s.get("agent_session_id"), _HINT_SESSION)
    kind = s.get("agent_kind")
    if tab_id:
        hints["herdr_focus"] = f"herdr tab focus {tab_id}"
    if kind == "claude" and sid:
        hints["resume_command"] = f"claude --resume {sid}"
        hints["resume_uri"] = f"vscode://anthropic.claude-code/open?session={sid}"
    elif kind == "codex" and sid:
        hints["codex_thread_open"] = f"vscode://openai.chatgpt/local/{sid}"
        hints["resume_uri"] = hints["codex_thread_open"]
    if kind in ("claude", "codex", "other") and pane_id:
        hints["herdr_attach"] = f"herdr agent attach {pane_id}"
    elif terminal_id:
        hints["herdr_attach"] = f"herdr terminal attach {terminal_id}"
    return hints


def inventory_l1(h, ctx: dict, cache: "Cache | None" = None) -> dict:
    """Metadata only: workspace/tab/pane/agent lists. Never reads terminal output."""
    now = _now()
    agents = h.call("agent", "list")["result"]["agents"]
    agent_by_pane = {a["pane_id"]: a for a in agents}       # verified pane association, never sidebar order
    rules = boundary_rules()
    flags = cache.flags() if cache else {}
    renames = cache.renames() if cache else {}
    out_ws = []
    for w in ctx["workspaces"]:
        if ctx["workspace_id"] and w["workspace_id"] != ctx["workspace_id"]:
            continue
        wid = w["workspace_id"]
        tabs = h.call("tab", "list", "--workspace", wid)["result"]["tabs"]
        tab_by_id = {t["tab_id"]: t for t in tabs}
        panes = h.call("pane", "list", "--workspace", wid)["result"]["panes"]
        per_tab: dict[str, int] = {}
        for p in panes:
            per_tab[p["tab_id"]] = per_tab.get(p["tab_id"], 0) + 1
        sessions = []
        for order, p in enumerate(panes):
            ag = agent_by_pane.get(p["pane_id"])
            tab = tab_by_id.get(p["tab_id"], {})
            key = session_key(p)
            title = redact((p.get("terminal_title_stripped") or "").strip()) or None
            tab_label = redact(tab.get("label")) if tab.get("label") else tab.get("label")
            alone = per_tab.get(p["tab_id"], 0) == 1
            explicit = renames.get(key)
            name, name_src = title, "session title"
            outdated = None
            if alone and tab_label and explicit and tab_label == explicit:
                name, name_src = tab_label, "explicit rename"
            elif not title and ag and ag.get("name"):
                name, name_src = ag["name"], "agent name"
            elif not title and alone and tab_label and not title_issue(tab_label, p):
                name, name_src = tab_label, "tab label"
            issue = title_issue(name, p)
            if not issue and not title and name_src == "agent name":
                issue = "missing session title (showing agent name)"
            kind0 = (p.get("agent") or "").lower()
            if not kind0:
                issue = None                     # plain shell/process: no agent session to review
            if not issue and alone and tab_label and title and tab_label != title and not explicit \
                    and not title_issue(tab_label, p):
                outdated = "tab label differs from current session title (possibly outdated)"
            if key in flags and kind0:
                issue = issue or "manually flagged for review"
            kind = (p.get("agent") or "").lower()
            kind = kind if kind in ("claude", "codex") else ("other" if kind else "none")
            bnd = boundary_for(p.get("foreground_cwd") or p.get("cwd"), rules)
            row = {
                "workspace_id": wid, "tab_id": p["tab_id"], "tab_label": tab_label, "tab_pane_count": per_tab.get(p["tab_id"], 0),
                "pane_id": p["pane_id"], "pane": short_id(p["pane_id"]), "order": order, "terminal_id": p.get("terminal_id"),
                "session_key": key, "agent_session_id": (p.get("agent_session") or {}).get("value"),
                "agent_kind": kind, "agent_name": (ag or {}).get("name"), "herdr_status": p.get("agent_status") if p.get("agent") else None,
                "cwd": p.get("foreground_cwd") or p.get("cwd"), "boundary": bnd,
                "title": title, "name": name, "name_source": name_src, "title_issue": issue, "title_note": outdated,
                "questionable": bool(issue), "focused": bool(p.get("focused")), "is_self": p["pane_id"] == ctx["self_pane"],
                "cached_summary": cache.get_summary(key, bnd) if cache else None,
                "human_ack": cache.get_ack(key) if cache else None,
            }
            row["open_hints"] = open_hints(row)
            sessions.append(row)
        dup = {}
        for s in sessions:
            if s["name"] and s["agent_kind"] != "none":
                dup.setdefault(s["name"].lower(), []).append(s)
        for group in dup.values():
            if len(group) > 1:
                for s in group:
                    if not s["title_issue"]:
                        s["title_issue"], s["questionable"] = "ambiguous: same title as another session", True
        out_ws.append({"workspace_id": wid, "label": w.get("label"), "tabs": tabs, "sessions": sessions})
    return {"schema": SCHEMA, "kind": "inventory", "level": 1, "generated_at": now, "self_pane": ctx["self_pane"],
            "scope": "all-workspaces" if not ctx["workspace_id"] else "workspace", "workspaces": out_ws}


def state_word(s: dict) -> str:
    st = s.get("herdr_status")
    if not st:
        return "shell/process"
    return {"done": "done (unviewed)"}.get(st, st)


def attention_footer(ws: dict) -> list[str]:
    """Outstanding / Needs Attention from metadata + cached summaries ONLY (never triggers reads)."""
    lines = []
    for s in ws["sessions"]:
        if s["is_self"]:
            continue
        if s["herdr_status"] == "blocked":
            lines.append(f"- `{s['pane']}` {_cell(s['name'] or '', 50)} — blocked: approval or question pending")
        elif s["herdr_status"] == "done":
            lines.append(f"- `{s['pane']}` {_cell(s['name'] or '', 50)} — finished a turn, not yet viewed")
    cached = [s for s in ws["sessions"] if s.get("cached_summary") and s["cached_summary"].get("outstanding") and not s["is_self"]]
    for s in cached:
        cs = s["cached_summary"]
        lines.append(f"- `{s['pane']}` outstanding (cached {_hm(cs.get('updated_at', ''))}): {_cell(cs['outstanding'], 90)}")
    known = {s["pane_id"] for s in cached}
    unknown = [s for s in ws["sessions"] if s["agent_kind"] != "none" and not s["is_self"] and s["pane_id"] not in known]
    if unknown:
        lines.append(f"- Outstanding work for {len(unknown)} session(s) is unknown from metadata — run `/herdr-session-manager remaining`.")
    return ["", "**Outstanding / Needs Attention**"] + (lines or ["- None known."])


def render_list(inv: dict) -> str:
    blocks = []
    for w in inv["workspaces"]:
        ss = w["sessions"]
        head = f"**Herdr — {w['label'] or w['workspace_id']}** (`{w['workspace_id']}`) · {len(ss)} panes · {len(w['tabs'])} tabs · as of {_hm(inv['generated_at'])}"
        if not ss:
            blocks.append(head + "\nEmpty workspace — no sessions.")
            continue
        rows = ["| Pane | Tab | Session | Agent | State |", "|---|---|---|---|---|"]
        for s in sorted(ss, key=lambda s: (s["tab_id"], s["order"])):
            mark = "? " if s["questionable"] else ""
            nm = _cell(s["name"] or "(untitled)", 60)
            agent = s["agent_kind"] if s["agent_kind"] != "none" else "—"
            if s["agent_name"]:
                agent += f" `{s['agent_name']}`"
            me = " (this session)" if s["is_self"] else ""
            tab = short_id(s["tab_id"]) + ("" if (s["tab_label"] or "") == (s["name"] or "") else f" {_cell(s['tab_label'] or '', 28)}")
            rows.append(f"| {s['pane']} | {tab} | {mark}{nm}{me} | {agent} | {state_word(s)} |")
        q = [s for s in ss if s["questionable"]]
        notes = [f"? {s['pane']}: {s['title_issue']}" for s in q] + [f"note {s['pane']}: {s['title_note']}" for s in ss if s.get("title_note")]
        tail = []
        if q:
            tail.append("Questionable titles — " + "; ".join(notes) + ". Suggest `/herdr-session-manager review --unknown`.")
        elif notes:
            tail.append("; ".join(notes))
        blocks.append("\n".join([head, ""] + rows + [""] + tail + attention_footer(w)))
    return "\n\n".join(blocks)


# =========================================================================== organize (level 1)

def _leader(proc: dict) -> dict | None:
    fg = proc.get("foreground_processes") or []
    pg = proc.get("foreground_process_group_id")
    for p in fg:
        if p.get("pid") == pg:
            return {"pid": p.get("pid"), "name": p.get("argv0") or p.get("name")}
    if fg:
        return {"pid": fg[-1].get("pid"), "name": fg[-1].get("argv0") or fg[-1].get("name")}
    return None


def tab_label_for(s: dict) -> str:
    """New-tab name = the pane's existing session title in ALL CAPS."""
    from titles import tab_title
    return tab_title(s["title"] or s["agent_name"] or f"pane {s['pane']}")


def plan_organize(inv: dict, sync_titles: bool = False) -> dict:
    """One pane per tab. In each crowded tab the caller's own pane stays (else the first pane
    in Herdr's list order); every other pane moves to a new tab in the SAME workspace named
    from its session title. Generic tab labels are replaced by the remaining pane's title;
    meaningful labels are kept unless --sync-titles (reproduces the original Bash script).
    """
    ops, kept = [], []
    w = inv["workspaces"][0]
    by_tab: dict[str, list[dict]] = {}
    for s in w["sessions"]:
        by_tab.setdefault(s["tab_id"], []).append(s)
    taken = {(t.get("label") or "").lower() for t in w["tabs"]}

    def unique(label: str, s: dict, own: str = "") -> str:
        if label.lower() in taken - {own.lower()}:
            label = f"{label} ({s['pane']})".upper()           # uppercase title + pane id only on collision
        taken.add(label.lower())
        return label

    for t in w["tabs"]:
        members = sorted(by_tab.get(t["tab_id"], []), key=lambda s: s["order"])
        if not members:
            continue
        anchor = next((s for s in members if s["is_self"]), members[0])
        for s in members:
            if s is anchor:
                continue
            ops.append({"op": "move", "pane_id": s["pane_id"], "terminal_id": s["terminal_id"], "workspace_id": w["workspace_id"],
                        "from_tab": t["tab_id"], "label": unique(tab_label_for(s), s), "questionable": s["questionable"],
                        "agent_session_id": s["agent_session_id"]})
        label = t.get("label") or ""
        from titles import tab_title
        want = tab_title(anchor["title"])
        if want and not title_issue(want, {"cwd": anchor["cwd"], "agent": anchor["agent_kind"]}) \
                and label not in (want, f"{want} ({anchor['pane']})") and anchor["name_source"] != "explicit rename":
            if title_issue(label, {"cwd": anchor["cwd"], "agent": anchor["agent_kind"]}) or sync_titles:
                ops.append({"op": "rename", "tab_id": t["tab_id"], "workspace_id": w["workspace_id"], "from_label": label, "label": unique(want, anchor, label),
                            "for_pane": anchor["pane_id"], "questionable": anchor["questionable"]})
                continue
        if label != tab_title(label):
            ops.append({"op": "rename", "tab_id": t["tab_id"], "workspace_id": w["workspace_id"],
                        "from_label": label, "label": tab_title(label), "for_pane": anchor["pane_id"],
                        "questionable": anchor["questionable"]})
            continue
        if len(members) == 1:
            kept.append({"tab_id": t["tab_id"], "label": label})
    return {"schema": SCHEMA, "kind": "organize-plan", "generated_at": inv["generated_at"], "workspace_id": w["workspace_id"],
            "ops": ops, "kept_tabs": kept, "noop": not ops}


def _proc_snapshot(h, pane_id: str) -> dict:
    p = h.call("pane", "get", pane_id)["result"]["pane"]
    try:
        proc = h.call("pane", "process-info", "--pane", p["pane_id"])["result"]["process_info"]
    except HerdrError:
        proc = {}
    lead = _leader(proc)
    return {"pane_id": p["pane_id"], "tab_id": p["tab_id"], "workspace_id": p["workspace_id"], "terminal_id": p.get("terminal_id"),
            "shell_pid": proc.get("shell_pid"), "leader_pid": (lead or {}).get("pid"), "leader": (lead or {}).get("name"),
            "cwd": p.get("cwd"), "agent_session_id": (p.get("agent_session") or {}).get("value")}


def _global_counts(h) -> dict:
    out = {}
    for w in h.call("workspace", "list")["result"]["workspaces"]:
        panes = h.call("pane", "list", "--workspace", w["workspace_id"])["result"]["panes"]
        out[w["workspace_id"]] = sorted(p.get("terminal_id") for p in panes)
    return out


def _focused_tab(h, ws: str) -> str | None:
    return next((t["tab_id"] for t in h.call("tab", "list", "--workspace", ws)["result"]["tabs"] if t.get("focused")), None)


def apply_organize(h, ctx, plan: dict, cache, sync_titles: bool = False) -> dict:
    ws = plan["workspace_id"]
    before_global = _global_counts(h)
    before = {}
    for p in h.call("pane", "list", "--workspace", ws)["result"]["panes"]:
        before[p["terminal_id"]] = _proc_snapshot(h, p["pane_id"])
    focus_before = _focused_tab(h, ws)
    baseline = cache.save_baseline({"plan": plan, "panes": before, "focus": focus_before}) if cache else None
    results, aborted = [], None
    for op in plan["ops"]:
        if aborted:
            results.append({**op, "result": "not-attempted", "reason": aborted})
            continue
        if op["op"] == "rename":
            try:
                h.call("tab", "rename", op["tab_id"], op["label"])
                got = h.call("tab", "get", op["tab_id"])["result"]["tab"].get("label")
                results.append({**op, "result": "renamed" if got == op["label"] else "unverified", "observed_label": got})
            except HerdrError as e:
                results.append({**op, "result": "failed", "reason": str(e)[:240]})
            continue
        if op["pane_id"] == ctx["self_pane"]:
            results.append({**op, "result": "refused", "reason": "organizer's own pane is never moved"})
            continue
        try:
            cur = _proc_snapshot(h, op["pane_id"])
        except HerdrError as e:
            results.append({**op, "result": "skipped", "reason": f"stale pane id — no longer exists: {str(e)[:120]}"})
            continue
        drift = [k for k, want in (("terminal_id", op["terminal_id"]), ("tab_id", op["from_tab"]), ("workspace_id", ws)) if cur[k] != want]
        if drift:
            results.append({**op, "result": "skipped", "reason": f"pane changed since the plan ({', '.join(drift)}); re-run organize"})
            continue
        try:
            mr = h.call("pane", "move", op["pane_id"], "--new-tab", "--workspace", ws, "--label", op["label"], "--no-focus")["result"]["move_result"]
        except (HerdrError, KeyError) as e:
            results.append({**op, "result": "failed", "reason": f"pane move failed: {str(e)[:240]}"})
            continue
        new_id = mr["pane"]["pane_id"]
        problems, warnings = [], []
        try:
            after = _proc_snapshot(h, new_id)
        except HerdrError as e:
            after, problems = None, [f"moved pane {new_id} not found: {e}"]
        if after:
            if after["workspace_id"] != ws:
                problems.append(f"workspace changed {ws}→{after['workspace_id']}")
            if after["tab_id"] == op["from_tab"]:
                problems.append("pane still in its original tab")
            for k in ("terminal_id", "shell_pid", "agent_session_id", "cwd"):
                if cur[k] != after[k]:
                    problems.append(f"{k} changed {cur[k]}→{after[k]}")
            if cur["leader_pid"] != after["leader_pid"]:
                warnings.append(f"foreground process changed {cur['leader']}({cur['leader_pid']})→{after['leader']}({after['leader_pid']})")
        res = {**op, "new_pane_id": new_id, "previous_pane_id": mr.get("previous_pane_id"),
               "new_tab_id": (mr.get("created_tab") or {}).get("tab_id") or (after or {}).get("tab_id"), "warnings": warnings}
        if problems:
            res.update(result="moved-unverified", problems=problems)
            aborted = f"continuity check failed on {op['pane_id']}; remaining operations not attempted"
        else:
            res["result"] = "moved"
        results.append(res)
    # ---- re-inventory and whole-layout checks
    after_global = _global_counts(h)
    after_panes = {p["terminal_id"]: p for p in h.call("pane", "list", "--workspace", ws)["result"]["panes"]}
    checks = {}
    checks["workspace_isolation"] = all(sorted(before_global.get(k, [])) == sorted(after_global.get(k, [])) for k in set(before_global) | set(after_global))
    checks["pane_count_unchanged"] = {k: len(v) for k, v in before_global.items()} == {k: len(v) for k, v in after_global.items()}
    broken = []
    for term, b in before.items():
        a = after_panes.get(term)
        if not a:
            broken.append(f"{b['pane_id']} missing")
            continue
        snap = _proc_snapshot(h, a["pane_id"])
        for k in ("shell_pid", "agent_session_id", "workspace_id"):
            if b[k] != snap[k]:
                broken.append(f"{b['pane_id']} {k} {b[k]}→{snap[k]}")
    checks["process_continuity"] = not broken
    checks["continuity_problems"] = broken
    checks["focus_unchanged"] = _focused_tab(h, ws) == focus_before
    tabs_after = h.call("tab", "list", "--workspace", ws)["result"]["tabs"]
    per_tab = {}
    for p in after_panes.values():
        per_tab[p["tab_id"]] = per_tab.get(p["tab_id"], 0) + 1
    checks["one_pane_per_tab"] = all(n == 1 for n in per_tab.values())
    preserved = len(before) - len([x for x in broken if "missing" in x or "shell_pid" in x])
    return {"schema": SCHEMA, "kind": "organize-result", "generated_at": _now(), "baseline": baseline, "results": results,
            "aborted": aborted, "checks": checks, "tabs_after": len(tabs_after),
            "moved": sum(r["result"] == "moved" for r in results), "renamed": sum(r["result"] == "renamed" for r in results),
            "preserved": preserved, "failed": [r for r in results if r["result"] in ("failed", "moved-unverified", "skipped", "unverified")]}


def render_organize(inv_after: dict, plan: dict, res: dict | None, idempotent: bool | None = None) -> str:
    w = inv_after["workspaces"][0]
    changed = {}
    if res:
        for r in res["results"]:
            if r["op"] == "move" and r["result"] == "moved":
                changed[r["new_pane_id"]] = f"moved from {short_id(r['from_tab'])}"
            elif r["op"] == "rename" and r["result"] == "renamed":
                changed.setdefault(r["for_pane"], "tab renamed")
    else:
        for o in plan["ops"]:
            changed[o.get("pane_id") or o["for_pane"]] = ("would move → new tab" if o["op"] == "move" else f"would rename tab \"{o['from_label']}\"")
    head = f"**Herdr organize — {w['label']}** (`{w['workspace_id']}`)" + (" — DRY RUN" if res is None else "")
    rows = ["| Pane | Tab | Session | Agent | State | Change |", "|---|---|---|---|---|---|"]
    for s in sorted(w["sessions"], key=lambda s: (s["tab_id"], s["order"])):
        mark = "? " if s["questionable"] else ""
        rows.append(f"| {s['pane']} | {short_id(s['tab_id'])} | {mark}{_cell(s['name'] or '(untitled)', 56)}{' (this session)' if s['is_self'] else ''} | "
                    f"{s['agent_kind'] if s['agent_kind'] != 'none' else '—'} | {state_word(s)} | {changed.get(s['pane_id'], 'unchanged')} |")
    out = [head, ""] + rows + [""]
    if res:
        c = res["checks"]
        ok = lambda b: "✓" if b else "✗"
        out.append(f"Checks: workspace isolation {ok(c['workspace_isolation'])} · pane count unchanged {ok(c['pane_count_unchanged'])} · "
                   f"process continuity {ok(c['process_continuity'])} · one pane per tab {ok(c['one_pane_per_tab'])} · "
                   f"focus unchanged {ok(c['focus_unchanged'])}" + (f" · idempotent re-run {ok(idempotent)}" if idempotent is not None else ""))
        for r in res["failed"]:
            out.append(f"- **{r['result']}** `{r.get('pane_id') or r.get('tab_id')}` — {r.get('reason') or '; '.join(r.get('problems', []))}")
        for r in res["results"]:
            for wmsg in r.get("warnings") or []:
                out.append(f"- note `{r['pane_id']}`: {wmsg}")
        if res["aborted"]:
            out.append(f"- ABORTED: {res['aborted']}")
        for k in c.get("continuity_problems", []):
            out.append(f"- continuity: {k}")
    q = [s for s in w["sessions"] if s["questionable"]]
    if q:
        out.append("Questionable titles (marked ?): " + "; ".join(f"{s['pane']} — {s['title_issue']}" for s in q)
                   + ". Suggest `/herdr-session-manager review --unknown` (not run automatically).")
    out += attention_footer(w)
    if res:
        out += ["", f"Organized: {res['moved']} panes moved, {res['renamed']} tabs renamed, {res['preserved']} sessions preserved."]
    else:
        n_move = sum(o["op"] == "move" for o in plan["ops"])
        n_ren = sum(o["op"] == "rename" for o in plan["ops"])
        out += ["", "Already organized — no changes needed." if plan["noop"] else f"Dry run: would move {n_move} panes and rename {n_ren} tabs."]
    return "\n".join(out)


# =========================================================================== rename / resolve

def resolve_targets(inv: dict, tokens: list[str]) -> tuple[list[dict], list[str]]:
    """Resolve pane ids (p4, w6:p4), agent names, titles or unique partial matches. Never guesses."""
    ss = [s for w in inv["workspaces"] for s in w["sessions"]]
    found, errors = [], []
    for tok in tokens:
        t = tok.strip()
        hit = [s for s in ss if s["pane_id"] == t or s["pane"] == t]
        if not hit:
            low = t.lower()
            fields = lambda s: [x for x in (s["name"], s["title"], s["agent_name"], s["tab_label"]) if x]
            hit = [s for s in ss if any(f.lower() == low for f in fields(s))]
            if not hit:
                hit = [s for s in ss if any(low in f.lower() for f in fields(s))]
        if len(hit) == 1:
            if hit[0] not in found:
                found.append(hit[0])
        elif not hit:
            errors.append(f"no session matches {t!r} in this workspace")
        else:
            errors.append(f"{t!r} is ambiguous — matches " + ", ".join(f"{s['pane']} ({s['name']})" for s in hit) + "; use a pane id")
    return found, errors


def do_rename(h, inv: dict, target: dict, title: str, cache, shared_ok: bool = False, if_questionable: bool = False) -> dict:
    title = title.strip()
    if not title:
        return {"result": "refused", "reason": "empty title"}
    if if_questionable and not target["questionable"]:
        return {"result": "refused", "pane": target["pane"], "reason": "title is already meaningful; --apply only renames identified unclear titles"}
    if target["tab_pane_count"] > 1 and not shared_ok:
        return {"result": "refused", "pane": target["pane"], "tab": target["tab_id"],
                "reason": f"tab {short_id(target['tab_id'])} is shared by {target['tab_pane_count']} panes; renaming it would misrepresent the others. "
                          "Run `/herdr-session-manager organize` first, or pass --shared to rename the shared tab deliberately."}
    from titles import tab_title
    title = tab_title(title)
    old = target["tab_label"]
    h.call("tab", "rename", target["tab_id"], title)
    got = h.call("tab", "get", target["tab_id"])["result"]["tab"].get("label")
    p = h.call("pane", "get", target["pane_id"])["result"]["pane"]
    ok = got == title and p.get("terminal_id") == target["terminal_id"]
    if ok and cache:
        cache.record_rename(target["session_key"], title, target["boundary"])
    return {"result": "renamed" if ok else "unverified", "pane": target["pane"], "tab": target["tab_id"], "from": old, "to": got,
            "session_unchanged": p.get("terminal_id") == target["terminal_id"]}


# =========================================================================== level 2 evidence

_CHROME = [
    re.compile(r"^[\s─━═│┃╭╮╰╯┌┐└┘├┤┬┴┼▔▁\-_=]+$"),
    re.compile(r"^\s*[❯›>]\s*$"),
    re.compile(r"⏵⏵|shift\+tab to cycle|\? for shortcuts|esc to interrupt|ctrl\+[a-z] to", re.I),
    re.compile(r"^\s*[✻✳✶✢·*◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*\w+(?:…|\.\.\.)?\s*(\(|for\s+\d|·|$)", re.I),
    re.compile(r"^\s*new task\? /cle", re.I),
    re.compile(r"^\s*(Context left|auto-compact|tokens? used)", re.I),
    re.compile(r"\d+ new messages? \(click\)|^\s*(Read|Referenced file|Skills restored|Stop says)\b|^\s*\.\./|✔ Update installed|Restart to update|Compacted while idle|idleCompaction|^\s*[✻✳✶✢]\s*\w+ for \d", re.I),
]
_TIMER = re.compile(r"\b\d+m\s*\d+s\b|\b\d+s\b|\b\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM)?\b", re.I)
_ERROR = re.compile(r"(Traceback \(most recent call last\)|\bAPI Error\b|\bfatal:|\bpanic:|\bError: |\bENOENT\b|rate limit|overloaded_error|Request timed out|connection refused|command not found)", re.I)
_COMPLETE = re.compile(r"(all (tasks|work|items) (are )?(complete|completed|done)|\bwork is complete\b|session (is )?closed|close-?out complete|✅ (done|complete)|nothing (left|remaining) to do)", re.I)
_ASKS = re.compile(r"(want me to|should i\b|shall i\b|do you want|would you like|which (option|one)|please (confirm|approve|choose|provide)|awaiting (your|approval)|need(s)? your (decision|approval|input)|(confirmation|decision|approval|input) from you|before you merge|waiting on your call|your call|approve\?|proceed\?|\(y/n\)|\[y/n\])", re.I)
_PR = re.compile(r"\bPR\s*#?(\d{1,6})\b|\bpull/(\d{1,6})\b", re.I)


_SEP = re.compile(r"^\s*[─━═]{8,}\s*$")
_PROMPT = re.compile(r"^\s*[❯›]")


def strip_input_area(text: str) -> str:
    """Drop the agent's input box and footer: the Operator's unsent draft is never evidence."""
    lines = (text or "").splitlines()
    idx = next((i for i in range(len(lines) - 1, -1, -1) if _PROMPT.match(lines[i])), None)
    if idx is None:
        return text or ""
    cut = idx
    for j in range(idx - 1, max(-1, idx - 3), -1):
        if _SEP.match(lines[j]):
            cut = j
            break
    return "\n".join(lines[:cut])


def meaningful_lines(text: str) -> list[str]:
    return [l.strip() for l in strip_input_area(text).splitlines() if l.strip() and not any(p.search(l) for p in _CHROME)]


def fingerprint(text: str) -> str | None:
    lines = meaningful_lines(text)
    if not lines:
        return None
    return hashlib.sha256("\n".join(_TIMER.sub("#", l) for l in lines).encode()).hexdigest()[:16]


def last_line(text: str, width: int = 110) -> str:
    """The agent's latest message: the last '⏺' text line (not a tool call) if visible,
    else the last paragraph. Tool echoes ('⎿') are skipped."""
    lines = [l for l in meaningful_lines(text) if not l.startswith("⎿")]
    msg = [i for i, l in enumerate(lines) if l.startswith(("⏺", "●", "•")) and not re.match(r"^[⏺●•]\s*\w+\(", l)]
    if msg:
        i = msg[-1]
        s = re.sub(r"^[⏺●•]\s*", "", " ".join(lines[i:i + 3]))
        return (s[: width - 1] + "…") if len(s) > width else s
    tail: list[str] = []
    for l in reversed(lines):
        tail.insert(0, l)
        if len(" ".join(tail)) > width or (l.endswith((".", ":", "?", "!")) and len(tail) > 1):
            break
    s = re.sub(r"^[⏺●•\-*]\s*", "", " ".join(tail))
    return (s[: width - 1] + "…") if len(s) > width else s


def classify(s: dict, tail: str) -> tuple[str, str]:
    """(state, basis). States: Working, Idle, Blocked, Completed, Error, Unknown.

    Idle never means finished; Completed needs an explicit completion statement and still
    does not prove the underlying project work is complete.
    """
    st = (s.get("herdr_status") or "").lower()
    recent = "\n".join(meaningful_lines(tail)[-8:])
    if s["agent_kind"] == "none":
        return ("Idle", "no recognized agent in pane")
    if st == "working":
        return "Working", "herdr agent_status=working"
    if st == "blocked":
        return "Blocked", "herdr recognized an approval/question UI"
    if st in ("idle", "done"):
        if _ERROR.search(recent):
            return "Error", f"agent_status={st}; error text in recent output"
        if _COMPLETE.search(recent):
            return "Completed", f"agent_status={st}; explicit completion statement in recent output"
        return "Idle", f"agent_status={st}" + ("; finished turn not yet viewed" if st == "done" else "")
    return "Unknown", f"agent_status={st or 'none'} (does not prove completion)"


def native_history(kind: str, session_id: str | None, max_chars: int = 600) -> dict | None:
    """Read-only peek at a native Claude/Codex transcript: first user prompt + last assistant text."""
    if not session_id or not re.match(r"^[0-9a-fA-F\-]{8,64}$", session_id):
        return None
    home = os.path.expanduser("~")
    if kind == "claude":
        paths = glob.glob(os.path.join(home, ".claude", "projects", "*", f"{session_id}.jsonl"))
    elif kind == "codex":
        paths = glob.glob(os.path.join(home, ".codex", "sessions", "**", f"*{session_id}*.jsonl"), recursive=True)
    else:
        return None
    if not paths:
        return None
    first_user, last_asst = None, None
    try:
        with open(paths[0], "rb") as fh:
            for i, raw in enumerate(fh):
                if i > 400 or first_user:
                    break
                first_user = _extract_text(raw, "user") or first_user
            size = fh.seek(0, 2)
            fh.seek(max(0, size - 400_000))
            for raw in fh.read().splitlines()[1:]:
                t = _extract_text(raw, "assistant")
                if t:
                    last_asst = t
    except OSError:
        return None
    clip = lambda x: redact(x)[:max_chars] if x else None
    return {"source": "native transcript (read-only, bounded)", "first_user_prompt": clip(first_user), "last_assistant_text": clip(last_asst)}


def _extract_text(raw: bytes, role: str) -> str | None:
    try:
        o = json.loads(raw)
    except Exception:
        return None
    msg = o.get("message") if isinstance(o.get("message"), dict) else None
    payload = o.get("payload") if isinstance(o.get("payload"), dict) else None
    if msg and (o.get("type") == role or msg.get("role") == role):
        c = msg.get("content")
    elif payload and payload.get("type") == "message" and payload.get("role") == role:
        c = payload.get("content")
    else:
        return None
    if isinstance(c, str):
        text = c
    elif isinstance(c, list):
        text = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") in ("text", "input_text", "output_text"))
    else:
        return None
    text = text.strip()
    if not text or (role == "user" and text.startswith("<")):
        return None
    return text


def apply_card_gate(ev: dict, s: dict) -> dict:
    """Decide whether a needs-me signal may become a card.

    A human ack matches the live fingerprint and clears needs_me until the pane
    evidence changes. A cached needs_me whose fingerprint does not match the
    read just taken is stale and non-actionable. A live ask with a failed read
    is also non-actionable: the fingerprint was not confirmed.
    """
    fp = ev.get("fingerprint")
    cs = s.get("cached_summary") or {}
    ack = s.get("human_ack") or {}
    observed = bool(ev.get("needs_me"))
    cached_needs = bool(cs.get("needs_me"))
    fresh_summary = bool(fp and cs.get("fingerprint") == fp)
    acked = bool(fp and ack.get("fingerprint") == fp)
    ev["human_acked"] = acked
    ev["human_acked_at"] = ack.get("human_acked_at") if acked else None
    if acked and observed:
        ev["needs_me_suppressed"] = "human_acked"
        ev["suppressed_needs_reason"] = ev.get("needs_reason")
        ev["needs_me"] = False
        ev["needs_reason"] = None
    # Stale only when the cached ask was NOT confirmed by this read.
    ev["stale_needs_me"] = bool(cached_needs and not fresh_summary and not observed)
    live_blocked = ev.get("state") == "Blocked" and not ev.get("read_error")
    if ev.get("needs_me") and not ev.get("read_error") and (fp or live_blocked):
        ev["needs_me_actionable"] = True
        ev["card_gate"] = "fresh"
    elif ev.get("needs_me"):
        ev["needs_me_actionable"] = False
        ev["card_gate"] = "stale_fingerprint"
    elif acked:
        ev["needs_me_actionable"] = False
        ev["card_gate"] = "human_acked"
    elif ev["stale_needs_me"]:
        ev["needs_me_actionable"] = False
        ev["card_gate"] = "stale_fingerprint"
    else:
        ev["needs_me_actionable"] = False
        ev["card_gate"] = "none"
    return ev


def evidence(h, s: dict, lines: int, history: bool) -> dict:
    """Bounded, redacted evidence for ONE session. Reads terminal output; optionally native history."""
    tail, err = "", None
    if s["agent_kind"] != "none":          # plain shells/processes are NEVER read (could show `cat .env`)
        try:
            tail = h.read(s["pane_id"], lines)
        except HerdrError as e:
            err = str(e)[:160]
    state, basis = classify(s, tail)
    recent = "\n".join(meaningful_lines(tail)[-8:])
    fp = fingerprint(tail)
    cs = s.get("cached_summary")
    needs_me, why = False, None
    if not s["is_self"]:
        if state == "Blocked":
            needs_me, why = True, "approval or question pending in the agent UI"
        elif state in ("Idle", "Completed") and _ASKS.search(recent):
            needs_me, why = True, "agent asked a question"
        elif state == "Error":
            needs_me, why = True, "stopped on an error"
        elif cs and fp and cs.get("fingerprint") == fp and cs.get("needs_me"):
            needs_me, why = True, f"needs you (cached {_hm(cs.get('updated_at', ''))}): {cs.get('next') or cs.get('outstanding') or ''}".rstrip(": ")
    ev = {**session_identity(s), "title": s["name"], "title_issue": s["title_issue"],
          "agent_kind": s["agent_kind"], "boundary": s["boundary"],
          "state": state, "state_basis": basis, "needs_me": needs_me, "needs_reason": why, "is_self": s["is_self"],
          "fingerprint": fp, "last_output": redact(last_line(tail)), "refs": sorted({f"PR {m.group(1) or m.group(2)}" for m in _PR.finditer(redact(recent))})[:3],
          "cached_summary": cs, "summary_fresh": bool(cs and fp and cs.get("fingerprint") == fp),
          "read_error": err, "evidence_at": _now(), "open_hints": open_hints(s)}
    apply_card_gate(ev, s)
    if lines >= REVIEW_LINES or history:
        ev["recent_output"] = redact("\n".join(meaningful_lines(tail)[-lines:]))
    if history and s["agent_kind"] in ("claude", "codex"):
        ev["native_history"] = native_history(s["agent_kind"], s["agent_session_id"])
    return ev


def review_packet(h, inv: dict, targets: list[dict], mode: str) -> dict:
    return {"schema": SCHEMA, "kind": "review", "level": 2, "mode": mode, "generated_at": _now(),
            "card_gate_rule": CARD_GATE_RULE,
            "instructions": "For each session write: Session / Identity / Suggested title / Goal / Done / Remaining / Needs me. "
                            "Identity is the full agent_session_id, agent_name, cwd, pane_id, tab_id and workspace_id "
                            "(never a shortened session id). Label verified facts vs. inferences. "
                            "Do NOT rename unless the Operator asked (rename or --apply). "
                            "Do NOT run open_hints. A needs-me card requires needs_me_actionable and a fresh inspect.",
            "sessions": [evidence(h, s, REVIEW_LINES, history=True) for s in targets]}


# =========================================================================== remaining / status (level 2, cached first)

GROUPS = [("needs_me", "Needs my approval or decision"), ("blocked_failed", "Blocked or failed"),
          ("active", "Active work"), ("idle_unfinished", "Idle — unfinished or unknown"), ("completed", "Completed (agent says so)")]


def _group(ev: dict) -> str:
    # Cards follow the fresh-inspect gate. A raw needs_me that is stale or
    # already human-acked stays out of the needs-me group.
    if ev.get("needs_me_actionable") and ev["state"] != "Error":
        return "needs_me"
    if ev["state"] in ("Blocked", "Error"):
        return "blocked_failed"
    if ev["state"] == "Working":
        return "active"
    if ev["state"] == "Completed":
        return "completed"
    return "idle_unfinished"


def remaining_packet(h, inv: dict, max_inspect: int = 6) -> dict:
    """Cached summaries first. Only sessions whose evidence changed (fingerprint) or that have no
    summary are flagged `inspect`; up to max_inspect of them get bounded native history."""
    out = {"schema": SCHEMA, "kind": "remaining", "level": 2, "generated_at": _now(),
           "card_gate_rule": CARD_GATE_RULE, "workspaces": []}
    budget = max_inspect
    for w in inv["workspaces"]:
        groups: dict[str, list] = {g: [] for g, _ in GROUPS}
        for s in w["sessions"]:
            if s["is_self"] or s["agent_kind"] == "none":
                continue
            ev = evidence(h, s, TAIL_LINES, history=False)
            ev["inspect"] = not ev["summary_fresh"]
            if ev["inspect"] and budget > 0:
                ev["native_history"] = native_history(s["agent_kind"], s["agent_session_id"], 500)
                ev["recent_output"] = redact("\n".join(meaningful_lines(h.read(s["pane_id"], TAIL_LINES) if not ev["read_error"] else "")[-12:]))
                budget -= 1
            ev["remaining"] = (ev["cached_summary"] or {}).get("outstanding") if ev["summary_fresh"] else None
            groups[_group(ev)].append(ev)
        out["workspaces"].append({"workspace_id": w["workspace_id"], "label": w["label"], "groups": groups})
    out["instructions"] = ("Write one sentence per session under the five headings, in order. Use cached remaining work where "
                           "summary_fresh is true. For inspect=true sessions derive remaining work ONLY from the evidence given; if it "
                           "cannot be determined say 'Unknown'. Never invent tasks; idle ≠ finished; completed agent state ≠ project "
                           "complete. Include the full identity line (agent_session_id, agent_name, cwd, pane_id, tab_id, "
                           "workspace_id). Card only sessions with needs_me_actionable true after a fresh inspect. "
                           "Do not run open_hints. Then cache each result with `inventory.py remember`.")
    return out


def render_remaining(pk: dict) -> str:
    blocks = []
    for w in pk["workspaces"]:
        lines = [f"**Remaining work — {w['label']}** (`{w['workspace_id']}`, as of {_hm(pk['generated_at'])})"]
        for g, title in GROUPS:
            items = w["groups"][g]
            if not items:
                continue
            lines.append(f"\n**{title}**")
            for ev in items:
                what = ev["remaining"] or ("Unknown — needs inspection" if ev["inspect"] else "Unknown")
                why = f" — {ev['needs_reason']}" if ev["needs_reason"] else ""
                held = ""
                if ev.get("card_gate") == "human_acked":
                    held = " — human ack; do not re-card until pane evidence changes"
                elif ev.get("card_gate") == "stale_fingerprint":
                    held = " — stale fingerprint; not actionable until re-inspect"
                lines.append(f"- `{ev['pane']}` {_cell(ev['title'] or '(untitled)', 50)}: {_cell(what, 110)}{why}"
                             + (" (cached)" if ev["summary_fresh"] else "") + held)
                lines.append(f"  {format_identity(ev)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_status(inv: dict, evs: dict[str, dict]) -> str:
    blocks = []
    for w in inv["workspaces"]:
        ss = w["sessions"]
        es = [evs[s["pane_id"]] for s in ss if s["pane_id"] in evs]
        n_work = sum(e["state"] == "Working" for e in es)
        n_block = sum(e["state"] == "Blocked" for e in es)
        n_me = sum(bool(e.get("needs_me_actionable")) for e in es)
        lines = [f"**Workspace: {w['label'] or w['workspace_id']}** (`{w['workspace_id']}`, as of {_hm(inv['generated_at'])})",
                 f"Sessions: {len(ss)} | Working: {n_work} | Blocked: {n_block} | Needs me: {n_me}", ""]
        if not ss:
            return "\n".join(lines + ["Empty workspace — no sessions."])
        order = {"needs_me": 0, "blocked_failed": 1, "active": 2, "idle_unfinished": 3, "completed": 4}
        es.sort(key=lambda e: (e["is_self"], order[_group(e)], e["pane"]))
        lines += ["| Session | State | Progress / outstanding | Next |", "|---|---|---|---|"]
        for e in es:
            cs = e["cached_summary"]
            if cs and (cs.get("current") or cs.get("outstanding")):
                prog = " · ".join(x for x in (cs.get("current") or cs.get("completed"), ("Left: " + cs["outstanding"]) if cs.get("outstanding") else None) if x)
                prog += "" if e["summary_fresh"] else f" (summary from {_hm(cs.get('updated_at', ''))}; evidence changed since)"
            else:
                prog = e["last_output"] or "No recent output"
            if e["is_self"]:
                nxt = "— (this session)"
            elif e.get("card_gate") == "human_acked":
                nxt = "Answered — no re-card until pane evidence changes"
            elif e.get("card_gate") == "stale_fingerprint":
                nxt = "Not actionable — stale fingerprint; re-inspect"
            else:
                nxt = {
                    "Blocked": f"Answer the prompt in {e['pane']}", "Error": "Inspect the error", "Working": "Let it run",
                    "Completed": "Verify result; close or assign next", "Unknown": "Inspect pane"}.get(e["state"], "Awaiting next task")
                if e.get("needs_me_actionable") and e["state"] == "Idle":
                    nxt = "Reply to its question"
            q = "? " if next((s for s in ss if s["pane_id"] == e["pane_id"]), {}).get("questionable") else ""
            flag = " ⚑" if e.get("needs_me_actionable") else ""
            lines.append(f"| `{e['pane']}` {q}{_cell(e['title'] or '(untitled)', 40)} | {e['state']}{flag} | {_cell(prog, 100)} | {nxt} |")
        me = [e for e in es if e.get("needs_me_actionable")]
        lines += ["", "**Needs me**"]
        if me:
            for e in me:
                refs = f" ({', '.join(e['refs'])})" if e["refs"] else ""
                lines.append(f"- `{e['pane']}` {e['title'] or ''} — {e['needs_reason']}{refs}")
                lines.append(f"  {format_identity(e)}")
        else:
            lines.append("- None.")
        answered = [e for e in es if e.get("card_gate") == "human_acked" and not e["is_self"]]
        unconfirmed = [e for e in es if e.get("card_gate") == "stale_fingerprint" and not e["is_self"]]
        if answered:
            lines.append("\n**Already answered** (no re-card until pane evidence changes)")
            for e in answered:
                lines.append(f"- `{e['pane']}` {e['title'] or ''} — human ack {_hm(e.get('human_acked_at') or '')}")
                lines.append(f"  {format_identity(e)}")
        if unconfirmed:
            lines.append("\n**Not actionable** (stale fingerprint — re-inspect before any card)")
            for e in unconfirmed:
                lines.append(f"- `{e['pane']}` {e['title'] or ''} — run status or watch again before carding")
                lines.append(f"  {format_identity(e)}")
        stale = [e for e in es if e["cached_summary"] and not e["summary_fresh"] and not e["is_self"]]
        nosum = [e for e in es if not e["cached_summary"] and not e["is_self"] and e["agent_kind"] != "none"]
        if stale or nosum:
            lines.append(f"\nEvidence: {len(stale)} cached summary(ies) stale, {len(nosum)} session(s) never summarized — "
                         "`/herdr-session-manager remaining` refreshes them.")
        if not me and not any(e["state"] in ("Blocked", "Error") for e in es):
            lines.append("\nNo action required.")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# =========================================================================== watch

def _snapshot_indexes(prev_s: dict) -> tuple[dict, dict, dict]:
    """Index a snapshot by session key, agent session id, and terminal id.

    Older snapshots used the terminal id as the dict key and did not store
    session_key. A record is still findable by that terminal id, and by
    agent_session_id when the record has one.
    """
    by_sk: dict[str, dict] = {}
    by_sid: dict[str, dict] = {}
    by_term: dict[str, dict] = {}
    for key, rec in prev_s.items():
        if not isinstance(rec, dict):
            continue
        sk = rec.get("session_key")
        if not sk and isinstance(key, str) and key.startswith(("agent:", "term:")):
            sk = key
        sid = rec.get("agent_session_id")
        if not sid and isinstance(sk, str) and sk.startswith("agent:"):
            sid = sk[len("agent:"):]
        tid = rec.get("terminal_id")
        if not tid and isinstance(key, str) and not key.startswith(("agent:", "term:")):
            tid = key
        if sk:
            by_sk[sk] = rec
        if sid:
            by_sid[sid] = rec
        if tid:
            by_term[tid] = rec
    return by_sk, by_sid, by_term


def _match_snapshot(s: dict, by_sk: dict, by_sid: dict, by_term: dict) -> tuple[dict | None, str]:
    """Match a live session to the previous snapshot.

    The agent session is primary. terminal_id is only the fallback, so a pane
    respawn that keeps agent_session_id stays the same session.
    """
    sk = s.get("session_key")
    if sk and sk in by_sk:
        return by_sk[sk], "session"
    sid = s.get("agent_session_id")
    if sid and sid in by_sid:
        return by_sid[sid], "session"
    tid = s.get("terminal_id")
    if tid and tid in by_term:
        return by_term[tid], "terminal"
    return None, ""


def _prev_terminal(rec: dict, prev_s: dict) -> str | None:
    if rec.get("terminal_id"):
        return rec["terminal_id"]
    for key, val in prev_s.items():
        if val is rec and isinstance(key, str) and not key.startswith(("agent:", "term:")):
            return key
    return None


def _watch_event(kind: str, s: dict, message: str, e: dict | None = None) -> dict:
    ev = {"type": kind, "pane": s.get("pane") or short_id(s.get("pane_id") or "?"), "message": message, **session_identity(s)}
    if e:
        ev["fingerprint"] = e.get("fingerprint")
        ev["needs_me_actionable"] = bool(e.get("needs_me_actionable"))
        ev["card_gate"] = e.get("card_gate")
        ev["evidence_at"] = e.get("evidence_at")
    return ev


def _snapshot_record(s: dict, e: dict, now: str, since: str) -> dict:
    """Metadata only. No terminal text."""
    return {
        "session_key": s.get("session_key"),
        "agent_session_id": s.get("agent_session_id"),
        "terminal_id": s.get("terminal_id"),
        "pane_id": s.get("pane_id"),
        "tab_id": s.get("tab_id"),
        "workspace_id": s.get("workspace_id"),
        "cwd": redact(s.get("cwd") or "")[:300] or None,
        "agent_name": redact(s.get("agent_name") or "")[:120] or None,
        "agent_kind": s.get("agent_kind"),
        "state": e.get("state"),
        "needs_me": bool(e.get("needs_me")),
        "needs_me_actionable": bool(e.get("needs_me_actionable")),
        "card_gate": e.get("card_gate"),
        "fingerprint": e.get("fingerprint"),
        "human_acked": bool(e.get("human_acked")),
        "seen_at": now,
        "unchanged_since": since,
    }


def watch(h, inv: dict, cache, stale_minutes: int) -> dict:
    now = inv["generated_at"]
    events, baseline = [], []
    for w in inv["workspaces"]:
        prev = cache.load_snapshot(w["workspace_id"]) or {}
        prev_s = prev.get("sessions", {})
        by_sk, by_sid, by_term = _snapshot_indexes(prev_s)
        cur_s: dict[str, dict] = {}
        consumed: set[int] = set()
        for s in w["sessions"]:
            e = evidence(h, s, TAIL_LINES, history=False)
            p, how = _match_snapshot(s, by_sk, by_sid, by_term)
            since = p.get("unchanged_since", now) if p and p.get("fingerprint") == e["fingerprint"] and p.get("state") == e["state"] else now
            cur_s[s["session_key"]] = _snapshot_record(s, e, now, since)
            if p is not None:
                consumed.add(id(p))
            if s["is_self"] or not prev:
                continue
            label = f"{s['pane']} {s['name'] or s['agent_name'] or ''}".strip()
            if s.get("agent_session_id"):
                label += f" session {s['agent_session_id']}"
            if p is None:
                events.append(_watch_event("new-session", s, f"{label}: new {s['agent_kind']} session ({e['state']})", e))
                continue
            prev_sid = p.get("agent_session_id")
            cur_sid = s.get("agent_session_id")
            if how == "terminal" and prev_sid and cur_sid and prev_sid != cur_sid:
                events.append(_watch_event("new-session", s, f"{label}: agent session replaced in the same terminal (previous session {prev_sid})", e))
                continue
            prev_tid = _prev_terminal(p, prev_s)
            if how == "session" and prev_tid and s.get("terminal_id") and prev_tid != s.get("terminal_id"):
                events.append(_watch_event(
                    "respawned", s,
                    f"{label}: pane respawned (terminal {prev_tid} → {s['terminal_id']}); agent_session_id unchanged", e))
            if e["state"] == "Blocked" and p.get("state") != "Blocked" and e.get("needs_me_actionable"):
                events.append(_watch_event("newly-blocked", s, f"{label}: now blocked — approval or question pending", e))
            elif e.get("needs_me_actionable") and not p.get("needs_me") and not p.get("needs_me_actionable"):
                events.append(_watch_event("needs-me", s, f"{label}: {e['needs_reason']}", e))
            if p.get("agent_kind") in ("claude", "codex", "other") and s["agent_kind"] == "none":
                events.append(_watch_event("agent-exited", s, f"{label}: agent process is gone; pane is back at a shell", e))
            if e["state"] == "Completed" and p.get("state") != "Completed":
                events.append(_watch_event("completed", s, f"{label}: agent reports completion — verify before closing", e))
            elif e["state"] == "Error" and p.get("state") != "Error":
                events.append(_watch_event("error", s, f"{label}: stopped on an error", e))
            elif p.get("state") == "Working" and e["state"] == "Idle":
                events.append(_watch_event("turn-finished", s, f"{label}: finished a turn — {_cell(e['last_output'], 80)}", e))
            mins = _minutes_between(since, now)
            if mins is not None and mins >= stale_minutes:
                if e["state"] == "Idle" and s["agent_kind"] != "none":
                    events.append(_watch_event("inactive", s, f"{label}: idle and unchanged for {mins}m", e))
                elif e["state"] == "Working":
                    events.append(_watch_event("quiet-working", s, f"{label}: working, output unchanged {mins}m (may be a long-running command; not a failure)", e))
        for p in prev_s.values():
            if not isinstance(p, dict) or id(p) in consumed or p.get("agent_kind") == "none":
                continue
            gone = dict(p)
            if not gone.get("pane") and gone.get("pane_id"):
                gone["pane"] = short_id(gone["pane_id"])
            who = gone.get("agent_session_id") or gone.get("agent_name") or gone.get("pane_id")
            events.append(_watch_event("pane-gone", gone, f"{who}: pane no longer exists"))
        cache.save_snapshot(w["workspace_id"], {"saved_at": now, "workspace_id": w["workspace_id"], "sessions": cur_s})
        if not prev:
            baseline.append(w["workspace_id"])
    return {"schema": SCHEMA, "kind": "watch", "generated_at": now, "stale_minutes": stale_minutes,
            "baseline_created": baseline, "card_gate_rule": CARD_GATE_RULE, "events": events}


def render_watch(res: dict, inv: dict) -> str:
    ws = ", ".join(f"{w['label']} (`{w['workspace_id']}`)" for w in inv["workspaces"])
    head = f"**Herdr watch — {ws}** (as of {_hm(res['generated_at'])}, inactivity threshold {res['stale_minutes']}m)"
    if res["baseline_created"] and not res["events"]:
        return head + "\nBaseline snapshot recorded; changes are reported from the next pass."
    if not res["events"]:
        return head + "\nNo meaningful changes. No action required."
    rows = []
    for e in res["events"]:
        rows.append(f"- **{e['type']}** `{e['pane']}` — {e['message']}")
        rows.append(f"  {format_identity(e)}")
    return head + "\n" + "\n".join(rows)


# =========================================================================== files (Yazi in the skill's own tab)

FILES_LABEL = "FILES"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def yazi_bin() -> str | None:
    import shutil
    return os.environ.get("HSM_YAZI_BIN") or shutil.which("yazi") or next(
        (c for c in ("/opt/homebrew/bin/yazi", "/usr/local/bin/yazi") if os.access(c, os.X_OK)), None)


def yazi_config_ok() -> tuple[bool, str]:
    """Yazi silently falls back to presets on a config error, which would drop the
    sensitive-preview guard. Validate first via `ya env` (needs a pty → `script`)."""
    if os.environ.get("HSM_SKIP_YAZI_CHECK") == "1":
        return True, "skipped"
    import shutil
    ya = shutil.which("ya") or next((c for c in ("/opt/homebrew/bin/ya", "/usr/local/bin/ya") if os.access(c, os.X_OK)), None)
    if not ya:
        return False, "`ya` not found — cannot verify the sensitive-preview guard config"
    env = dict(os.environ, YAZI_CONFIG_HOME=os.path.join(SKILL_DIR, "config", "yazi"))
    try:
        cp = subprocess.run(["script", "-q", "/dev/null", ya, "env"], env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not run `ya env`: {e}"
    text = cp.stdout + cp.stderr
    if "Failed to parse config" in text or "parse error" in text.lower():
        return False, "Yazi config failed to parse — the sensitive-preview guard would be dropped"
    if "yazi.toml" not in text:
        return False, "`ya env` did not report the skill's yazi.toml"
    return True, "config parses"


def _yazi_cmd(root: str, transient: bool) -> str:
    import shlex
    cfg = os.path.join(SKILL_DIR, "config", "yazi")
    cmd = f"YAZI_CONFIG_HOME={shlex.quote(cfg)} {shlex.quote(yazi_bin() or 'yazi')} {shlex.quote(root)}"
    return cmd + ("; exit" if transient else "")


def _leader_name(h, pane_id: str) -> str:
    try:
        proc = h.call("pane", "process-info", "--pane", pane_id)["result"]["process_info"]
    except HerdrError:
        return ""
    return os.path.basename(((_leader(proc) or {}).get("name") or "")).lstrip("-").lower()


def files(h, ctx: dict, inv: dict, root: str | None, persistent: bool, wait: float = 3.0, focus: bool = True, cache: "Cache | None" = None) -> dict:
    """Open Yazi in a tab this skill owns. Never runs anything in an agent's pane."""
    import time
    ws = ctx["workspace_id"] or ctx["self_workspace"]
    if not yazi_bin():
        return {"result": "unavailable", "reason": "yazi is not installed (brew install yazi)"}
    ok, why = yazi_config_ok()
    if not ok:
        return {"result": "refused", "reason": why}
    if not root:
        me = h.call("pane", "get", ctx["self_pane"])["result"]["pane"]
        root = me.get("foreground_cwd") or me.get("cwd")
    root = os.path.abspath(os.path.expanduser(root or ""))
    if not os.path.isdir(root):
        return {"result": "failed", "reason": f"project root is not a directory: {root!r}"}
    out = {"workspace_id": ws, "root": root, "persistent": persistent}
    if persistent:
        w = next(w for w in inv["workspaces"] if w["workspace_id"] == ws)
        tabs = [t for t in w["tabs"] if (t.get("label") or "").upper() == FILES_LABEL]
        if len(tabs) > 1:
            return {**out, "result": "refused", "reason": f"{len(tabs)} tabs are labelled {FILES_LABEL!r}; rename extras first"}
        if tabs:
            t = tabs[0]
            members = [s for s in w["sessions"] if s["tab_id"] == t["tab_id"]]
            if len(members) != 1 or members[0]["agent_kind"] != "none":
                return {**out, "result": "refused", "tab_id": t["tab_id"],
                        "reason": f"existing {FILES_LABEL!r} tab holds {len(members)} pane(s) incl. an agent — not reusing it"}
            pane = members[0]["pane_id"]
            lead = _leader_name(h, pane)
            if lead == "yazi":
                if focus:
                    h.call("tab", "focus", t["tab_id"])
                return {**out, "result": "reused", "tab_id": t["tab_id"], "pane_id": pane, "note": "Yazi already running; tab focused"}
            if lead in SHELL_NAMES and cache and members[0]["terminal_id"] in cache.own_terminals():
                h.call("pane", "run", pane, _yazi_cmd(root, transient=False))
                if focus:
                    h.call("tab", "focus", t["tab_id"])
                return {**out, "result": "restarted", "tab_id": t["tab_id"], "pane_id": pane}
            why = (f"{FILES_LABEL!r} tab is running {lead!r}" if lead not in SHELL_NAMES
                   else f"{FILES_LABEL!r} tab was not created by this skill — not running anything in it")
            return {**out, "result": "refused", "tab_id": t["tab_id"], "reason": why}
    from titles import tab_title
    label = FILES_LABEL if persistent else tab_title(f"{FILES_LABEL} · {os.path.basename(root) or root}")
    r = h.call("tab", "create", "--workspace", ws, "--cwd", root, "--label", label, "--focus" if focus else "--no-focus")["result"]
    tab_id, pane = r["tab"]["tab_id"], r["root_pane"]["pane_id"]
    if cache:
        cache.record_own_terminal(r["root_pane"].get("terminal_id"))
    lead = ""
    for _ in range(int(wait * 10)):
        lead = _leader_name(h, pane)
        if lead in SHELL_NAMES:
            break
        time.sleep(0.1)
    if lead not in SHELL_NAMES:
        return {**out, "result": "failed", "tab_id": tab_id, "pane_id": pane, "reason": f"new pane is not at a shell prompt ({lead!r}); nothing run"}
    h.call("pane", "run", pane, _yazi_cmd(root, transient=not persistent))
    running = False
    for _ in range(int(wait * 10)):
        if _leader_name(h, pane) == "yazi":
            running = True
            break
        time.sleep(0.1)
    return {**out, "result": "opened" if running else "started-unverified", "tab_id": tab_id, "pane_id": pane, "label": label}


SHELL_NAMES = {"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh", "nu"}


def render_files(r: dict) -> str:
    if r["result"] in ("opened", "reused", "restarted", "started-unverified"):
        verb = {"opened": "Opened", "reused": "Reused existing", "restarted": "Restarted Yazi in", "started-unverified": "Started (unverified)"}[r["result"]]
        return (f"{verb} file browser tab `{r.get('tab_id')}` at {r['root']}. Hidden files shown; mouse enabled; Enter opens in VS Code; "
                "previews disabled for .env/keys/credentials. Agent sessions untouched.")
    return f"File browser not opened: {r.get('reason')}"


# =========================================================================== VS Code (no transcript reading)

VSCODE_CANDIDATES = [
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    os.path.expanduser("~/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
]


def vscode_cli() -> str | None:
    import shutil
    env = os.environ.get("HSM_CODE_BIN")
    if env:
        return env
    return shutil.which("code") or next((c for c in VSCODE_CANDIDATES if os.access(c, os.X_OK)), None)


def launch(argv: list[str]) -> dict:
    """Start a GUI helper detached. HSM_DRY_LAUNCH=1 records instead of executing (tests)."""
    if os.environ.get("HSM_DRY_LAUNCH") == "1":
        return {"launched": False, "dry_run": True, "argv": argv}
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"launched": True, "argv": argv}


def project_dir(h, s: dict) -> str:
    """The pane's CURRENT foreground cwd, re-read from the server (not the cached inventory)."""
    p = h.call("pane", "get", s["pane_id"])["result"]["pane"]
    cwd = p.get("foreground_cwd") or p.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        raise HerdrError(f"pane {s['pane']} working directory is not an accessible directory: {cwd!r}")
    return cwd


def open_project(h, s: dict) -> dict:
    cwd = project_dir(h, s)
    code = vscode_cli()
    if not code:
        return {"result": "unavailable", "pane": s["pane"], "cwd": cwd, "reason": "VS Code CLI not found (install VS Code or set HSM_CODE_BIN)"}
    # No -r: VS Code focuses a window already showing this folder, else opens a new one —
    # it never replaces the folder of an unrelated window. argv list → spaces are safe.
    r = launch([code, cwd])
    return {"result": "opened" if r["launched"] else "dry-run", "pane": s["pane"], "cwd": cwd, "argv": r["argv"],
            "note": "project folder only — this does not attach to or display the agent session"}


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def claude_session_live(session_id: str, sessions_dir: str | None = None) -> bool:
    """Live-holder check the Claude extension itself uses: ~/.claude/sessions/<pid>.json.
    Reads ONLY *.json and ONLY pid/sessionId (the *.key files are never opened)."""
    d = sessions_dir or os.path.join(os.path.expanduser("~"), ".claude", "sessions")
    for f in glob.glob(os.path.join(d, "*.json")):
        try:
            with open(f) as fh:
                rec = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("sessionId") == session_id and _pid_alive(rec.get("pid")):
            return True
    return False


def codex_session_live(session_id: str, locks_dir: str | None = None) -> bool:
    d = locks_dir or os.path.join(os.path.expanduser("~"), ".codex", "thread-writer-locks")
    return os.path.exists(os.path.join(d, f"{session_id}.lock"))


def open_gui(h, s: dict, open_session: bool) -> dict:
    """Capabilities verified 2026-11-04 (Claude ext 2.1.318, Codex/ChatGPT ext 26.1021):
    (1) project folder: supported. (2) read-only history view: Claude NO (opening a session
    spawns `claude --resume`); Codex only as a fallback when the CLI holds the writer lock.
    (3) attach to the live process: NO for both. See references/vscode-sessions.md."""
    out = {**session_identity(s), "agent_kind": s["agent_kind"], "state": s["herdr_status"],
           "cwd": None, "open_hints": open_hints(s), "capabilities": {}}
    proj = open_project(h, s)
    out["cwd"] = proj.get("cwd")
    # Hints stay documentation. open_project is the only process this function starts,
    # and only for the project folder. Resume and attach hints are not launched.
    out["capabilities"]["project_folder"] = proj["result"]
    sid = s["agent_session_id"]
    if s["agent_kind"] == "claude" and sid:
        live = claude_session_live(sid)
        out["session_live"] = live
        out["capabilities"]["read_only_history"] = "unsupported — the Claude extension resumes sessions (spawns `claude --resume`)"
        out["capabilities"]["attach_live"] = "unsupported — no attach path in the extension"
        if open_session and live:
            out["capabilities"]["open_session"] = ("refused — session is live in Herdr; the extension would offer "
                                                   "'Open here anyway', which creates a second writer")
        elif open_session:
            uri = f"vscode://anthropic.claude-code/open?session={sid}"
            r = launch(["open", uri])
            out["capabilities"]["open_session"] = ("resumed in VS Code (Operator-approved): " if r["launched"] else "dry-run: ") + uri
        else:
            out["capabilities"]["open_session"] = ("not attempted — would RESUME the session in VS Code; "
                                                   "re-run with --open-session only if that is intended" + (" (blocked while live)" if live else ""))
    elif s["agent_kind"] == "codex" and sid:
        live = codex_session_live(sid)
        out["session_live"] = live
        out["capabilities"]["read_only_history"] = ("available as fallback — CLI holds the writer lock, the extension shows history read-only"
                                                    if live else "unsupported while idle — opening would make VS Code the writer (resume)")
        out["capabilities"]["attach_live"] = "unsupported — writer lock is exclusive"
        if open_session:
            uri = f"vscode://openai.chatgpt/local/{sid}"
            r = launch(["open", uri])
            out["capabilities"]["open_session"] = ("opened in VS Code (Operator-approved" + ("; read-only while the CLI is live" if live else "; VS Code becomes the writer")
                                                   + "): " if r["launched"] else "dry-run: ") + uri
        else:
            out["capabilities"]["open_session"] = "not attempted — re-run with --open-session to open it in the Codex extension"
    else:
        out["capabilities"]["read_only_history"] = "unsupported — no recognized Claude/Codex session in this pane"
        out["capabilities"]["attach_live"] = "unsupported"
    return out


def render_gui(r: dict) -> str:
    c = r["capabilities"]
    sid = r.get("agent_session_id") or "—"
    lines = [f"**open-gui `{r.get('pane_id') or r.get('pane')}`** — {r['agent_kind']} `{r.get('agent_name') or '—'}` "
             f"session `{sid}`, state {r['state'] or '—'}"
             + (f", live in Herdr" if r.get("session_live") else ""),
             format_identity(r),
             f"- Project folder: {c['project_folder']} → {r['cwd']}",
             f"- Read-only history view: {c['read_only_history']}",
             f"- Attach to running session: {c['attach_live']}"]
    if "open_session" in c:
        lines.append(f"- Open session in extension: {c['open_session']}")
    hints = r.get("open_hints") or {}
    shown = [(k, hints[k]) for k in ("herdr_focus", "resume_command", "resume_uri", "codex_thread_open", "herdr_attach") if hints.get(k)]
    if shown:
        lines.append("- Open hints (not run):")
        lines.extend(f"  - {k}: `{v}`" for k, v in shown)
    lines.append("The Herdr pane was not touched. Hints were not executed.")
    return "\n".join(lines)


def do_ack(h, target: dict, cache: "Cache") -> dict:
    """Record a human answer against the live fingerprint. Does not touch the pane."""
    if target.get("agent_kind") == "none":
        return {"result": "refused", "reason": "plain shells are not needs-me asks"}
    ev = evidence(h, target, TAIL_LINES, history=False)
    if not ev.get("fingerprint"):
        return {"result": "refused", "reason": "no pane evidence fingerprint to ack; re-read the pane first",
                **session_identity(target)}
    rec = cache.record_ack(target["session_key"], {
        "fingerprint": ev["fingerprint"],
        "pane_id": target["pane_id"],
        "tab_id": target["tab_id"],
        "workspace_id": target["workspace_id"],
        "terminal_id": target["terminal_id"],
        "agent_session_id": target["agent_session_id"],
        "agent_name": target["agent_name"],
        "cwd": target["cwd"],
    })
    return {"result": "acked", **session_identity(target), "human_acked_at": rec["human_acked_at"],
            "fingerprint": ev["fingerprint"], "open_hints": open_hints(target),
            "note": "Recorded only. Needs-me cards for this fingerprint are suppressed until the pane evidence changes. No keys were sent."}


# =========================================================================== cache

class Cache:
    """Machine-local cache. Steward: the Operator. Never holds raw terminal output or credentials.

    * Summaries / explicit renames are partitioned PER TRUST BOUNDARY (one store per boundary,
      never merged). Sessions whose boundary is 'unassigned' are never persisted.
    * Keys are the stable agent session identity (agent:<session id>), not pane numbers.
    * Snapshots, flags, and human acks hold metadata only (ids, states, content hashes, ack time).
    * Retention: entries/files older than HSM_RETENTION_DAYS (default 7) are purged on every
      run; `inventory.py purge` removes everything on demand.
    """

    def __init__(self, root: str | None = None, scope: str = "default"):
        self.base = root or os.environ.get("HSM_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "herdr-session-manager")
        self.root = os.path.join(self.base, re.sub(r"[^\w.\-]", "_", scope))
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.retention_days = float(os.environ.get("HSM_RETENTION_DAYS", "7"))
        self._expire()

    def _p(self, name):
        return os.path.join(self.root, name)

    def _load(self, name, default):
        try:
            with open(self._p(name)) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return default

    def _save(self, name, data):
        path = self._p(name)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
        return path

    def _expire(self):
        cutoff = _dt.datetime.now(_dt.timezone.utc).timestamp() - self.retention_days * 86400
        for name in os.listdir(self.root):
            path = self._p(name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                elif name.startswith(("summaries-", "renames-")) or name == "acks.json":
                    data = self._load(name, {})
                    stamp = "human_acked_at" if name == "acks.json" else "updated_at"
                    keep = {k: v for k, v in data.items() if _ts(v.get(stamp)) >= cutoff}
                    if len(keep) != len(data):
                        self._save(name, keep)
            except OSError:
                pass

    def purge(self, boundary: str | None = None, everything: bool = False) -> list[str]:
        removed = []
        roots = [os.path.join(self.base, d) for d in os.listdir(self.base)] if everything else [self.root]
        for r in roots:
            if not os.path.isdir(r):
                continue
            for name in os.listdir(r):
                if boundary and name not in (f"summaries-{boundary}.json", f"renames-{boundary}.json"):
                    continue
                os.remove(os.path.join(r, name))
                removed.append(os.path.join(r, name))
        return removed

    @staticmethod
    def _bfile(kind: str, boundary: str) -> str:
        return f"{kind}-{re.sub(r'[^a-z0-9_-]', '_', boundary.lower())}.json"

    def _bound_files(self, kind):
        return [n for n in os.listdir(self.root) if n.startswith(kind + "-") and n.endswith(".json")]

    def get_summary(self, key, boundary: str):
        return None if boundary == "unassigned" else self._load(self._bfile("summaries", boundary), {}).get(key)

    def put_summary(self, key, summary: dict, boundary: str):
        if boundary == "unassigned":
            raise HerdrError("refusing to persist a summary for a session with no assigned Trust Boundary "
                             "(add a rule to ~/.config/herdr-session-manager/boundaries.json)")
        allowed = {"goal", "completed", "current", "outstanding", "blocker", "next", "needs_me", "suggested_title", "fingerprint", "pane"}
        clean = {k: (redact(v)[:300] if isinstance(v, str) else v) for k, v in summary.items() if k in allowed}
        clean.update(updated_at=_now(), boundary=boundary)
        name = self._bfile("summaries", boundary)
        data = self._load(name, {})
        data[key] = clean
        self._save(name, data)
        return clean

    def renames(self) -> dict:
        out = {}
        for n in self._bound_files("renames"):
            out.update({k: v.get("label") for k, v in self._load(n, {}).items()})
        return out

    def record_rename(self, key, label, boundary):
        if boundary == "unassigned":
            return
        name = self._bfile("renames", boundary)
        data = self._load(name, {})
        data[key] = {"label": label, "updated_at": _now()}
        self._save(name, data)

    def own_terminals(self) -> set:
        return set(self._load("own-terminals.json", []))

    def record_own_terminal(self, terminal_id):
        if terminal_id:
            self._save("own-terminals.json", sorted(self.own_terminals() | {terminal_id})[-50:])

    def acks(self) -> dict:
        return self._load("acks.json", {})

    def get_ack(self, key):
        if not key:
            return None
        return self.acks().get(key)

    def record_ack(self, key, fields: dict) -> dict:
        """Remember that the human already answered this fingerprint. Metadata only."""
        if not key or not fields.get("fingerprint"):
            raise HerdrError("refusing to record a human ack without a session key and evidence fingerprint")
        keep = ("fingerprint", "pane_id", "tab_id", "workspace_id", "terminal_id", "agent_session_id", "agent_name", "cwd")
        rec = {k: fields.get(k) for k in keep}
        rec["session_key"] = key
        for k in ("agent_name", "cwd"):
            if isinstance(rec.get(k), str):
                rec[k] = redact(rec[k])[:300]
        rec["human_acked_at"] = _now()
        data = self.acks()
        data[key] = rec
        self._save("acks.json", data)
        return rec

    def flags(self) -> dict:
        return self._load("flags.json", {})

    def set_flag(self, key, on: bool):
        data = self.flags()
        if on:
            data[key] = {"updated_at": _now()}
        else:
            data.pop(key, None)
        self._save("flags.json", data)

    def load_snapshot(self, ws):
        return self._load(f"snapshot-{ws}.json", None)

    def save_snapshot(self, ws, data):
        return self._save(f"snapshot-{ws}.json", data)

    def save_baseline(self, data):
        return self._save(f"organize-baseline-{_now().replace(':', '').replace('-', '')}.json", data)


# =========================================================================== utils

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _ts(iso: str | None) -> float:
    try:
        return _dt.datetime.fromisoformat(iso).timestamp() if iso else 0.0
    except ValueError:
        return 0.0


def _hm(iso: str) -> str:
    try:
        return _dt.datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except Exception:
        return iso or "?"


def _minutes_between(a: str, b: str) -> int | None:
    try:
        return int((_dt.datetime.fromisoformat(b) - _dt.datetime.fromisoformat(a)).total_seconds() // 60)
    except Exception:
        return None


def _cell(text: str, n: int) -> str:
    text = (text or "").replace("|", "/").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _print(obj, as_json: bool, text: str | None = None):
    print(json.dumps(obj, indent=1, ensure_ascii=False) if as_json or text is None else text)


# =========================================================================== CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="inventory.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", help="run against a JSON fixture instead of the live Herdr server")
    ap.add_argument("--cache-dir", help="override cache root (default ~/.cache/herdr-session-manager)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, all_ok=False):
        p.add_argument("--workspace", help="workspace id (default: the caller's workspace)")
        if all_ok:
            p.add_argument("--all", dest="all_workspaces", action="store_true", help="include every workspace (explicit opt-in)")
        p.add_argument("--json", action="store_true")
        return p

    p = sub.add_parser("move-selected-space", help="preview/move an explicitly operator-selected right-hand pane")
    for flag in ("pane", "terminal", "right-of", "source-tab-label", "label", "expected-socket"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--operator-selected", action="store_true", required=True, help="only after explicit operator selection and fresh layout binding")
    p.add_argument("--apply", action="store_true", help="perform the explicitly requested move; default is preview")
    p.add_argument("--json", action="store_true")
    for name in ("reconcile-context", "move-self-space"):
        p = sub.add_parser(name, help="explicit Codex self-only identity reconciliation / move")
        p.add_argument("--expected-socket", required=True, help="independently confirmed endpoint comparison; never selects a server")
        p.add_argument("--native-client", action="store_true", help="require fresh process-verified native identity; never fall back to legacy metadata")
        p.add_argument("--json", action="store_true")
        if name == "move-self-space":
            p.add_argument("--label", required=True, help="new space and tab label (normalized to ALL CAPS)")
            p.add_argument("--apply", action="store_true", help="perform the explicitly requested move; default is preview")
    p = sub.add_parser("diagnose-context", help="bounded read-only caller diagnostic; no repair")
    p.add_argument("--json", action="store_true")
    p.add_argument("--expected-socket", help="trusted comparison only; never selects a server")
    p = common(sub.add_parser("titles", help="preview or apply ALL CAPS tab labels"), all_ok=True)
    p.add_argument("--apply", action="store_true")
    p = common(sub.add_parser("restore", help="find and restore selected sessions by date, repo or title"))
    p.add_argument("date", nargs="?", help="YYYY-MM-DD: last activity since this date")
    p.add_argument("--until", help="inclusive end date YYYY-MM-DD")
    p.add_argument("--timezone", default="UTC", help="IANA timezone for date boundaries (default UTC)")
    p.add_argument("--repo", help="repository path")
    p.add_argument("--title", help="case-insensitive title substring")
    p.add_argument("--select", nargs="+", help="exact kind:session-id values from candidate list")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--approve-for-me", action="store_true", help="Codex only: automatic approval review")
    p = common(sub.add_parser("recent", help="saved Codex/Claude sessions, newest first"))
    p.add_argument("--limit", type=int, default=30)
    p = common(sub.add_parser("resume", help="preview or resume selected saved sessions in named tabs"))
    p.add_argument("targets", nargs="+", help="exact kind:session-id values from recent")
    p.add_argument("--apply", action="store_true", help="start the explicitly selected sessions")
    common(sub.add_parser("context"))
    common(sub.add_parser("list", help="L1 compact inventory"), all_ok=True)
    p = common(sub.add_parser("organize", help="L1 one pane per tab, verified"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sync-titles", action="store_true", help="also retitle meaningful tab labels to the session title (original script behaviour)")
    p = common(sub.add_parser("rename", help="rename the tab title of one pane's tab"))
    p.add_argument("target"); p.add_argument("title")
    p.add_argument("--shared", action="store_true", help="allow renaming a tab shared by several panes")
    p.add_argument("--if-questionable", action="store_true", help="refuse unless the current title is questionable (used by review --unknown --apply)")
    for name in ("flag", "unflag"):
        p = common(sub.add_parser(name, help=f"{name} a session title for review --unknown")); p.add_argument("target")
    p = common(sub.add_parser("review", help="L2 evidence for selected sessions only"))
    p.add_argument("targets", nargs="*", help="pane ids (p4, w6:p4), names, or unique partial names")
    p.add_argument("--unknown", "--unnamed", dest="unknown", action="store_true", help="only sessions whose titles are unclear (+ manual flags)")
    p.add_argument("--flag", action="append", default=[], help="also include this session (manual flag, this run only)")
    p.add_argument("--apply", action="store_true", help="with --unknown: the agent may rename ONLY the identified sessions (via rename --if-questionable)")
    p = common(sub.add_parser("remaining", help="L2 outstanding-work packet"), all_ok=True)
    p.add_argument("--max-inspect", type=int, default=6); p.add_argument("--render", action="store_true", help="deterministic draft instead of JSON")
    common(sub.add_parser("status", help="L2 concise status"), all_ok=True)
    p = common(sub.add_parser("watch", help="L2 one monitoring pass"), all_ok=True)
    p.add_argument("--stale-minutes", type=int, default=int(os.environ.get("HSM_STALE_MINUTES", "45")))
    p = common(sub.add_parser("remember", help="cache an agent-written summary")); p.add_argument("target")
    p.add_argument("--summary", required=True, help='JSON: {"goal","completed","current","outstanding","blocker","next","needs_me","suggested_title","human_acked"}')
    p = common(sub.add_parser("ack", help="record that the human already answered this pane's current needs-me ask"))
    p.add_argument("target")
    p = common(sub.add_parser("open-project", help="open a pane's working directory in VS Code"))
    p.add_argument("target", nargs="?", help="pane id or name (default: this pane)")
    p = common(sub.add_parser("files", help="Yazi file browser in a Files tab of this workspace"))
    p.add_argument("--persistent", action="store_true", help="create or reuse ONE Files tab per workspace")
    p.add_argument("--root", help="directory to browse (default: this pane's verified cwd)")
    p.add_argument("--no-focus", action="store_true", help="open without switching to the Files tab")
    p = common(sub.add_parser("open-gui", help="open a pane's project + report GUI session capabilities"))
    p.add_argument("target")
    p.add_argument("--open-session", action="store_true", help="Operator-approved: hand the session to the VS Code extension (resumes it; refused for live Claude sessions)")
    p = sub.add_parser("purge", help="delete cached data (no Herdr context needed)")
    p.add_argument("--boundary"); p.add_argument("--everything", action="store_true"); p.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "move-selected-space":
        from self_space import IdentityError
        from selected_space import move_selected
        if a.fixture:
            print(json.dumps({"verified": False, "error": "operator selection does not load inventory fixtures"}))
            return EXIT_NO_CONTEXT
        try:
            result = move_selected(LiveHerdr(), a.pane, a.terminal, a.right_of,
                                   a.source_tab_label, a.label, a.expected_socket,
                                   a.operator_selected, a.apply)
            print(json.dumps(result))
            return EXIT_OK if result["verified"] else EXIT_ERR
        except (IdentityError, HerdrError, NoContext, KeyError, TypeError, AttributeError) as e:
            print(json.dumps({"verified": False, "error": str(e) if isinstance(e, IdentityError) else "selected-pane metadata unavailable; no automatic retry",
                              "error_code": getattr(e, "code", "metadata_unavailable"), "stage": getattr(e, "stage", None)}))
            return EXIT_NO_CONTEXT
    if a.cmd in ("reconcile-context", "move-self-space"):
        from self_space import IdentityError, resolve_self, move_self
        if a.fixture:
            print(json.dumps({"verified": False, "error": "self reconciliation does not load inventory fixtures"}))
            return EXIT_NO_CONTEXT
        try:
            h = LiveHerdr()
            extra = {"native_client": True} if a.native_client else {}
            result = (resolve_self(h, a.expected_socket, **extra) if a.cmd == "reconcile-context"
                      else move_self(h, a.label, a.expected_socket, a.apply, **extra))
            print(json.dumps(result))
            return EXIT_OK if result["verified"] else EXIT_ERR
        except (IdentityError, HerdrError, NoContext, KeyError, TypeError, AttributeError) as e:
            print(json.dumps({"verified": False, "error": str(e) if isinstance(e, IdentityError) else "identity metadata unavailable; no automatic retry", "error_code": getattr(e, "code", "metadata_unavailable"), "stage": getattr(e, "stage", None), "details": getattr(e, "details", None)}))
            return EXIT_NO_CONTEXT
    if a.cmd == "diagnose-context":
        from context_diagnostic import diagnose
        if a.fixture:
            print(json.dumps({"finding": "unavailable", "verified": False, "reason": "diagnostic does not load inventory fixtures"}))
            return EXIT_NO_CONTEXT
        result = diagnose(expected_socket=a.expected_socket)
        print(json.dumps(result))
        return EXIT_OK if result["verified"] else EXIT_NO_CONTEXT
    h = FixtureHerdr(a.fixture) if a.fixture else LiveHerdr()
    if a.cmd in ("recent", "resume", "restore", "titles"):
        import recovery
        try:
            try:
                ctx = verify_context(h, a.workspace, getattr(a, "all_workspaces", False))
            except NoContext:
                if a.cmd in ("resume", "titles") or getattr(a, "apply", False) or getattr(a, "select", None):
                    raise
                ctx = None
            if a.cmd == "titles":
                from titles import uppercase_tabs
                _print(uppercase_tabs(h, ctx, a.apply), True)
                return EXIT_OK
            if a.cmd == "restore":
                result = recovery.restore(h, ctx, a.date, a.until, a.timezone, a.repo, a.title,
                    a.select, a.apply, a.approve_for_me, bool(a.workspace))
                _print(result, True)
                return EXIT_ERR if result['result'] == 'incomplete' else EXIT_OK
            if a.cmd == "recent":
                rows, warnings = recovery.recent(h, ctx)
                rows = rows[:max(1, a.limit)]
                _print({"sessions": rows, "warnings": warnings}, a.json, recovery.render(rows, warnings))
                return EXIT_OK
            result = recovery.resume(h, ctx, a.targets, a.apply)
            _print(result, True)
            return EXIT_ERR if result["result"] == "incomplete" else EXIT_OK
        except (HerdrError, ValueError, OSError) as e:
            print(f"Recovery unavailable: {e}", file=sys.stderr)
            return EXIT_ERR
    sock = h.env.get("HERDR_SOCKET_PATH", "")
    cache = Cache(a.cache_dir, "fixture" if a.fixture else "herdr-" + hashlib.sha1(sock.encode()).hexdigest()[:10])
    if a.cmd == "purge":
        _print({"removed": cache.purge(a.boundary, a.everything)}, True)
        return EXIT_OK
    try:
        ctx = verify_context(h, a.workspace, getattr(a, "all_workspaces", False))
    except NoContext as e:
        if a.json:
            print(json.dumps({"schema": SCHEMA, "kind": "error", "error": "no_verified_herdr_context", "message": str(e)}))
        else:
            print(f"Herdr — unavailable: {e}", file=sys.stderr)
        return EXIT_NO_CONTEXT
    try:
        if a.cmd == "context":
            _print({k: ctx[k] for k in ("self_pane", "self_workspace", "workspace_id", "workspace_label")}, True)
            return EXIT_OK
        inv = inventory_l1(h, ctx, cache)
        if a.cmd == "list":
            _print(inv, a.json, render_list(inv))
        elif a.cmd == "organize":
            plan = plan_organize(inv, a.sync_titles)
            if a.dry_run or plan["noop"]:
                _print(plan, a.json, render_organize(inv, plan, None))
                return EXIT_OK
            res = apply_organize(h, ctx, plan, cache, a.sync_titles)
            inv2 = inventory_l1(h, ctx, cache)
            again = plan_organize(inv2, a.sync_titles)
            res["checks"]["idempotent"] = again["noop"]
            _print({"plan": plan, "result": res, "replan": again}, a.json, render_organize(inv2, plan, res, again["noop"]))
            c = res["checks"]
            if res["failed"] or not all(c[k] for k in ("workspace_isolation", "pane_count_unchanged", "process_continuity")):
                return EXIT_ERR
        elif a.cmd == "open-project":
            found, errs = resolve_targets(inv, [a.target or ctx["self_pane"]])
            if errs:
                _print({"error": errs[0]}, a.json, errs[0])
                return EXIT_USAGE
            r = open_project(h, found[0])
            text = {"opened": f"Opened {r['cwd']} in VS Code (pane {r['pane']} untouched; project folder only, not the agent session).",
                    "dry-run": f"Would run: {' '.join(repr(x) for x in r.get('argv', []))}",
                    "unavailable": f"Not opened: {r.get('reason')}"}[r["result"]]
            _print(r, a.json, text)
            return EXIT_OK if r["result"] != "unavailable" else EXIT_ERR
        elif a.cmd == "files":
            r = files(h, ctx, inv, a.root, a.persistent, focus=not a.no_focus, cache=cache)
            _print(r, a.json, render_files(r))
            return EXIT_OK if r["result"] in ("opened", "reused", "restarted", "started-unverified") else EXIT_ERR
        elif a.cmd == "open-gui":
            found, errs = resolve_targets(inv, [a.target])
            if errs:
                _print({"error": errs[0]}, a.json, errs[0])
                return EXIT_USAGE
            r = open_gui(h, found[0], a.open_session)
            _print(r, a.json, render_gui(r))
        elif a.cmd in ("rename", "flag", "unflag", "remember", "ack"):
            found, errs = resolve_targets(inv, [a.target])
            if errs:
                _print({"error": errs[0]}, a.json, errs[0])
                return EXIT_USAGE
            t = found[0]
            if a.cmd == "rename":
                r = do_rename(h, inv, t, a.title, cache, a.shared, a.if_questionable)
                text = (f"Renamed tab {short_id(r['tab'])} \"{r['from']}\" → \"{r['to']}\" (pane {r['pane']}; session untouched)."
                        if r["result"] == "renamed" else f"Not renamed: {r.get('reason') or r['result']}")
                _print(r, a.json, text)
                return EXIT_OK if r["result"] == "renamed" else EXIT_ERR
            if a.cmd in ("flag", "unflag"):
                cache.set_flag(t["session_key"], a.cmd == "flag")
                _print({"pane": t["pane"], "flagged": a.cmd == "flag"}, a.json, f"{t['pane']} {'flagged for' if a.cmd == 'flag' else 'removed from'} review --unknown.")
            elif a.cmd == "ack":
                r = do_ack(h, t, cache)
                text = (f"Recorded human ack for `{r.get('pane_id')}` session `{r.get('agent_session_id') or '—'}` "
                        f"at {r.get('human_acked_at')}. Needs-me cards for this fingerprint are suppressed until the pane evidence changes. "
                        "Nothing was sent to the pane."
                        if r["result"] == "acked" else f"Not acked: {r.get('reason')}")
                _print(r, a.json, text)
                return EXIT_OK if r["result"] == "acked" else EXIT_ERR
            else:
                ev = evidence(h, t, TAIL_LINES, history=False)
                summary = json.loads(a.summary)
                if not isinstance(summary, dict):
                    raise HerdrError("summary must be a JSON object")
                human_acked = summary.pop("human_acked", False) is True
                summary.update(fingerprint=ev["fingerprint"], pane=t["pane"])
                saved = cache.put_summary(t["session_key"], summary, t["boundary"])
                if human_acked:
                    if not ev.get("fingerprint"):
                        raise HerdrError("summary cached, but a human ack needs a pane evidence fingerprint")
                    saved = dict(saved)
                    saved["human_ack"] = cache.record_ack(t["session_key"], {
                        "fingerprint": ev["fingerprint"], "pane_id": t["pane_id"], "tab_id": t["tab_id"],
                        "workspace_id": t["workspace_id"], "terminal_id": t["terminal_id"],
                        "agent_session_id": t["agent_session_id"], "agent_name": t["agent_name"], "cwd": t["cwd"]})
                _print(saved, True)
        elif a.cmd == "review":
            if a.unknown:
                extra, errs = resolve_targets(inv, a.flag + a.targets)
                targets = [s for w in inv["workspaces"] for s in w["sessions"] if s["questionable"] and not s["is_self"]]
                targets += [s for s in extra if s not in targets]
                if errs:
                    _print({"errors": errs}, a.json, "\n".join(errs))
                    return EXIT_USAGE
                if not targets:
                    _print({"schema": SCHEMA, "kind": "review", "mode": "unknown", "sessions": [], "message": NO_REVIEWS}, a.json, NO_REVIEWS)
                    return EXIT_OK
                pk = review_packet(h, inv, targets, "unknown" + ("+apply" if a.apply else ""))
            else:
                if not a.targets:
                    print("review needs pane ids / names, or --unknown", file=sys.stderr)
                    return EXIT_USAGE
                targets, errs = resolve_targets(inv, a.targets)
                if errs:
                    _print({"errors": errs}, a.json, "\n".join(errs))
                    return EXIT_USAGE
                pk = review_packet(h, inv, targets, "selected")
            _print(pk, True)
        elif a.cmd == "remaining":
            pk = remaining_packet(h, inv, a.max_inspect)
            _print(pk, a.json or not a.render, render_remaining(pk) if a.render else None)
        elif a.cmd == "status":
            evs = {s["pane_id"]: evidence(h, s, TAIL_LINES, history=False) for w in inv["workspaces"] for s in w["sessions"]}
            _print({"schema": SCHEMA, "kind": "status", "card_gate_rule": CARD_GATE_RULE, "inventory": inv, "evidence": evs},
                   a.json, render_status(inv, evs))
        elif a.cmd == "watch":
            res = watch(h, inv, cache, a.stale_minutes)
            _print(res, a.json, render_watch(res, inv))
    except HerdrError as e:
        msg = {"schema": SCHEMA, "kind": "error", "error": "herdr_error", "message": str(e)}
        print(json.dumps(msg) if getattr(a, "json", False) else f"Herdr error: {e}", file=sys.stderr)
        return EXIT_ERR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
