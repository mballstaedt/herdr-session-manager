"""Operator-selected two-pane layout move; never automatic conversation identity."""
from __future__ import annotations

from self_space import IdentityError, _call, _identity, _pane, _session, _status


def _rows(h, group, key, *args):
    body = _call(h, group, "list", *args).get("result", {})
    rows = body.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise IdentityError("malformed topology metadata")
    return rows


def _unique(rows, key):
    values = [r.get(key) for r in rows]
    if any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
        raise IdentityError("missing or duplicate topology handles")
    return {r[key]: r for r in rows}


def _snapshot(h):
    # Enumerate each server-returned workspace explicitly. Never assume that an
    # unscoped pane/tab list covers the whole server on every CLI version.
    workspaces = _unique(_rows(h, "workspace", "workspaces"), "workspace_id")
    panes, tabs = {}, {}
    for wid, workspace in workspaces.items():
        ps = _unique(_rows(h, "pane", "panes", "--workspace", wid), "pane_id")
        ts = _unique(_rows(h, "tab", "tabs", "--workspace", wid), "tab_id")
        if workspace.get("pane_count") != len(ps) or workspace.get("tab_count") != len(ts):
            raise IdentityError("workspace coverage/counts changed or unavailable")
        if any(p.get("workspace_id") != wid or p.get("tab_id") not in ts or not isinstance(p.get("terminal_id"), str) for p in ps.values()):
            raise IdentityError("inconsistent workspace pane membership")
        if any(t.get("workspace_id") != wid or t.get("pane_count") != sum(p["tab_id"] == tid for p in ps.values()) for tid, t in ts.items()):
            raise IdentityError("inconsistent workspace tab membership")
        if panes.keys() & ps.keys() or tabs.keys() & ts.keys():
            raise IdentityError("duplicate cross-workspace topology handles")
        panes.update(ps); tabs.update(ts)
    if set(_unique(_rows(h, "workspace", "workspaces"), "workspace_id")) != set(workspaces):
        raise IdentityError("workspace membership changed during snapshot")
    return {"workspaces": workspaces, "panes": panes, "tabs": tabs}


def _pane_sig(p):
    return tuple(p.get(k) for k in ("pane_id", "terminal_id", "tab_id", "workspace_id"))


def _tab_sig(t):
    return tuple(t.get(k) for k in ("tab_id", "workspace_id", "label", "pane_count"))


def _workspace_sig(w):
    return tuple(w.get(k) for k in ("workspace_id", "label", "pane_count", "tab_count"))


def _structure(snapshot):
    return (sorted(_pane_sig(p) for p in snapshot["panes"].values()),
            sorted(_tab_sig(t) for t in snapshot["tabs"].values()),
            sorted(_workspace_sig(w) for w in snapshot["workspaces"].values()))


def _processes(h, pane_id):
    info = _call(h, "pane", "process-info", "--pane", pane_id).get("result", {}).get("process_info")
    if not isinstance(info, dict):
        raise IdentityError("process PID metadata unavailable")
    shell = info.get("shell_pid")
    foreground = info.get("foreground_processes")
    if type(shell) is not int or shell <= 0 or not isinstance(foreground, list):
        raise IdentityError("process PID metadata unavailable")
    pids = sorted({p["pid"] for p in foreground if isinstance(p, dict) and type(p.get("pid")) is int and p["pid"] > 0})
    if not pids:
        raise IdentityError("foreground PID metadata unavailable")
    # argv/env are never inspected, returned, cached or persisted.
    return {"shell_pid": shell, "foreground_pids": pids}


def _pair(h, snapshot, selected, terminal, right_of, source_label):
    p = _pane(snapshot["panes"].get(selected))
    neighbor = snapshot["panes"].get(right_of)
    if p["terminal_id"] != terminal or not isinstance(neighbor, dict) or neighbor.get("tab_id") != p["tab_id"]:
        raise IdentityError("operator-selected terminal or neighbor binding changed")
    tab = snapshot["tabs"][p["tab_id"]]
    if tab.get("label") != source_label or tab.get("pane_count") != 2:
        raise IdentityError("operator-selected source tab changed")
    body = _call(h, "pane", "layout", "--pane", selected).get("result", {})
    layout = body.get("layout") or body.get("pane_layout") or body
    leaves = layout.get("panes") if isinstance(layout, dict) else None
    if not isinstance(leaves, list) or len(leaves) != 2 or {x.get("pane_id") for x in leaves} != {selected, right_of}:
        raise IdentityError("operator-selected two-leaf topology changed")
    try:
        left, right = sorted(leaves, key=lambda leaf: leaf["rect"]["x"])
        a, b = left["rect"], right["rect"]
        valid = (left["pane_id"] == right_of and right["pane_id"] == selected
                 and a["x"] + a["width"] <= b["x"] and a["y"] == b["y"] and a["height"] == b["height"])
    except (TypeError, KeyError):
        valid = False
    if not valid:
        raise IdentityError("operator-selected right-hand position changed")
    direct = _pane(_call(h, "pane", "get", selected).get("result", {}).get("pane"))
    if _identity(direct) != _identity(p):
        raise IdentityError("selected pane changed during direct verification")
    return p, neighbor


def move_selected(h, pane_id, terminal_id, right_of, source_label, label,
                  expected_socket, operator_selected=False, apply=False):
    if h.env.get("HERDR_ENV") != "1" or not operator_selected or not expected_socket:
        raise IdentityError("explicit operator selection and independently confirmed endpoint required")
    if not isinstance(label, str) or not label.strip() or len(label) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise IdentityError("provide a nonempty printable space label of at most 100 characters")
    label = label.strip().upper()
    _status(h, expected_socket)
    before = _snapshot(h)
    p, neighbor = _pair(h, before, pane_id, terminal_id, right_of, source_label)
    process_before = _processes(h, pane_id)
    session_before = _session(p)
    out = {"schema": "herdr-operator-selected-move/v1", "identity_method": "explicit_operator_selection_plus_fresh_layout",
           "automatic_self_identity": False, "identity": _identity(p),
           "reported_session_status": "available" if session_before else "unavailable",
           "preserved_neighbor": {"pane_id": neighbor["pane_id"], "terminal_id": neighbor["terminal_id"]},
           "label": label, "applied": False, "verified": True, "move_attempted": False,
           "workspace_coverage": len(before["workspaces"]), "process_before": process_before,
           "focus": "--no-focus requested on apply; native focus is not independently verified"}
    if not apply:
        return out
    # Refresh all topology plus the selected process and geometry before the only
    # dispatch. Explicit checks remain active under python -O.
    _status(h, expected_socket)
    final = _snapshot(h)
    if _structure(final) != _structure(before) or _processes(h, pane_id) != process_before:
        raise IdentityError("topology or selected process changed before dispatch")
    final_p, final_neighbor = _pair(h, final, pane_id, terminal_id, right_of, source_label)
    if _identity(final_p) != _identity(p) or _pane_sig(final_neighbor) != _pane_sig(neighbor):
        raise IdentityError("operator binding changed before dispatch")
    out.update(move_attempted=True, verified=False, applied=None)
    try:
        receipt = _call(h, "pane", "move", pane_id, "--new-workspace", "--label", label,
                        "--tab-label", label, "--no-focus").get("result", {}).get("move_result")
    except Exception:
        return {**out, "error": "move outcome unknown; inspect metadata, do not retry"}
    try:
        if not isinstance(receipt, dict) or receipt.get("changed") is not True:
            raise IdentityError("move receipt unavailable or unchanged")
        moved = _pane(receipt.get("pane"))
        after = _snapshot(h)
        live = _pane(after["panes"].get(moved["pane_id"]))
        new_wid, new_tid = live["workspace_id"], live["tab_id"]
        dest, dest_tab = after["workspaces"][new_wid], after["tabs"][new_tid]
        source_tab = after["tabs"].get(p["tab_id"], {})
        after_process = _processes(h, live["pane_id"])
        _status(h, expected_socket)
        source_wid = p["workspace_id"]
        expected_workspaces = {wid: _workspace_sig(w) for wid, w in before["workspaces"].items()}
        expected_source = dict(before["workspaces"][source_wid]); expected_source["pane_count"] -= 1
        expected_workspaces[source_wid] = _workspace_sig(expected_source)
        checks = {
            "source_binding": all(receipt.get("previous_" + k) == p[k] for k in ("pane_id", "tab_id", "workspace_id")),
            "terminal_and_cwd_unchanged": moved["terminal_id"] == live["terminal_id"] == terminal_id and moved["cwd"] == live["cwd"] == p["cwd"],
            "selected_processes_unchanged": after_process == process_before,
            "other_panes_topology_unchanged": sorted(_pane_sig(x) for key, x in before["panes"].items() if key != pane_id) == sorted(_pane_sig(x) for key, x in after["panes"].items() if key != live["pane_id"]),
            "other_tabs_unchanged": {key: _tab_sig(t) for key, t in before["tabs"].items() if key != p["tab_id"]} == {key: _tab_sig(t) for key, t in after["tabs"].items() if key not in (p["tab_id"], new_tid)},
            "source_tab_label_and_count": source_tab.get("label") == source_label and source_tab.get("pane_count") == 1,
            "neighbor_topology_unchanged": right_of in after["panes"] and _pane_sig(after["panes"][right_of]) == _pane_sig(neighbor),
            "existing_workspaces_preserved": {wid: _workspace_sig(w) for wid, w in after["workspaces"].items() if wid != new_wid} == expected_workspaces,
            "destination_is_new": new_wid not in before["workspaces"] and new_tid not in before["tabs"],
            "destination_labels": dest.get("label") == dest_tab.get("label") == label,
            "destination_single_pane_tab": dest.get("pane_count") == dest.get("tab_count") == dest_tab.get("pane_count") == 1,
            "pane_count_unchanged": len(before["panes"]) == len(after["panes"]),
        }
        # Missing session metadata is unavailable, not equality proof. Terminal /
        # PID continuity and the operator binding are the measured evidence here.
        session_status = "unavailable"
        if session_before:
            checks["reported_session_unchanged"] = session_before == _session(moved) == _session(live)
            session_status = "unchanged" if checks["reported_session_unchanged"] else "changed"
        out.update(applied=True, verified=all(checks.values()), checks=checks, identity=_identity(live),
                   previous_identity=_identity(p), process_after=after_process,
                   reported_session_continuity=session_status)
    except Exception as e:
        out["error"] = str(e) if isinstance(e, IdentityError) else "post-move metadata unavailable; inspect before any retry"
    return out
