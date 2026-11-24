"""Exact-thread resolution and guarded moves through Herdr's native-client API.

Only the native frontend registers ownership. This tool never registers a claim.
"""
import json
import os
import socket
import stat
import sys
import uuid

from self_space import IdentityError, _id, _pane, _status


def _peer_uid(connection):
    if sys.platform == "darwin":
        import ctypes
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        function = ctypes.CDLL(None, use_errno=True).getpeereid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        function.restype = ctypes.c_int
        if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise IdentityError("native identity peer credentials unavailable")
        return uid.value
    if sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED"):
        import struct
        return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]
    raise IdentityError("native identity peer credentials unsupported")


def _request(endpoint, method, params):
    """Use only the already compared local endpoint; bound all input/output."""
    try:
        info = os.stat(endpoint, follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise IdentityError("native identity endpoint ownership or permissions invalid")
        request_id = str(uuid.uuid4())
        body = {"id": request_id, "method": method, "params": params}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(endpoint)
            if _peer_uid(connection) != os.geteuid():
                raise IdentityError("native identity peer ownership invalid")
            connection.sendall(json.dumps(body).encode() + b"\n")
            data = bytearray()
            while b"\n" not in data and len(data) <= 65536:
                chunk = connection.recv(min(4096, 65537 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
        if len(data) > 65536 or not data.endswith(b"\n"):
            raise IdentityError("native identity response incomplete or oversized")
        result = json.loads(data)
        if not isinstance(result, dict) or result.get("id") != request_id:
            raise IdentityError("native identity unavailable, rejected or ambiguous")
        if "error" in result:
            error = result["error"]
            code = error.get("code") if isinstance(error, dict) else None
            if code not in {"native_client_unavailable", "ambiguous_native_client", "native_client_changed",
                            "native_client_sequence", "invalid_native_client", "server_unavailable", "timeout"}:
                code = "native_api_unavailable"
            raise IdentityError("native identity request refused: " + code, code, method)
        return result.get("result")
    except IdentityError:
        raise
    except (OSError, ValueError, TypeError):
        raise IdentityError("native identity transport unavailable or malformed") from None


def request(endpoint, thread):
    return _request(endpoint, "agent.resolve_native_client",
                    {"agent": "codex", "agent_session_id": thread})


def move(endpoint, thread, client, terminal, label):
    result = _request(endpoint, "agent.move_native_client", {
        "agent": "codex", "agent_session_id": thread, "native_client_id": client,
        "terminal_id": terminal, "destination": {"type": "new_workspace", "label": label, "tab_label": label},
        "focus": False})
    if not isinstance(result, dict) or result.get("type") != "native_client_move" or result.get("native_client_id") != client or result.get("agent_session_id") != thread:
        raise IdentityError("native move receipt identity unavailable; do not retry")
    return result.get("move_result")


def resolve(h, expected_socket):
    if h.env.get("HERDR_ENV") != "1" or not expected_socket:
        raise IdentityError("Herdr context and independently confirmed endpoint required")
    thread = h.env.get("CODEX_THREAD_ID")
    if not _id(thread, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"):
        raise IdentityError("trusted host CODEX_THREAD_ID unavailable")
    endpoint = _status(h, expected_socket)
    result = h.native_identity(endpoint, thread)
    if not isinstance(result, dict) or result.get("type") != "native_client_identity" or result.get("agent") != "codex" or result.get("agent_session_id") != thread:
        raise IdentityError("native identity response does not match this conversation")
    lease = result.get("lease_remaining_ms")
    client = result.get("native_client_id")
    if type(lease) is not int or not 0 < lease <= 5000 or not _id(client, r"[0-9A-Za-z_.:-]{1,200}"):
        raise IdentityError("native client ownership or lease unavailable")
    pane = _pane(result.get("pane"))
    if result.get("terminal_id") != pane["terminal_id"]:
        raise IdentityError("native terminal identity inconsistent")
    if _status(h, expected_socket) != endpoint:
        raise IdentityError("server endpoint changed during native resolution")
    identity = {k: pane[k] for k in ("pane_id", "tab_id", "workspace_id", "terminal_id", "cwd")}
    identity["agent_session_id"] = thread
    return {"schema": "herdr-self-space/v1", "verified": True,
            "method": "native_client", "identity": identity,
            "native_client_id": client, "repaired": False}


def snapshot(h, identity):
    from selected_space import _snapshot, _processes
    topology = _snapshot(h)
    pane = topology["panes"].get(identity["pane_id"])
    if not isinstance(pane, dict) or any(pane.get(k) != identity[k] for k in ("terminal_id", "cwd", "tab_id", "workspace_id")):
        raise IdentityError("native pane changed during topology snapshot")
    return topology, _processes(h, identity["pane_id"])


def verify_move(h, before, old, new, label, receipt):
    from selected_space import _pane_sig, _tab_sig, _workspace_sig
    topology, process = before
    after, process_after = snapshot(h, new)
    source_tab = dict(topology["tabs"][old["tab_id"]])
    source_tab["pane_count"] -= 1
    expected_tabs = dict(topology["tabs"])
    if source_tab["pane_count"]:
        expected_tabs[old["tab_id"]] = source_tab
    else:
        del expected_tabs[old["tab_id"]]
    expected_workspaces = {k: dict(v) for k, v in topology["workspaces"].items()}
    expected_workspaces[old["workspace_id"]]["pane_count"] -= 1
    if not source_tab["pane_count"]:
        expected_workspaces[old["workspace_id"]]["tab_count"] -= 1
    closed_workspace = None
    if expected_workspaces[old["workspace_id"]]["pane_count"] == 0:
        # The existing native move closes a source space only when it is empty.
        # No separate close operation is issued by this helper.
        closed_workspace = old["workspace_id"]
        del expected_workspaces[closed_workspace]
    destination = after["workspaces"].get(new["workspace_id"], {})
    destination_tab = after["tabs"].get(new["tab_id"], {})
    checks = {
        "source_workspace_closure_matches_receipt": receipt.get("closed_workspace_id") == closed_workspace,
        "native_processes_unchanged": process == process_after,
        "other_panes_topology_unchanged": sorted(_pane_sig(p) for k, p in topology["panes"].items() if k != old["pane_id"]) == sorted(_pane_sig(p) for k, p in after["panes"].items() if k != new["pane_id"]),
        "existing_tabs_preserved": {k: _tab_sig(v) for k, v in expected_tabs.items()} == {k: _tab_sig(v) for k, v in after["tabs"].items() if k != new["tab_id"]},
        "existing_workspaces_preserved": {k: _workspace_sig(v) for k, v in expected_workspaces.items()} == {k: _workspace_sig(v) for k, v in after["workspaces"].items() if k != new["workspace_id"]},
        "destination_is_new": new["workspace_id"] not in topology["workspaces"] and new["tab_id"] not in topology["tabs"],
        "destination_labels": destination.get("label") == destination_tab.get("label") == label,
        "destination_single_pane_tab": destination.get("pane_count") == destination.get("tab_count") == destination_tab.get("pane_count") == 1,
        "pane_count_unchanged": len(topology["panes"]) == len(after["panes"]),
    }
    if not all(checks.values()):
        raise IdentityError("native move process/topology verification failed", details=checks)
    return checks
