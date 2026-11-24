"""Explicit self-only reconciliation and move. No environment rebinding or cache."""
from __future__ import annotations

import re
import subprocess


class IdentityError(ValueError):
    def __init__(self, message, code="identity_unverified", stage=None, details=None):
        super().__init__(message)
        self.code, self.stage, self.details = code, stage, details
        self.payload = {"error": {"code": code}}


def _call(h, *args):
    try:
        return h.call(*args)
    except Exception as e:
        payload = getattr(e, "payload", None)
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(e, (PermissionError,)) or re.search(r"permission denied|operation not permitted|access denied", str(e), re.I):
            code = "access_denied"
        elif isinstance(e, (TimeoutError, subprocess.TimeoutExpired)):
            code = "timeout"
        if code not in ("access_denied", "permission_denied", "pane_not_found", "timeout"):
            code = "unavailable"
        if code == "permission_denied":
            code = "access_denied"
        stage = "/".join(args[:2])
        raise IdentityError(f"Herdr metadata {code} at {stage}", code, stage) from e



def _id(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _pane(p):
    if not isinstance(p, dict):
        raise IdentityError("malformed pane metadata")
    for key, pattern in (("pane_id", r"w[0-9A-Za-z]+:p[0-9A-Za-z]+"),
                         ("tab_id", r"w[0-9A-Za-z]+:t[0-9A-Za-z]+"),
                         ("workspace_id", r"w[0-9A-Za-z]+")):
        if not _id(p.get(key), pattern):
            raise IdentityError("invalid pane topology")
    if any(p[k].split(":")[0] != p["workspace_id"] for k in ("pane_id", "tab_id")):
        raise IdentityError("inconsistent pane topology")
    # Terminal handles are opaque. Do not require a particular term/term_ prefix.
    if not _id(p.get("terminal_id"), r"[0-9A-Za-z][0-9A-Za-z_.:-]{0,199}") or not isinstance(p.get("cwd"), str) or not p["cwd"].startswith("/"):
        raise IdentityError("missing terminal or cwd metadata")
    return p


def _session(p):
    s = p.get("agent_session")
    if not isinstance(s, dict) or p.get("agent") != "codex" or s.get("agent") != "codex" or s.get("kind") != "id" or s.get("source") != "herdr:codex":
        return None
    return s.get("value")


def _identity(p):
    return {k: p[k] for k in ("pane_id", "tab_id", "workspace_id", "terminal_id", "cwd")} | {"agent_session_id": _session(p)}


def _status(h, expected_socket):
    # No endpoint selector is ever passed to Herdr. Compare the inherited server.
    body = _call(h, "status", "--json")
    s = body.get("server")
    if not isinstance(s, dict) or any(s.get(k) is not True for k in ("running", "compatible", "endpoint_compatible")):
        raise IdentityError("server unavailable, incompatible or malformed")
    socket = s.get("socket")
    if not isinstance(socket, str) or not socket:
        raise IdentityError("missing server endpoint")
    if expected_socket is not None and socket != expected_socket:
        raise IdentityError("independently confirmed endpoint does not match")
    return socket


def resolve_self(h, expected_socket=None, native_client=False):
    """Resolve only this host-provided Codex conversation, on the inherited server.

    Caller supplies an independently confirmed endpoint for the pane-not-found
    exception. This function does not derive that expectation from environment.
    """
    if native_client:
        from native_identity import resolve
        return resolve(h, expected_socket)
    if h.env.get("HERDR_ENV") != "1":
        raise IdentityError("not a Herdr-managed context")
    thread = h.env.get("CODEX_THREAD_ID")
    if not _id(thread, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"):
        raise IdentityError("trusted host CODEX_THREAD_ID unavailable; no identity inference allowed")
    endpoint = _status(h, expected_socket)
    reconciled = False
    try:
        body = _call(h, "pane", "current", "--current")
    except Exception as e:
        # Only the structured, exact stale-caller error admits the exception.
        payload = getattr(e, "payload", None)
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict) or payload["error"].get("code") != "pane_not_found":
            if isinstance(e, IdentityError):
                raise
            raise IdentityError("caller verification failed; reconciliation not admitted") from e
        if expected_socket is None:
            raise IdentityError("pane not found; independently confirmed --expected-socket required") from e
        reconciled = True
        body = None
    panes = _call(h, "pane", "list").get("result", {})
    if panes.get("type") != "pane_list" or not isinstance(panes.get("panes"), list):
        raise IdentityError("malformed pane inventory")
    matches = [p for p in panes["panes"] if isinstance(p, dict) and _session(p) == thread]
    if len(matches) != 1:
        raise IdentityError(f"current conversation matched {len(matches)} live panes; exactly one required", "exact_match_count", "pane/list", {"exact_matches": len(matches), "panes_without_session": sum(isinstance(p, dict) and _session(p) is None for p in panes["panes"])})
    match = _pane(matches[0])
    if body is not None:
        result = body.get("result", {})
        if result.get("type") != "pane_current":
            raise IdentityError("malformed current-pane response")
        current = _pane(result.get("pane"))
        if _session(current) != thread or _identity(current) != _identity(match):
            raise IdentityError("current caller and exact conversation mapping disagree")
    # Fresh direct metadata must confirm the inventory match; never rewrite env IDs.
    direct = _pane(_call(h, "pane", "get", match["pane_id"]).get("result", {}).get("pane"))
    if _identity(direct) != _identity(match):
        raise IdentityError("pane identity changed during reconciliation")
    if _status(h, expected_socket) != endpoint:
        raise IdentityError("server endpoint changed during reconciliation")
    return {"schema": "herdr-self-space/v1", "verified": True,
            "method": "exact_conversation_reconciliation" if reconciled else "current_pane",
            "identity": _identity(direct), "repaired": False}


def move_self(h, label, expected_socket=None, apply=False, native_client=False):
    if not isinstance(label, str) or not label.strip() or len(label) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise IdentityError("provide a nonempty space label of at most 100 printable characters")
    label = label.strip().upper()
    extra = {"native_client": True} if native_client else {}
    before = resolve_self(h, expected_socket, **extra)
    out = {**before, "kind": "self-space-move", "applied": False, "label": label}
    if not apply:
        return out
    # resolve_self just reverified this exact process for this one move. No general
    # context capability, cache record or reusable admission token is emitted.
    old = before["identity"]
    if native_client:
        from native_identity import snapshot, verify_move
        from selected_space import _structure
        native_before = snapshot(h, old)
        final_binding = resolve_self(h, expected_socket, **extra)
        if final_binding["identity"] != old or final_binding["native_client_id"] != before["native_client_id"]:
            raise IdentityError("native client changed before move dispatch")
        native_final = snapshot(h, old)
        if _structure(native_final[0]) != _structure(native_before[0]) or native_final[1] != native_before[1]:
            raise IdentityError("native process or topology changed before move dispatch")
    # Last metadata call before dispatch: catch replacement after reconciliation.
    # Herdr exposes no expected-session compare-and-move flag. Its documented
    # nonreused pane IDs prevent retargeting a closed handle, but cannot atomically
    # exclude another agent replacing the occupant of the same still-open pane.
    final = _pane(_call(h, "pane", "get", old["pane_id"]).get("result", {}).get("pane"))
    final_identity = _identity(final)
    if native_client:
        final_identity["agent_session_id"] = old["agent_session_id"]
    if final_identity != old:
        raise IdentityError("pane occupant changed before move dispatch; nothing dispatched")
    if native_client:
        dispatch_binding = resolve_self(h, expected_socket, **extra)
        if dispatch_binding["identity"] != old or dispatch_binding["native_client_id"] != before["native_client_id"]:
            raise IdentityError("native client changed at move dispatch; nothing dispatched")
    try:
        if native_client:
            receipt = h.native_move(_status(h, expected_socket), old["agent_session_id"],
                                    before["native_client_id"], old["terminal_id"], label)
        else:
            receipt = _call(h, "pane", "move", old["pane_id"], "--new-workspace", "--label", label,
                             "--tab-label", label, "--no-focus").get("result", {}).get("move_result")
    except Exception as e:
        raise IdentityError("move outcome unknown; inspect metadata before any retry") from e
    out.update(applied=True, verified=False)
    try:
        if not isinstance(receipt, dict) or receipt.get("changed") is not True:
            raise IdentityError("move not confirmed")
        after = _pane(receipt.get("pane"))
        new = _identity(after)
        if native_client:
            new["agent_session_id"] = old["agent_session_id"]
        if any(new[k] != old[k] for k in ("terminal_id", "agent_session_id", "cwd")):
            raise IdentityError("terminal, conversation or cwd changed")
        if new["workspace_id"] == old["workspace_id"]:
            raise IdentityError("workspace did not change")
        for key in ("pane_id", "tab_id", "workspace_id"):
            if receipt.get("previous_" + key) != old[key]:
                raise IdentityError("source identity differs from verified selection")
        ws, tab = receipt.get("created_workspace"), receipt.get("created_tab")
        if not isinstance(ws, dict) or not isinstance(tab, dict) or ws.get("pane_count") != 1 or ws.get("tab_count") != 1 or tab.get("pane_count") != 1 or ws.get("workspace_id") != new["workspace_id"] or tab.get("workspace_id") != new["workspace_id"] or tab.get("tab_id") != new["tab_id"]:
            raise IdentityError("single-pane destination not confirmed")
        # Recheck exact-session uniqueness and terminal continuity after the move.
        confirmed = resolve_self(h, expected_socket, **extra)
        if confirmed["identity"] != new or (native_client and confirmed["native_client_id"] != before["native_client_id"]):
            raise IdentityError("live identity differs from move receipt")
        live_panes = _call(h, "pane", "list", "--workspace", new["workspace_id"]).get("result", {}).get("panes")
        live_tabs = _call(h, "tab", "list", "--workspace", new["workspace_id"]).get("result", {}).get("tabs")
        destination = _identity(_pane(live_panes[0])) if isinstance(live_panes, list) and len(live_panes) == 1 else None
        if native_client and destination is not None:
            destination["agent_session_id"] = old["agent_session_id"]
        if destination != new or not isinstance(live_tabs, list) or len(live_tabs) != 1 or live_tabs[0].get("tab_id") != new["tab_id"] or live_tabs[0].get("pane_count") != 1:
            raise IdentityError("live destination is not a single-tab, single-pane space")
        if native_client:
            out["checks"] = verify_move(h, native_before, old, new, label, receipt)
        out.update(verified=True, identity=new, previous_identity=old)
    except Exception as e:
        out["error"] = str(e) if isinstance(e, IdentityError) else "post-move metadata unavailable"
        out["next_action"] = "Do not retry or roll back automatically; reconcile live metadata."
    return out
