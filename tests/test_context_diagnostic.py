import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_diagnostic as diagnostic
import inventory

ENV = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1", "HERDR_TAB_ID": "old:tab", "HERDR_WORKSPACE_ID": "old:workspace"}
STATUS = {"client": {"version": "0.9.5", "session": "PRIVATE"}, "server": {"running": True, "compatible": True, "endpoint_compatible": True, "socket": "/private/socket", "session": None}}
CURRENT = {"result": {"type": "pane_current", "pane": {"pane_id": "w2:p2", "tab_id": "w2:t2", "workspace_id": "w2", "title": "SECRET", "cwd": "PRIVATE"}}}


def reply(body, code=0, stderr=""):
    return subprocess.CompletedProcess([], code, json.dumps(body), stderr)


class DiagnosticTests(unittest.TestCase):
    def diagnose(self, responses=None, **kwargs):
        calls = []
        responses = iter(responses or [reply(STATUS), reply(CURRENT)])
        def run(argv, **options):
            calls.append(argv)
            self.assertEqual(options, {"capture_output": True, "text": True, "timeout": 5})
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value
        result = diagnostic.diagnose(env=kwargs.pop("env", ENV), run=run, **kwargs)
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(calls, [["herdr", *args] for args in diagnostic.COMMANDS[:len(calls)]])
        return result, calls

    def test_outside_context_never_calls_cli(self):
        result, calls = self.diagnose(env={"HERDR_ENV": "0"})
        self.assertEqual(calls, [])
        self.assertFalse(result["verified"])

    def test_verified_live_workspace_and_tab_override_old_aliases(self):
        result, calls = self.diagnose()
        self.assertTrue(result["verified"])
        self.assertEqual(result["current"]["workspace_id"], "w2")
        self.assertEqual(len(calls), 2)
        self.assertTrue(result["inherited_pane_alias_observed"])

    def test_expected_socket_compares_but_does_not_route(self):
        result, calls = self.diagnose(expected_socket="/other/socket")
        self.assertEqual(result["finding"], "wrong_server_context")
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.diagnose(expected_socket="/private/socket")[0]["verified"])

    def test_failures_are_conservative_and_do_not_leak_errors(self):
        for code, finding in [("pane_not_found", "pane_not_found"), ("access_denied", "access_denied"), ("unknown_SECRET", "unavailable")]:
            with self.subTest(code=code):
                result, _ = self.diagnose([reply(STATUS), reply({}, 1, json.dumps({"error": {"code": code, "message": "SECRET token"}}))])
                self.assertEqual(result["finding"], finding)
                self.assertNotIn("SECRET", json.dumps(result))
        result, _ = self.diagnose([PermissionError("SECRET")])
        self.assertEqual(result["finding"], "access_denied")
        result, _ = self.diagnose([subprocess.TimeoutExpired("SECRET", 5)])
        self.assertEqual(result["finding"], "timeout")
        result, _ = self.diagnose([FileNotFoundError("SECRET")])
        self.assertEqual(result["finding"], "unavailable")

    def test_malformed_incompatible_and_contradictory_replies(self):
        for current in [{}, {"result": {"type": "other", "pane": CURRENT["result"]["pane"]}}, {"result": {"type": "pane_current", "pane": {"pane_id": "different", "tab_id": "t", "workspace_id": "w"}}}]:
            result, _ = self.diagnose([reply(STATUS), reply(current)])
            self.assertFalse(result["verified"])
        result, _ = self.diagnose([reply({"server": []})])
        self.assertEqual(result["finding"], "malformed")
        status = json.loads(json.dumps(STATUS)); status["server"]["compatible"] = False
        self.assertEqual(self.diagnose([reply(status)])[0]["finding"], "incompatible")

    def test_allowlist_redacts_nonidentity_environment_and_payload(self):
        result, _ = self.diagnose(env={**ENV, "HERDR_TAB_ID": "SECRET with spaces", "TOKEN": "SECRET"})
        text = json.dumps(result)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("/private/socket", text)

    def test_diagnostic_dispatch_never_initializes_cache_or_inventory(self):
        with patch.object(inventory, "Cache", side_effect=AssertionError("cache")), patch.object(inventory, "inventory_l1", side_effect=AssertionError("inventory")), patch.object(inventory, "LiveHerdr", side_effect=AssertionError("normal client")), patch.object(diagnostic, "diagnose", return_value={"verified": False}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inventory.main(["diagnose-context", "--json"]), 3)
        with patch.object(diagnostic, "diagnose", return_value={"verified": True}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inventory.main(["diagnose-context", "--json"]), 0)

    def test_normal_context_guard_unchanged(self):
        h = type("Client", (), {"env": {}, "call": lambda *args: self.fail("CLI called")})()
        with self.assertRaises(inventory.NoContext):
            inventory.verify_context(h)

    def test_inconsistent_current_prefixes_and_tokenlike_ids_refused(self):
        for pane in [
            {"pane_id": "w3:p1", "tab_id": "w2:t2", "workspace_id": "w2"},
            {"pane_id": "token_SECRET", "tab_id": "w2:t2", "workspace_id": "w2"},
            {"pane_id": "w2:p2", "tab_id": "w3:t2", "workspace_id": "w2"},
        ]:
            result, _ = self.diagnose([reply(STATUS), reply({"result": {"type": "pane_current", "pane": pane}})])
            self.assertEqual(result["finding"], "malformed")
            self.assertNotIn("SECRET", json.dumps(result))
        result, _ = self.diagnose(env={**ENV, "HERDR_PANE_ID": "ghp_SECRET"})
        self.assertFalse(result["verified"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_fixture_diagnostic_cannot_reach_live_or_load_fixture(self):
        with patch.object(diagnostic, "diagnose", side_effect=AssertionError("diagnostic called")), patch.object(inventory, "FixtureHerdr", side_effect=AssertionError("fixture loaded")), patch.object(inventory, "LiveHerdr", side_effect=AssertionError("live client")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inventory.main(["--fixture", "must-not-open.json", "diagnose-context", "--json"]), 3)

    def test_normal_mode_still_exits_three_without_context(self):
        h = type("Client", (), {"env": {}, "call": lambda *args: self.fail("CLI called")})()
        with patch.object(inventory, "LiveHerdr", return_value=h), patch.object(inventory, "Cache"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inventory.main(["context", "--json"]), 3)

    def test_access_denied_stage_and_prior_status_retained(self):
        denial = reply({}, 1, json.dumps({"error": {"code": "access_denied", "message": "SECRET"}}))
        first, calls = self.diagnose([denial])
        self.assertEqual((first["finding"], first["stage"]), ("access_denied", "status"))
        self.assertEqual(first["status"], {})
        second, calls = self.diagnose([reply(STATUS), denial])
        self.assertEqual((second["finding"], second["stage"]), ("access_denied", "current"))
        self.assertTrue(second["status"]["running"])
        self.assertEqual(second["status"]["client_version"], "0.9.5")
        self.assertNotIn("SECRET", json.dumps(second))
        self.assertEqual(self.diagnose()[0]["stage"], "complete")
        self.assertEqual(self.diagnose(env={})[0]["stage"], "outside_herdr")

    def test_apply_not_supported(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            inventory.build_parser().parse_args(["diagnose-context", "--apply"])


if __name__ == "__main__":
    unittest.main()
