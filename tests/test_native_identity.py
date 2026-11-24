"""Synthetic native API tests. Never contact the installed Herdr server."""
import copy
import json
import os
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_selected_space import Herdr as LayoutHerdr, SOCKET
from test_self_space import THREAD
import native_identity
from self_space import IdentityError, move_self, resolve_self


class NativeHerdr(LayoutHerdr):
    def __init__(self):
        super().__init__()
        self.env.update(CODEX_THREAD_ID=THREAD, HERDR_PANE_ID="w6:p3")
        self.native_calls = 0
        self.native_error = None
        self.alter = None

    def native_identity(self, endpoint, thread):
        self.native_calls += 1
        if self.native_error:
            raise IdentityError(self.native_error)
        pane = copy.deepcopy(next(p for p in self.panes if p["terminal_id"] == "term_right"))
        result = {"type": "native_client_identity", "agent": "codex", "agent_session_id": THREAD,
                  "native_client_id": "native_123", "terminal_id": pane["terminal_id"],
                  "pane": pane, "lease_remaining_ms": 4000}
        if self.alter:
            self.alter(result, self.native_calls)
        return result

    def native_move(self, endpoint, thread, client, terminal, label):
        self.atomic_request = (endpoint, thread, client, terminal, label)
        return self.call("pane", "move", "w1:p2", "--new-workspace", "--label", label,
                         "--tab-label", label, "--no-focus")["result"]["move_result"]

    def call(self, *args):
        if args[:2] == ("pane", "move"):
            source = copy.deepcopy(next(p for p in self.panes if p["pane_id"] == args[2]))
        result = super().call(*args)
        if args[:2] == ("pane", "move"):
            receipt = result["result"]["move_result"]
            if not any(p["tab_id"] == source["tab_id"] for p in self.panes):
                self.tabs = [t for t in self.tabs if t["tab_id"] != source["tab_id"]]
            receipt["closed_workspace_id"] = None
            if not any(p["workspace_id"] == source["workspace_id"] for p in self.panes):
                del self.labels[source["workspace_id"]]
                receipt["closed_workspace_id"] = source["workspace_id"]
            receipt["created_workspace"] = {"workspace_id": "w3", "pane_count": 1, "tab_count": 1}
            receipt["created_tab"] = {"workspace_id": "w3", "tab_id": "w3:t1", "pane_count": 1}
        return result


class NativeTests(unittest.TestCase):
    def test_stale_env_and_absent_legacy_session_use_only_fresh_native_claim(self):
        h = NativeHerdr()
        env = dict(h.env)
        result = resolve_self(h, SOCKET, native_client=True)
        self.assertEqual(result["identity"]["pane_id"], "w1:p2")
        self.assertEqual(result["method"], "native_client")
        self.assertEqual(result["identity"]["agent_session_id"], THREAD)
        self.assertEqual(h.env, env)
        self.assertTrue(all(call[0] == "status" for call in h.calls))

    def test_missing_inputs_and_wrong_endpoint_do_not_resolve(self):
        for key in ("HERDR_ENV", "CODEX_THREAD_ID"):
            h = NativeHerdr()
            del h.env[key]
            with self.assertRaises(IdentityError):
                resolve_self(h, SOCKET, native_client=True)
            self.assertEqual(h.native_calls, 0)
        h = NativeHerdr()
        with self.assertRaises(IdentityError):
            resolve_self(h, "/wrong", native_client=True)
        self.assertEqual(h.native_calls, 0)

    def test_invalid_expired_conflicting_or_unsupported_claim_has_no_fallback(self):
        for key, value in (("agent_session_id", "other"), ("agent", "claude"),
                           ("lease_remaining_ms", 0), ("lease_remaining_ms", True),
                           ("lease_remaining_ms", 5001), ("terminal_id", "other"),
                           ("native_client_id", None), ("type", "pane_current")):
            h = NativeHerdr()
            h.alter = lambda result, count: result.update({key: value})
            with self.subTest(key=key, value=value), self.assertRaises(IdentityError):
                move_self(h, "own", SOCKET, True, native_client=True)
            self.assertEqual(h.moves, 0)
        for error in ("unsupported", "ambiguous", "expired"):
            h = NativeHerdr()
            h.native_error = error
            with self.assertRaises(IdentityError):
                resolve_self(h, SOCKET, native_client=True)
            self.assertFalse(any(c[:2] == ("pane", "list") for c in h.calls))

    def test_preview_and_one_move_preserve_process_and_other_panes(self):
        h = NativeHerdr()
        self.assertFalse(move_self(h, "own", SOCKET, native_client=True)["applied"])
        result = move_self(h, "own", SOCKET, True, native_client=True)
        self.assertTrue(result["verified"], result)
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(h.moves, 1)
        self.assertEqual(result["identity"]["agent_session_id"], THREAD)
        self.assertEqual(result["native_client_id"], "native_123")
        self.assertEqual(h.atomic_request, (SOCKET, THREAD, "native_123", "term_right", "OWN"))

    def test_frontend_replacement_before_dispatch_stops(self):
        h = NativeHerdr()
        h.alter = lambda result, count: result.update(native_client_id="native_replacement") if count > 1 else None
        with self.assertRaises(IdentityError):
            move_self(h, "own", SOCKET, True, native_client=True)
        self.assertEqual(h.moves, 0)

    def test_last_pane_in_tab_or_space_preserves_all_unrelated_topology(self):
        for keep_other_tab in (False, True):
            h = NativeHerdr()
            h.panes = [p for p in h.panes if p["terminal_id"] != "term_left"]
            if keep_other_tab:
                h.panes.append({"pane_id": "w1:p3", "terminal_id": "term_sibling", "tab_id": "w1:t2", "workspace_id": "w1", "cwd": "/fixture/sibling"})
                h.tabs.append({"tab_id": "w1:t2", "workspace_id": "w1", "label": "SIBLING"})
            result = move_self(h, "own", SOCKET, True, native_client=True)
            with self.subTest(keep_other_tab=keep_other_tab):
                self.assertTrue(result["verified"], result)
                self.assertTrue(all(result["checks"].values()))
                self.assertEqual("w1" in h.labels, keep_other_tab)
                self.assertEqual(h.moves, 1)

    def test_post_move_process_topology_or_claim_failure_is_not_retried(self):
        for mode in ("process", "other", "claim", "closure"):
            h = NativeHerdr()
            if mode == "process":
                h.process_changed = True
            original = h.call
            def call(*args):
                result = original(*args)
                if args[:2] == ("pane", "move"):
                    if mode == "other":
                        h.panes[0]["tab_id"] = "w2:t1"
                    if mode == "claim":
                        h.native_error = "expired"
                    if mode == "closure":
                        result["result"]["move_result"]["closed_workspace_id"] = "w2"
                return result
            h.call = call
            result = move_self(h, "own", SOCKET, True, native_client=True)
            self.assertFalse(result["verified"])
            self.assertTrue(result["applied"])
            self.assertEqual(h.moves, 1)


class TransportTests(unittest.TestCase):
    def test_connected_peer_mismatch_stops_before_thread_delivery(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        info = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.geteuid())
        with patch.object(native_identity.os, "stat", return_value=info), patch.object(native_identity.socket, "socket", return_value=connection), patch.object(native_identity, "_peer_uid", return_value=os.geteuid()+1):
            with self.assertRaises(IdentityError):
                native_identity.request(SOCKET, THREAD)
        connection.sendall.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Unix sockets required")
    def test_kernel_peer_uid_on_synthetic_socketpair(self):
        import socket
        first, second = socket.socketpair()
        with first, second:
            self.assertEqual(native_identity._peer_uid(first), os.geteuid())

    def test_malformed_incomplete_oversized_and_wrong_id_frames_fail_closed(self):
        info = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.geteuid())
        for response in (b"{}", b"{\n", b"x" * 65537, b'{"id":"wrong","result":{}}\n'):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.recv.side_effect = [response, b""]
            with self.subTest(response_size=len(response)), patch.object(native_identity.os, "stat", return_value=info), patch.object(native_identity.socket, "socket", return_value=connection), patch.object(native_identity, "_peer_uid", return_value=os.geteuid()):
                with self.assertRaises(IdentityError):
                    native_identity.request(SOCKET, THREAD)

    def test_bounded_readonly_request_and_response_identity(self):
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, timeout): self.timeout = timeout
            def connect(self, endpoint): self.endpoint = endpoint
            def sendall(self, body): self.request = json.loads(body)
            def recv(self, count): return json.dumps({"id": self.request["id"], "result": {"fixture": True}}).encode() + b"\n"
        connection = Connection()
        info = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.geteuid())
        with patch.object(native_identity.os, "stat", return_value=info), patch.object(native_identity.socket, "socket", return_value=connection), patch.object(native_identity, "_peer_uid", return_value=os.geteuid()):
            self.assertEqual(native_identity.request(SOCKET, THREAD), {"fixture": True})
        self.assertEqual(connection.request["method"], "agent.resolve_native_client")
        self.assertEqual(connection.request["params"], {"agent": "codex", "agent_session_id": THREAD})

    def test_native_move_uses_guarded_method_and_checks_receipt(self):
        response = {"type": "native_client_move", "native_client_id": "native_1", "agent_session_id": THREAD, "move_result": {"changed": True}}
        with patch.object(native_identity, "_request", return_value=response) as request:
            self.assertEqual(native_identity.move(SOCKET, THREAD, "native_1", "term_1", "OWN"), {"changed": True})
            args = request.call_args.args
            self.assertEqual(args[1], "agent.move_native_client")
            self.assertEqual(args[2]["native_client_id"], "native_1")
            self.assertNotIn("pane_id", args[2])
            self.assertFalse(args[2]["focus"])
        response["native_client_id"] = "other"
        with patch.object(native_identity, "_request", return_value=response):
            with self.assertRaises(IdentityError):
                native_identity.move(SOCKET, THREAD, "native_1", "term_1", "OWN")

    def test_wrong_endpoint_ownership_permissions_or_type_prevent_connect(self):
        for mode, uid in ((stat.S_IFREG | 0o600, os.geteuid()), (stat.S_IFSOCK | 0o666, os.geteuid()), (stat.S_IFSOCK | 0o600, os.geteuid()+1)):
            with patch.object(native_identity.os, "stat", return_value=SimpleNamespace(st_mode=mode, st_uid=uid)), patch.object(native_identity.socket, "socket", side_effect=AssertionError("connect")):
                with self.assertRaises(IdentityError):
                    native_identity.request(SOCKET, THREAD)


if __name__ == "__main__":
    unittest.main()
