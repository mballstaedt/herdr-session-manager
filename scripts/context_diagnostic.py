"""Bounded metadata-only diagnosis. Never repairs context or selects an endpoint."""
from __future__ import annotations

import json
import os
import re
import subprocess

COMMANDS = (("status", "--json"), ("pane", "current", "--current"))
IDENTITY_KEYS = ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID")


def _identity(value, kind):
    patterns = {"pane": r"w[0-9A-Za-z]+:p[0-9A-Za-z]+",
                "tab": r"w[0-9A-Za-z]+:t[0-9A-Za-z]+",
                "workspace": r"w[0-9A-Za-z]+", "env": r"[01]"}
    return value if isinstance(value, str) and re.fullmatch(patterns[kind], value) else None


def _error(cp):
    try:
        body = json.loads(cp.stderr)
        code = body.get("error", {}).get("code")
    except (ValueError, AttributeError, TypeError):
        code = None
    if code in ("pane_not_found", "access_denied", "permission_denied"):
        return "access_denied" if code == "permission_denied" else code
    # Common OS-level denial is recognized, never returned verbatim or blamed on sandbox.
    if re.search(r"permission denied|operation not permitted|access denied", cp.stderr or "", re.I):
        return "access_denied"
    return "unavailable"


def diagnose(expected_socket=None, *, env=None, run=None):
    env = os.environ if env is None else env
    run = subprocess.run if run is None else run
    result = {"schema": "herdr-context-diagnostic/v1", "verified": False,
              "finding": "unavailable", "stage": "outside_herdr", "environment": {k: _identity(env.get(k), kind) for k, kind in zip(IDENTITY_KEYS, ("env", "pane", "tab", "workspace"))},
              "status": {}, "current": {}, "next_actions": []}

    def finish(finding):
        result["finding"] = finding
        result["verified"] = finding == "verified"
        advice = {
            "verified": "Current pane is confirmed by the inherited server; workspace aliases may differ after a move. No repair performed.",
            "outside_herdr": "Use an already authorized Herdr-managed context. No server call was attempted.",
            "pane_not_found": "Stale or wrong context remains unresolved. Ask the existing owner to reconcile identity; do not overwrite environment or resume a session.",
            "wrong_server_context": "Trusted expected socket differs from server-reported socket. Ask the existing owner to reconcile; no alternate endpoint was contacted.",
            "access_denied": "Access was denied; the cause is unproven. If appropriate, request a scoped read-only permission route for these two metadata commands only; do not automatically retry.",
        }
        result["next_actions"] = [advice.get(finding, "Metadata could not establish current identity. Review the supported CLI schema and existing owner context; no repair or retry was attempted.")]
        return result

    if env.get("HERDR_ENV") != "1":
        return finish("outside_herdr")
    replies = []
    for args in COMMANDS:
        result["stage"] = "status" if args == COMMANDS[0] else "current"
        try:
            cp = run(["herdr", *args], capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            return finish("timeout")
        except PermissionError:
            return finish("access_denied")
        except OSError:
            return finish("unavailable")
        if cp.returncode:
            return finish(_error(cp))
        try:
            if len(cp.stdout) > 65536:
                return finish("malformed")
            body = json.loads(cp.stdout)
            if not isinstance(body, dict):
                return finish("malformed")
        except (ValueError, TypeError):
            return finish("malformed")
        replies.append(body)
        if len(replies) == 1:
            server = body.get("server")
            client = body.get("client")
            if not isinstance(server, dict) or not isinstance(client, dict):
                return finish("malformed")
            result["status"] = {k: server.get(k) for k in ("running", "compatible", "endpoint_compatible") if type(server.get(k)) is bool}
            result["status"]["client_version"] = client.get("version") if isinstance(client.get("version"), str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,60}", client["version"]) else None
            if server.get("compatible") is False or server.get("endpoint_compatible") is False:
                return finish("incompatible")
            if any(type(server.get(k)) is not bool for k in ("running", "compatible", "endpoint_compatible")) or not isinstance(server.get("socket"), str) or not server["socket"]:
                return finish("malformed")
            if not server["running"]:
                return finish("unavailable")
            # Socket values may contain private paths: report comparison only, never echo them.
            result["status"]["expected_socket_matches"] = None if expected_socket is None else expected_socket == server["socket"]
            if expected_socket is not None and expected_socket != server["socket"]:
                return finish("wrong_server_context")
    current = replies[1].get("result")
    if not isinstance(current, dict) or current.get("type") != "pane_current" or not isinstance(current.get("pane"), dict):
        return finish("malformed")
    pane = current["pane"]
    ids = {k: _identity(pane.get(k), kind) for k, kind in (("pane_id", "pane"), ("tab_id", "tab"), ("workspace_id", "workspace"))}
    if not all(ids.values()):
        return finish("malformed")
    if any(ids[k].split(":")[0] != ids["workspace_id"] for k in ("pane_id", "tab_id")):
        return finish("malformed")
    result["current"] = ids
    # The current endpoint resolves inherited aliases after cross-workspace moves.
    inherited = result["environment"]["HERDR_PANE_ID"]
    if inherited is None:
        return finish("context_unresolved")
    result["inherited_pane_alias_observed"] = inherited != ids["pane_id"]
    result["stage"] = "complete"
    return finish("verified")
