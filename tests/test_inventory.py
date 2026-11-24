#!/usr/bin/env python3
"""Tests for herdr-session-manager. Stdlib unittest; no live Herdr needed.

    python3 -m unittest discover -s tests -v      (from the skill directory)
"""
import contextlib
import subprocess
import io
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.dont_write_bytecode = True          # never leave __pycache__ in the projected skill source
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import inventory as inv  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _dump(obj, path):
    with open(path, "w") as fh:
        json.dump(obj, fh)


def fx(name):
    return os.path.join(FIX, name + ".json")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = self.tmp.name
        rules = os.path.join(self.tmp.name, "boundaries.json")
        with open(rules, "w") as fh:
            json.dump([{"prefix": "/work/employer/", "boundary": "employer"}, {"prefix": "/work/firm/", "boundary": "firm"}], fh)
        self._env = dict(os.environ)
        os.environ["HSM_BOUNDARIES_FILE"] = rules
        os.environ["HOME_FOR_HISTORY_DISABLED"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = inv.main(["--cache-dir", self.cache_dir, *args])
        return code, out.getvalue(), err.getvalue()

    def live(self, name, workspace=None, all_ws=False):
        h = inv.FixtureHerdr(fx(name))
        ctx = inv.verify_context(h, workspace, all_ws)
        cache = inv.Cache(self.cache_dir, "fixture")
        return h, ctx, cache

    def sessions(self, inventory):
        return {s["pane"]: s for w in inventory["workspaces"] for s in w["sessions"]}


# --------------------------------------------------------------------------- context & scope

class ContextTests(Base):
    def test_no_herdr_env_fails_safely(self):
        code, out, err = self.run_cli("--fixture", fx("no_context"), "list")
        self.assertEqual(code, inv.EXIT_NO_CONTEXT)
        self.assertIn("not running inside a Herdr-managed pane", err)

    def test_no_context_json(self):
        code, out, _ = self.run_cli("--fixture", fx("no_context"), "status", "--json")
        self.assertEqual(code, inv.EXIT_NO_CONTEXT)
        self.assertEqual(json.loads(out)["error"], "no_verified_herdr_context")

    def test_unknown_workspace_refused(self):
        code, _, err = self.run_cli("--fixture", fx("single"), "list", "--workspace", "w99")
        self.assertEqual(code, inv.EXIT_NO_CONTEXT)
        self.assertIn("not found", err)

    def test_default_scope_is_callers_workspace_only(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        self.assertEqual([w["workspace_id"] for w in i["workspaces"]], ["w1"])

    def test_all_is_explicit(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "list", "--all")
        self.assertIn("billing", out)
        self.assertIn("personal", out)


# --------------------------------------------------------------------------- level 1: list

class ListTests(Base):
    def test_list_is_readable_table_not_json(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "list")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("**Herdr — billing**"))
        self.assertIn("| Pane | Tab | Session | Agent | State |", out)
        self.assertNotIn('"schema"', out)

    def test_list_reads_no_terminal_output(self):
        h, ctx, cache = self.live("mixed")
        inv.render_list(inv.inventory_l1(h, ctx, cache))
        self.assertFalse(any(c[:2] == ["pane", "read"] for c in h.calls))
        self.assertFalse(any(c[:2] == ["pane", "process-info"] for c in h.calls))

    def test_claude_and_codex_together(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))
        self.assertEqual(s["p1"]["agent_kind"], "claude")
        self.assertEqual(s["p2"]["agent_kind"], "codex")
        self.assertEqual(s["p8"]["agent_kind"], "none")

    def test_agent_names_join_by_pane_id(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))
        self.assertEqual(s["p4"]["agent_name"], "webhooks")
        self.assertIsNone(s["p3"]["agent_name"])

    def test_empty_workspace(self):
        code, out, _ = self.run_cli("--fixture", fx("empty"), "list", "--workspace", "w5")
        self.assertEqual(code, 0)
        self.assertIn("Empty workspace", out)


# --------------------------------------------------------------------------- title quality

class TitleTests(Base):
    def test_questionable_titles(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))
        flagged = {k for k, v in s.items() if v["questionable"]}
        self.assertEqual(flagged, {"p1", "p2", "p3", "p4", "p6", "p7"})
        self.assertEqual(s["p1"]["title_issue"], "generic title")
        self.assertIn("missing session title", s["p2"]["title_issue"])
        self.assertIn("repository/directory", s["p3"]["title_issue"])
        self.assertIn("very long", s["p4"]["title_issue"])
        self.assertIn("ambiguous", s["p6"]["title_issue"])

    def test_short_title_is_not_bad_by_itself(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))
        self.assertFalse(s["p5"]["questionable"])          # "API"

    def test_agent_status_unknown_is_not_title_unknown(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))
        self.assertEqual(s["p5"]["herdr_status"], "unknown")
        self.assertFalse(s["p5"]["questionable"])

    def test_blocked_state_alone_does_not_flag(self):
        h, ctx, cache = self.live("w6_before")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))
        self.assertEqual(s["p9"]["herdr_status"], "blocked")
        self.assertFalse(s["p9"]["questionable"])

    def test_plain_shell_not_flagged(self):
        h, ctx, cache = self.live("mixed")
        self.assertFalse(self.sessions(inv.inventory_l1(h, ctx, cache))["p8"]["questionable"])

    def test_generic_rules(self):
        for t in ["Claude", "codex", "Terminal", "New Session", "Untitled", "Shell", "3", "tab 2", ""]:
            self.assertIsNotNone(inv.title_issue(t, {}), t)
        for t in ["API", "Security - PR 42", "Billing - Lead Validation", "Fix login"]:
            self.assertIsNone(inv.title_issue(t, {}), t)


# --------------------------------------------------------------------------- organize

class OrganizeTests(Base):
    def organize(self, name, sync=False):
        h, ctx, cache = self.live(name)
        i = inv.inventory_l1(h, ctx, cache)
        plan = inv.plan_organize(i, sync)
        res = inv.apply_organize(h, ctx, plan, cache, sync) if not plan["noop"] else None
        after = inv.inventory_l1(h, ctx, cache)
        return h, ctx, cache, i, plan, res, after

    def test_operator_case_nine_panes_three_tabs(self):
        h, ctx, cache, before, plan, res, after = self.organize("w6_before")
        w = after["workspaces"][0]
        self.assertEqual(len(w["tabs"]), 9)
        self.assertEqual(len(w["sessions"]), 9)
        per_tab = {}
        for s in w["sessions"]:
            per_tab[s["tab_id"]] = per_tab.get(s["tab_id"], 0) + 1
        self.assertTrue(all(n == 1 for n in per_tab.values()))
        self.assertEqual(res["moved"], 6)
        self.assertEqual(res["renamed"], 3)
        self.assertEqual(res["preserved"], 9)
        self.assertTrue(all(v is True for k, v in res["checks"].items() if k != "continuity_problems"))

    def test_equivalence_with_tested_bash_script(self):
        """Tested script: new tab label = terminal_title_stripped in uppercase; first pane stays; generic '1' retitled."""
        h, ctx, cache, before, plan, res, after = self.organize("w6_before")
        labels = {t["tab_id"]: t["label"] for t in after["workspaces"][0]["tabs"]}
        for s in after["workspaces"][0]["sessions"]:
            if s["pane"] in ("p9",):
                continue
            self.assertEqual(labels[s["tab_id"]], s["title"].upper(), s["pane"])
        self.assertEqual(self.sessions(after)["p1"]["tab_id"], "w6:t1")
        self.assertEqual(labels["w6:t2"], "DATA")                     # meaningful label kept by default

    def test_sync_titles_reproduces_script_exactly(self):
        h, ctx, cache, before, plan, res, after = self.organize("w6_before", sync=True)
        labels = {t["tab_id"]: t["label"] for t in after["workspaces"][0]["tabs"]}
        self.assertEqual(labels["w6:t2"], "DATA ACCESS REQUEST")

    def test_pane_ids_and_processes_preserved(self):
        h, ctx, cache, before, plan, res, after = self.organize("w6_before")
        b, a = self.sessions(before), self.sessions(after)
        self.assertEqual(set(b), set(a))
        for k in b:
            self.assertEqual(b[k]["terminal_id"], a[k]["terminal_id"])
            self.assertEqual(b[k]["agent_session_id"], a[k]["agent_session_id"])
            self.assertEqual(b[k]["workspace_id"], a[k]["workspace_id"])

    def test_only_move_and_rename_commands_issued(self):
        h, *_ = self.organize("w6_before")
        mutating = [c for c in h.calls if c[:2] in (["pane", "move"], ["tab", "rename"])]
        self.assertTrue(mutating)
        for c in h.calls:
            self.assertNotIn(c[:2], (["pane", "close"], ["pane", "send-text"], ["pane", "send-keys"], ["pane", "run"],
                                     ["agent", "prompt"], ["agent", "send-keys"], ["tab", "close"], ["workspace", "create"]))
        for c in mutating:
            if c[:2] == ["pane", "move"]:
                self.assertIn("--new-tab", c)
                self.assertIn("--no-focus", c)
                self.assertEqual(c[c.index("--workspace") + 1], "w6")

    def test_other_workspaces_untouched(self):
        h, ctx, cache, before, plan, res, after = self.organize("w6_before")
        w7 = [p for p in h.state["panes"] if p["workspace_id"] == "w7"]
        self.assertEqual([(p["pane_id"], p["tab_id"]) for p in w7], [("w7:p1", "w7:t1")])
        self.assertTrue(res["checks"]["workspace_isolation"])

    def test_idempotent_rerun(self):
        h, ctx, cache, before, plan, res, after = self.organize("w6_before")
        again = inv.plan_organize(after)
        self.assertTrue(again["noop"], again["ops"])

    def test_idempotent_rerun_sync_titles(self):
        h, ctx, cache, before, plan, res, after = self.organize("w6_before", sync=True)
        self.assertTrue(inv.plan_organize(after, True)["noop"])

    def test_organizer_pane_stays_put(self):
        h, ctx, cache, before, plan, res, after = self.organize("mixed")
        moved = {o["pane_id"] for o in plan["ops"] if o["op"] == "move"}
        self.assertNotIn("w1:p9", moved)
        self.assertIn("w1:p1", moved)                                  # first pane moves because caller is in the tab
        self.assertEqual(self.sessions(after)["p9"]["tab_id"], "w1:t1")

    def test_single_pane_noop(self):
        h, ctx, cache = self.live("single")
        for tab in h.state['tabs']:
            tab['label'] = tab['label'].upper()
        self.assertTrue(inv.plan_organize(inv.inventory_l1(h, ctx, cache))["noop"])
        code, out, _ = self.run_cli("--fixture", fx("single"), "organize")
        self.assertEqual(code, 0)
        self.assertIn("0 panes moved", out)

    def test_existing_meaningful_tab_names_kept(self):
        h, ctx, cache, before, plan, res, after = self.organize("mixed")
        labels = {t["tab_id"]: t["label"] for t in after["workspaces"][0]["tabs"]}
        self.assertEqual(labels["w1:t1"], "AGENTS")
        self.assertEqual(labels["w1:t3"], "MISC")

    def test_duplicate_names_disambiguated(self):
        h, ctx, cache, before, plan, res, after = self.organize("mixed")
        labels = [t["label"] for t in after["workspaces"][0]["tabs"]]
        self.assertEqual(len(labels), len(set(l.lower() for l in labels)), labels)

    def test_colliding_label_gets_pane_suffix(self):
        st = _load(fx("mixed"))
        for t in st["tabs"]:
            if t["tab_id"] == "w1:t3":
                t["label"] = "Release notes"               # an existing tab already carries p6's title
        path = os.path.join(self.tmp.name, "collide.json")
        _dump(st, path)
        h = inv.FixtureHerdr(path)
        ctx = inv.verify_context(h)
        plan = inv.plan_organize(inv.inventory_l1(h, ctx, inv.Cache(self.cache_dir, "fixture")))
        op = next(o for o in plan["ops"] if o.get("pane_id") == "w1:p6")
        self.assertEqual(op["label"], "RELEASE NOTES (P6)")

    def test_tab_not_renamed_to_questionable_title(self):
        st = _load(fx("w6_before"))
        for p in st["panes"]:
            if p["pane_id"] == "w6:p1":
                p["terminal_title_stripped"] = "op@host:/work/employer/project-a"
        path = os.path.join(self.tmp.name, "hosttitle.json")
        _dump(st, path)
        h = inv.FixtureHerdr(path)
        plan = inv.plan_organize(inv.inventory_l1(h, inv.verify_context(h), inv.Cache(self.cache_dir, "fixture")))
        self.assertFalse([o for o in plan["ops"] if o["op"] == "rename" and o["tab_id"] == "w6:t1"])

    def test_missing_title_gets_fallback_and_question_mark(self):
        h, ctx, cache, before, plan, res, after = self.organize("mixed")
        op = next(o for o in plan["ops"] if o.get("pane_id") == "w1:p2")
        self.assertEqual(op["label"], "CODEX-A")
        self.assertTrue(op["questionable"])

    def test_failed_move_reported_and_others_continue(self):
        h, ctx, cache = self.live("w6_before")
        h.state["fail_moves"] = ["w6:p3"]
        plan = inv.plan_organize(inv.inventory_l1(h, ctx, cache))
        res = inv.apply_organize(h, ctx, plan, cache)
        failed = [r for r in res["results"] if r["result"] == "failed"]
        self.assertEqual([r["pane_id"] for r in failed], ["w6:p3"])
        self.assertIn("pane move failed", failed[0]["reason"])
        self.assertEqual(res["moved"], 5)

    def test_failed_move_exit_code(self):
        st = _load(fx("w6_before"))
        st["fail_moves"] = ["w6:p4"]
        path = os.path.join(self.tmp.name, "fail.json")
        _dump(st, path)
        code, out, _ = self.run_cli("--fixture", path, "organize")
        self.assertEqual(code, inv.EXIT_ERR)
        self.assertIn("**failed** `w6:p4`", out)

    def test_continuity_break_aborts_remaining(self):
        h, ctx, cache = self.live("w6_before")
        h.state["break_continuity"] = ["w6:p2"]
        plan = inv.plan_organize(inv.inventory_l1(h, ctx, cache))
        res = inv.apply_organize(h, ctx, plan, cache)
        self.assertEqual(res["results"][0]["result"], "moved-unverified")
        self.assertTrue(res["aborted"])
        self.assertTrue(all(r["result"] == "not-attempted" for r in res["results"][1:]))
        self.assertFalse(res["checks"]["process_continuity"])

    def test_stale_pane_id_skipped(self):
        h, ctx, cache = self.live("w6_before")
        plan = inv.plan_organize(inv.inventory_l1(h, ctx, cache))
        plan["ops"].insert(0, {"op": "move", "pane_id": "w6:p77", "terminal_id": "term_gone", "workspace_id": "w6",
                               "from_tab": "w6:t1", "label": "ghost", "questionable": False, "agent_session_id": None})
        res = inv.apply_organize(h, ctx, plan, cache)
        self.assertEqual(res["results"][0]["result"], "skipped")
        self.assertIn("stale pane id", res["results"][0]["reason"])

    def test_pane_changed_since_plan_skipped(self):
        h, ctx, cache = self.live("w6_before")
        plan = inv.plan_organize(inv.inventory_l1(h, ctx, cache))
        plan["ops"][0]["terminal_id"] = "term_other"
        res = inv.apply_organize(h, ctx, plan, cache)
        self.assertEqual(res["results"][0]["result"], "skipped")

    def test_renumbered_pane_id_tracked(self):
        h, ctx, cache = self.live("w6_before")
        h.state["renumber_on_move"] = ["w6:p2"]
        plan = inv.plan_organize(inv.inventory_l1(h, ctx, cache))
        res = inv.apply_organize(h, ctx, plan, cache)
        r = res["results"][0]
        self.assertEqual(r["result"], "moved")
        self.assertNotEqual(r["new_pane_id"], "w6:p2")
        self.assertEqual(r["previous_pane_id"], "w6:p2")

    def test_dry_run_changes_nothing(self):
        code, out, _ = self.run_cli("--fixture", fx("w6_before"), "organize", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("would move 6 panes and rename 3 tabs", out)

    def test_cli_summary_line(self):
        code, out, _ = self.run_cli("--fixture", fx("w6_before"), "organize")
        self.assertEqual(code, 0, out)
        self.assertIn("Organized: 6 panes moved, 3 tabs renamed, 9 sessions preserved.", out)
        self.assertIn("idempotent re-run ✓", out)
        self.assertIn("Outstanding / Needs Attention", out)

    def test_organize_reads_no_transcripts(self):
        h, *_ = self.organize("w6_before")
        self.assertFalse(any(c[:2] == ["pane", "read"] for c in h.calls))


# --------------------------------------------------------------------------- rename

class RenameTests(Base):
    def test_rename_shared_tab_refused(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "rename", "p5", "API gateway")
        self.assertEqual(code, inv.EXIT_ERR)
        self.assertIn("shared by 2 panes", out)

    def test_rename_single_tab(self):
        h, ctx, cache = self.live("single")
        i = inv.inventory_l1(h, ctx, cache)
        t, _ = inv.resolve_targets(i, ["p1"])
        r = inv.do_rename(h, i, t[0], "Solo - Better Title", cache)
        self.assertEqual(r["result"], "renamed")
        self.assertTrue(r["session_unchanged"])
        self.assertEqual(h.state["tabs"][0]["label"], "SOLO - BETTER TITLE")
        self.assertEqual(h.state["panes"][0]["terminal_title_stripped"], "Solo task")   # session itself untouched
        s = self.sessions(inv.inventory_l1(h, ctx, cache))["p1"]
        self.assertEqual(s["name"], "SOLO - BETTER TITLE")
        self.assertEqual(s["name_source"], "explicit rename")

    def test_explicit_rename_survives_organize(self):
        h, ctx, cache = self.live("single")
        i = inv.inventory_l1(h, ctx, cache)
        inv.do_rename(h, i, inv.resolve_targets(i, ["p1"])[0][0], "Chosen", cache)
        self.assertTrue(inv.plan_organize(inv.inventory_l1(h, ctx, cache), sync_titles=True)["noop"])

    def test_if_questionable_guard(self):
        h, ctx, cache = self.live("single")
        i = inv.inventory_l1(h, ctx, cache)
        r = inv.do_rename(h, i, inv.resolve_targets(i, ["p1"])[0][0], "X", cache, if_questionable=True)
        self.assertEqual(r["result"], "refused")
        self.assertIn("already meaningful", r["reason"])

    def test_only_tab_rename_called(self):
        h, ctx, cache = self.live("single")
        i = inv.inventory_l1(h, ctx, cache)
        inv.do_rename(h, i, inv.resolve_targets(i, ["p1"])[0][0], "New", cache)
        muts = [c for c in h.calls if c[0] in ("pane", "tab", "agent") and c[1] not in ("list", "get", "current", "process-info")]
        self.assertEqual([c[:2] for c in muts], [["tab", "rename"]])


# --------------------------------------------------------------------------- resolve / review

class ReviewTests(Base):
    def test_resolve_forms(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        self.assertEqual(inv.resolve_targets(i, ["p4"])[0][0]["pane"], "p4")
        self.assertEqual(inv.resolve_targets(i, ["w1:p4"])[0][0]["pane"], "p4")
        self.assertEqual(inv.resolve_targets(i, ["webhook"])[0][0]["pane"], "p4")      # unique partial
        self.assertEqual(inv.resolve_targets(i, ["api"])[0][0]["pane"], "p5")          # exact beats partial
        found, errs = inv.resolve_targets(i, ["Release notes"])
        self.assertFalse(found)
        self.assertIn("ambiguous", errs[0])
        self.assertIn("no session matches", inv.resolve_targets(i, ["zzz"])[1][0])

    def test_review_p4_reads_only_p4(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "review", "p4")
        self.assertEqual(code, 0)
        pk = json.loads(out)
        self.assertEqual([s["pane"] for s in pk["sessions"]], ["p4"])
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        inv.review_packet(h, i, inv.resolve_targets(i, ["p4"])[0], "selected")
        self.assertEqual({c[2] for c in h.calls if c[:2] == ["pane", "read"]}, {"w1:p4"})

    def test_review_multiple(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "review", "p4", "p6")
        self.assertEqual([s["pane"] for s in json.loads(out)["sessions"]], ["p4", "p6"])

    def test_review_ambiguous_name_not_guessed(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "review", "Release notes")
        self.assertEqual(code, inv.EXIT_USAGE)
        self.assertIn("ambiguous", out)

    def test_review_unknown_only_questionable(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "review", "--unknown")
        panes = [s["pane"] for s in json.loads(out)["sessions"]]
        self.assertEqual(sorted(panes), ["p1", "p2", "p3", "p4", "p6", "p7"])
        self.assertNotIn("p5", panes)
        self.assertNotIn("p9", panes)        # organizer itself

    def test_unnamed_is_alias(self):
        a = json.loads(self.run_cli("--fixture", fx("mixed"), "review", "--unknown")[1])
        b = json.loads(self.run_cli("--fixture", fx("mixed"), "review", "--unnamed")[1])
        self.assertEqual([s["pane"] for s in a["sessions"]], [s["pane"] for s in b["sessions"]])

    def test_review_unknown_none_needed(self):
        code, out, _ = self.run_cli("--fixture", fx("w6_before"), "review", "--unknown")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), inv.NO_REVIEWS)

    def test_manual_flag_included(self):
        self.run_cli("--fixture", fx("w6_before"), "flag", "p5")
        code, out, _ = self.run_cli("--fixture", fx("w6_before"), "review", "--unknown")
        self.assertEqual([s["pane"] for s in json.loads(out)["sessions"]], ["p5"])
        self.run_cli("--fixture", fx("w6_before"), "unflag", "p5")
        self.assertEqual(self.run_cli("--fixture", fx("w6_before"), "review", "--unknown")[1].strip(), inv.NO_REVIEWS)

    def test_one_shot_flag_option(self):
        code, out, _ = self.run_cli("--fixture", fx("w6_before"), "review", "--unknown", "--flag", "p3")
        self.assertEqual([s["pane"] for s in json.loads(out)["sessions"]], ["p3"])

    def test_review_redacts_secrets(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "review", "p5")
        self.assertNotIn("sk_live_XXXXdummy", out)
        self.assertNotIn("ghp_DUMMYdummy", out)
        self.assertIn("REDACTED", out)

    def test_review_does_not_rename(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        inv.review_packet(h, i, [s for w in i["workspaces"] for s in w["sessions"] if s["questionable"]], "unknown+apply")
        self.assertFalse(any(c[:2] == ["tab", "rename"] for c in h.calls))


# --------------------------------------------------------------------------- level 2: states, status, remaining, watch

class StateTests(Base):
    def ev(self, pane):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        return inv.evidence(h, self.sessions(i)[pane], inv.TAIL_LINES, False)

    def test_states(self):
        self.assertEqual(self.ev("p1")["state"], "Working")           # little output, still working
        self.assertEqual(self.ev("p4")["state"], "Blocked")
        self.assertTrue(self.ev("p4")["needs_me"])
        self.assertEqual(self.ev("p3")["state"], "Error")
        self.assertEqual(self.ev("p2")["state"], "Completed")
        self.assertEqual(self.ev("p6")["state"], "Idle")              # idle is NOT completed
        self.assertEqual(self.ev("p5")["state"], "Unknown")

    def test_confirmation_request_needs_me(self):
        self.assertTrue(inv._ASKS.search("The last step needs one confirmation from you before you merge it."))
        self.assertTrue(inv._ASKS.search("The lesson file is still untracked, waiting on your call above."))
        self.assertFalse(inv._ASKS.search("All nine PRs are green and merged by CI."))

    def test_chrome_and_tool_echo_skipped(self):
        screen = "⏺ Tests pass on both branches.\n  ⎿  Read ../src/x.ts (20 lines)\n  Referenced file ../../a/b.txt\n7 new messages (click) ↓"
        self.assertEqual(inv.last_line(screen), "Tests pass on both branches.")

    def test_question_needs_me(self):
        h, ctx, cache = self.live("w6_before")
        i = inv.inventory_l1(h, ctx, cache)
        e = inv.evidence(h, self.sessions(i)["p9"], inv.TAIL_LINES, False)
        self.assertTrue(e["needs_me"])

    def test_status_header_and_table(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "status")
        self.assertEqual(code, 0)
        self.assertIn("**Workspace: billing**", out)
        self.assertIn("Sessions: 9 | Working: 1 | Blocked: 1 | Needs me: 2", out)
        self.assertIn("| Session | State | Progress / outstanding | Next |", out)
        self.assertLess(len(out.split()), 400)

    def test_status_uses_fresh_cache_and_flags_stale(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        s = self.sessions(i)["p6"]
        e = inv.evidence(h, s, inv.TAIL_LINES, False)
        cache.put_summary(s["session_key"], {"current": "Drafted notes", "outstanding": "Publish notes", "fingerprint": e["fingerprint"]}, s["boundary"])
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "status")
        # fixture runs use their own cache scope 'fixture' in the same cache dir
        self.assertIn("Left: Publish notes", out)
        st = _load(fx("mixed"))
        st["reads"]["term_w10006"] = "⏺ Something new happened."
        path = os.path.join(self.tmp.name, "changed.json")
        _dump(st, path)
        code, out, _ = self.run_cli("--fixture", path, "status")
        self.assertIn("evidence changed since", out)

    def test_remaining_order_and_no_invention(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "remaining")
        pk = json.loads(out)
        g = pk["workspaces"][0]["groups"]
        self.assertEqual(list(g), ["needs_me", "blocked_failed", "active", "idle_unfinished", "completed"])
        self.assertIn("p4", [e["pane"] for e in g["needs_me"]])
        self.assertIn("p3", [e["pane"] for e in g["blocked_failed"]])
        self.assertIn("p6", [e["pane"] for e in g["idle_unfinished"]])
        for grp in g.values():
            for e in grp:
                self.assertIsNone(e["remaining"])                      # nothing cached → nothing invented
                self.assertTrue(e["inspect"])
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "remaining", "--render")
        self.assertIn("Unknown — needs inspection", out)
        self.assertIn("**Needs my approval or decision**", out)

    def test_fresh_cached_needs_me_counts(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))["p6"]
        e = inv.evidence(h, s, inv.TAIL_LINES, False)
        cache.put_summary(s["session_key"], {"needs_me": True, "next": "Merge PR 42", "fingerprint": e["fingerprint"]}, s["boundary"])
        e2 = inv.evidence(h, self.sessions(inv.inventory_l1(h, ctx, cache))["p6"], inv.TAIL_LINES, False)
        self.assertTrue(e2["needs_me"])
        self.assertIn("Merge PR 42", e2["needs_reason"])

    def test_remaining_reuses_fresh_cache(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        s = self.sessions(i)["p6"]
        e = inv.evidence(h, s, inv.TAIL_LINES, False)
        cache.put_summary(s["session_key"], {"outstanding": "Publish the notes", "fingerprint": e["fingerprint"]}, s["boundary"])
        pk = inv.remaining_packet(h, inv.inventory_l1(h, ctx, cache))
        p6 = next(x for x in pk["workspaces"][0]["groups"]["idle_unfinished"] if x["pane"] == "p6")
        self.assertFalse(p6["inspect"])
        self.assertEqual(p6["remaining"], "Publish the notes")

    def test_watch_baseline_then_changes(self):
        code, out, _ = self.run_cli("--fixture", fx("w6_before"), "watch")
        self.assertIn("Baseline snapshot recorded", out)
        st = _load(fx("w6_before"))
        for p in st["panes"]:
            if p["pane_id"] == "w6:p6":
                p["agent_status"] = "blocked"
        path = os.path.join(self.tmp.name, "w6b.json")
        _dump(st, path)
        code, out, _ = self.run_cli("--fixture", path, "watch")
        self.assertIn("newly-blocked", out)
        self.assertNotIn("p5", out.split("newly-blocked")[0])

    def test_watch_quiet_working_is_not_failure(self):
        h, ctx, cache = self.live("mixed")
        inv.watch(h, inv.inventory_l1(h, ctx, cache), cache, 0)
        snap = cache.load_snapshot("w1")
        for v in snap["sessions"].values():
            v["unchanged_since"] = "2020-01-01T00:00:00+00:00"
        cache.save_snapshot("w1", snap)
        res = inv.watch(h, inv.inventory_l1(h, ctx, cache), cache, 30)
        types = {e["type"] for e in res["events"]}
        self.assertIn("quiet-working", types)
        self.assertNotIn("error", {e["type"] for e in res["events"] if e["pane"] == "p1"})


# --------------------------------------------------------------------------- supervisor: ack, identity, watch continuity, card gate

_BANNED_CALLS = {
    ("pane", "send-keys"), ("pane", "send-text"), ("pane", "close"), ("pane", "move"),
    ("tab", "close"), ("tab", "rename"), ("tab", "focus"), ("tab", "create"),
    ("workspace", "create"), ("workspace", "close"),
    ("agent", "prompt"), ("agent", "send-keys"), ("agent", "start"), ("agent", "attach"), ("agent", "focus"),
    ("terminal", "attach"),
}


class SupervisorTests(Base):
    def test_ack_suppresses_needs_me_until_fingerprint_changes(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        s = self.sessions(i)["p4"]
        before = inv.evidence(h, s, inv.TAIL_LINES, False)
        self.assertTrue(before["needs_me_actionable"])
        n = len(h.calls)
        rec = inv.do_ack(h, s, cache)
        self.assertEqual(rec["result"], "acked")
        self.assertEqual(rec["agent_session_id"], s["agent_session_id"])
        self.assertGreater(len(rec["agent_session_id"]), 8)
        self.assertEqual(rec["fingerprint"], before["fingerprint"])
        self.assertTrue(all(tuple(c[:2]) not in _BANNED_CALLS and tuple(c[:3]) not in _BANNED_CALLS for c in h.calls[n:]))
        self.assertTrue(all(c[:2] == ["pane", "read"] for c in h.calls[n:]))
        shell = self.sessions(i)["p8"]
        self.assertEqual(inv.do_ack(h, shell, cache)["result"], "refused")
        with open(os.path.join(cache.root, "acks.json")) as fh:
            blob = fh.read()
        self.assertNotIn("Want me to", blob)
        acked = inv.evidence(h, self.sessions(inv.inventory_l1(h, ctx, cache))["p4"], inv.TAIL_LINES, False)
        self.assertFalse(acked["needs_me"])
        self.assertFalse(acked["needs_me_actionable"])
        self.assertEqual(acked["card_gate"], "human_acked")
        self.assertEqual(acked["human_acked_at"], rec["human_acked_at"])
        st = _load(fx("mixed"))
        st["reads"][s["terminal_id"]] = "⏺ A new question. Want me to deploy the other plan?\n"
        path = os.path.join(self.tmp.name, "changed-ask.json")
        _dump(st, path)
        code, out, _ = self.run_cli("--fixture", path, "status", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        p4 = next(v for v in payload["evidence"].values() if v["pane"] == "p4")
        self.assertTrue(p4["needs_me_actionable"])
        self.assertEqual(p4["card_gate"], "fresh")
        self.assertNotEqual(p4["fingerprint"], before["fingerprint"])

    def test_remember_human_acked_records_ack_but_needs_me_false_does_not(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))["p4"]
        e = inv.evidence(h, s, inv.TAIL_LINES, False)
        code, out, err = self.run_cli("--fixture", fx("mixed"), "remember", "p4", "--summary",
                                      json.dumps({"needs_me": False, "current": "still blocked"}))
        self.assertEqual(code, 0, err)
        self.assertIsNone(cache.get_ack(s["session_key"]))
        still = inv.evidence(h, self.sessions(inv.inventory_l1(h, ctx, cache))["p4"], inv.TAIL_LINES, False)
        self.assertTrue(still["needs_me_actionable"])
        code, out, err = self.run_cli("--fixture", fx("mixed"), "remember", "p4", "--summary",
                                      json.dumps({"needs_me": False, "human_acked": True, "current": "Operator answered in the pane",
                                                  "fingerprint": e["fingerprint"]}))
        self.assertEqual(code, 0, err)
        saved = json.loads(out)
        self.assertNotIn("human_acked", saved)
        self.assertEqual(saved["human_ack"]["fingerprint"], e["fingerprint"])
        suppressed = inv.evidence(h, self.sessions(inv.inventory_l1(h, ctx, cache))["p4"], inv.TAIL_LINES, False)
        self.assertEqual(suppressed["card_gate"], "human_acked")

    def test_stale_cached_needs_me_is_not_actionable(self):
        h, ctx, cache = self.live("mixed")
        s = self.sessions(inv.inventory_l1(h, ctx, cache))["p6"]
        e = inv.evidence(h, s, inv.TAIL_LINES, False)
        cache.put_summary(s["session_key"], {"needs_me": True, "next": "Deploy it", "fingerprint": "0" * 16}, s["boundary"])
        e2 = inv.evidence(h, self.sessions(inv.inventory_l1(h, ctx, cache))["p6"], inv.TAIL_LINES, False)
        self.assertNotEqual(e2["fingerprint"], "0" * 16)
        self.assertFalse(e2["needs_me"])
        self.assertTrue(e2["stale_needs_me"])
        self.assertFalse(e2["needs_me_actionable"])
        self.assertEqual(e2["card_gate"], "stale_fingerprint")
        pk = inv.remaining_packet(h, inv.inventory_l1(h, ctx, cache))
        groups = pk["workspaces"][0]["groups"]
        self.assertNotIn("p6", [x["pane"] for x in groups["needs_me"]])
        self.assertIn("p6", [x["pane"] for x in groups["idle_unfinished"]])
        self.assertIn("fresh status --json", pk["card_gate_rule"])
        self.assertEqual(e["fingerprint"], e2["fingerprint"])

    def test_card_gate_cases(self):
        acked = {"needs_me": True, "needs_reason": "ask", "state": "Blocked", "fingerprint": "abc", "read_error": None}
        inv.apply_card_gate(acked, {"human_ack": {"fingerprint": "abc", "human_acked_at": "2026-01-01T00:00:00+00:00"}, "cached_summary": None})
        self.assertFalse(acked["needs_me"])
        self.assertEqual(acked["card_gate"], "human_acked")
        self.assertEqual(acked["suppressed_needs_reason"], "ask")
        fresh = {"needs_me": True, "needs_reason": "ask", "state": "Idle", "fingerprint": "new", "read_error": None}
        inv.apply_card_gate(fresh, {"human_ack": {"fingerprint": "old", "human_acked_at": "2026-01-01T00:00:00+00:00"}})
        self.assertTrue(fresh["needs_me_actionable"])
        self.assertEqual(fresh["card_gate"], "fresh")
        unread = {"needs_me": True, "needs_reason": "ask", "state": "Blocked", "fingerprint": None, "read_error": "boom"}
        inv.apply_card_gate(unread, {})
        self.assertFalse(unread["needs_me_actionable"])
        self.assertEqual(unread["card_gate"], "stale_fingerprint")

    def test_identity_contract_json_and_prose(self):
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "status", "--json")
        payload = json.loads(out)
        self.assertIn("card_gate_rule", payload)
        p4 = next(v for v in payload["evidence"].values() if v["pane"] == "p4")
        sid = p4["agent_session_id"]
        self.assertGreater(len(sid), 8)
        for key in ("agent_session_id", "agent_name", "cwd", "pane_id", "tab_id", "workspace_id", "terminal_id"):
            self.assertTrue(p4.get(key), key)
        self.assertEqual(p4["open_hints"]["do_not_run"], True)
        self.assertEqual(p4["open_hints"]["herdr_focus"], f"herdr tab focus {p4['tab_id']}")
        self.assertEqual(p4["open_hints"]["resume_command"], f"claude --resume {sid}")
        self.assertEqual(p4["open_hints"]["resume_uri"], f"vscode://anthropic.claude-code/open?session={sid}")
        self.assertEqual(p4["open_hints"]["herdr_attach"], f"herdr agent attach {p4['pane_id']}")
        code, prose, _ = self.run_cli("--fixture", fx("mixed"), "status")
        self.assertIn(sid, prose)
        self.assertNotRegex(prose, re.escape(sid[:8]) + "`")
        self.assertIn(p4["cwd"], prose)
        self.assertIn(p4["pane_id"], prose)
        listed = json.loads(self.run_cli("--fixture", fx("mixed"), "list", "--json")[1])
        row = next(s for w in listed["workspaces"] for s in w["sessions"] if s["pane"] == "p2")
        self.assertEqual(row["agent_session_id"], row["open_hints"]["codex_thread_open"].rsplit("/", 1)[-1])
        self.assertNotIn("resume_command", row["open_hints"])
        hostile = inv.open_hints({"tab_id": "w1:t1; rm -rf /", "pane_id": "w1:p4; touch /tmp/x", "terminal_id": "term ok",
                                  "agent_kind": "claude", "agent_session_id": "0000w1-0004-4000-8000-000000000004"})
        self.assertNotIn("herdr_focus", hostile)
        self.assertNotIn("herdr_attach", hostile)
        self.assertNotIn(";", json.dumps(hostile))
        self.assertEqual(hostile["resume_command"], "claude --resume 0000w1-0004-4000-8000-000000000004")

    def test_open_gui_prose_keeps_full_session_id_and_does_not_run_hints(self):
        proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(proj)
        st = _load(fx("mixed"))
        for p in st["panes"]:
            p["cwd"] = p["foreground_cwd"] = proj
        path = os.path.join(self.tmp.name, "vs.json")
        _dump(st, path)
        os.environ["HSM_DRY_LAUNCH"] = "1"
        os.environ["HSM_CODE_BIN"] = "/opt/code"
        h = inv.FixtureHerdr(path)
        ctx = inv.verify_context(h)
        s = self.sessions(inv.inventory_l1(h, ctx, inv.Cache(self.cache_dir, "g")))["p4"]
        n = len(h.calls)
        r = inv.open_gui(h, s, open_session=False)
        text = inv.render_gui(r)
        sid = s["agent_session_id"]
        self.assertIn(f"`{sid}`", text)
        self.assertNotRegex(text, re.escape(sid[:8]) + "`")
        self.assertIn(f"claude --resume {sid}", text)
        self.assertIn("not run", text)
        self.assertIn("not executed", text)
        self.assertTrue(r["open_hints"]["do_not_run"])
        self.assertTrue(r["capabilities"]["open_session"].startswith("not attempted"))
        self.assertFalse(any(tuple(c[:2]) in _BANNED_CALLS for c in h.calls[n:]))
        self.assertFalse(any("resume" in " ".join(c) or "attach" in " ".join(c) for c in h.calls[n:]))

    def test_watch_respawn_keeps_agent_session(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        first = inv.watch(h, i, cache, 45)
        self.assertEqual(first["events"], [])
        s = self.sessions(i)["p4"]
        snap = cache.load_snapshot("w1")
        self.assertIn(s["session_key"], snap["sessions"])
        self.assertNotIn(s["terminal_id"], snap["sessions"])
        pane = next(p for p in h.state["panes"] if p["pane_id"] == s["pane_id"])
        new_term = s["terminal_id"] + "-respawned"
        h.state["reads"][new_term] = h.state["reads"].pop(s["terminal_id"])
        pane["terminal_id"] = new_term
        res = inv.watch(h, inv.inventory_l1(h, ctx, cache), cache, 45)
        mine = [e for e in res["events"] if e.get("agent_session_id") == s["agent_session_id"]]
        types = [e["type"] for e in mine]
        self.assertIn("respawned", types)
        self.assertNotIn("pane-gone", types)
        self.assertNotIn("new-session", types)
        self.assertIn(s["agent_session_id"], mine[0]["message"])
        self.assertEqual(mine[0]["terminal_id"], new_term)
        self.assertEqual(mine[0]["pane_id"], s["pane_id"])

    def test_ack_survives_respawn_until_evidence_changes(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        s = self.sessions(i)["p4"]
        inv.watch(h, i, cache, 45)
        self.assertEqual(inv.do_ack(h, s, cache)["result"], "acked")
        pane = next(p for p in h.state["panes"] if p["pane_id"] == s["pane_id"])
        new_term = s["terminal_id"] + "-respawned"
        h.state["reads"][new_term] = h.state["reads"].pop(s["terminal_id"])
        pane["terminal_id"] = new_term
        res = inv.watch(h, inv.inventory_l1(h, ctx, cache), cache, 45)
        types = {e["type"] for e in res["events"] if e.get("agent_session_id") == s["agent_session_id"]}
        self.assertIn("respawned", types)
        self.assertNotIn("needs-me", types)
        self.assertNotIn("newly-blocked", types)
        e = inv.evidence(h, self.sessions(inv.inventory_l1(h, ctx, cache))["p4"], inv.TAIL_LINES, False)
        self.assertEqual(e["card_gate"], "human_acked")
        self.assertEqual(e["terminal_id"], new_term)

    def test_watch_legacy_terminal_key_is_not_a_new_session(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        s = self.sessions(i)["p6"]
        e = inv.evidence(h, s, inv.TAIL_LINES, False)
        now = inv._now()
        cache.save_snapshot("w1", {"saved_at": now, "workspace_id": "w1", "sessions": {
            s["terminal_id"]: {"pane_id": s["pane_id"], "state": e["state"], "needs_me": e["needs_me"],
                               "fingerprint": e["fingerprint"], "agent_kind": s["agent_kind"],
                               "agent_name": s["agent_name"], "seen_at": now, "unchanged_since": now}}})
        res = inv.watch(h, i, cache, 45)
        types = {ev["type"] for ev in res["events"] if ev.get("pane") == "p6" or ev.get("pane_id") == s["pane_id"]}
        self.assertNotIn("new-session", types)
        self.assertNotIn("pane-gone", types)

    def test_watch_new_session_in_same_terminal_is_not_pane_gone(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        s = self.sessions(i)["p4"]
        inv.watch(h, i, cache, 45)
        pane = next(p for p in h.state["panes"] if p["pane_id"] == s["pane_id"])
        pane["agent_session"]["value"] = "0000w1-9999-4000-8000-000000009999"
        res = inv.watch(h, inv.inventory_l1(h, ctx, cache), cache, 45)
        mine = [e for e in res["events"] if e.get("pane_id") == s["pane_id"]]
        types = [e["type"] for e in mine]
        self.assertIn("new-session", types)
        self.assertNotIn("pane-gone", types)
        self.assertEqual(mine[0]["agent_session_id"], pane["agent_session"]["value"])
        self.assertIn(s["agent_session_id"], mine[0]["message"])


# --------------------------------------------------------------------------- cache / boundaries / safety

class CacheTests(Base):
    def test_boundary_assignment(self):
        rules = [{"prefix": "/work/employer/", "boundary": "employer"}, {"prefix": "/work/", "boundary": "firm"}]
        self.assertEqual(inv.boundary_for("/work/employer/x", rules), "employer")
        self.assertEqual(inv.boundary_for("/work/other", rules), "firm")
        self.assertEqual(inv.boundary_for("/elsewhere", rules), "unassigned")

    def test_unassigned_never_persisted(self):
        c = inv.Cache(self.cache_dir, "t")
        with self.assertRaises(inv.HerdrError):
            c.put_summary("agent:x", {"goal": "g"}, "unassigned")

    def test_partitioned_per_boundary(self):
        c = inv.Cache(self.cache_dir, "t")
        c.put_summary("agent:a", {"goal": "employer thing"}, "employer")
        c.put_summary("agent:b", {"goal": "firm thing"}, "firm")
        files = sorted(os.listdir(c.root))
        self.assertIn("summaries-employer.json", files)
        self.assertIn("summaries-firm.json", files)
        self.assertNotIn("agent:b", _load(os.path.join(c.root, "summaries-employer.json")))

    def test_summary_redacted_and_whitelisted(self):
        c = inv.Cache(self.cache_dir, "t")
        out = c.put_summary("agent:a", {"goal": "use token ghp_DUMMYdummyDUMMYdummy00", "raw_output": "x" * 50}, "firm")
        self.assertNotIn("ghp_", out["goal"])
        self.assertNotIn("raw_output", out)

    def test_retention_expires(self):
        c = inv.Cache(self.cache_dir, "t")
        c.put_summary("agent:a", {"goal": "old"}, "firm")
        path = os.path.join(c.root, "summaries-firm.json")
        data = _load(path)
        data["agent:a"]["updated_at"] = "2020-01-01T00:00:00+00:00"
        _dump(data, path)
        inv.Cache(self.cache_dir, "t")
        self.assertEqual(_load(path), {})

    def test_purge(self):
        c = inv.Cache(self.cache_dir, "t")
        c.put_summary("agent:a", {"goal": "x"}, "firm")
        self.assertTrue(c.purge())
        self.assertEqual(os.listdir(c.root), [])

    def test_cache_files_private(self):
        c = inv.Cache(self.cache_dir, "t")
        c.put_summary("agent:a", {"goal": "x"}, "firm")
        mode = os.stat(os.path.join(c.root, "summaries-firm.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_snapshot_has_no_text(self):
        self.run_cli("--fixture", fx("mixed"), "watch")
        snap = _load(os.path.join(self.cache_dir, "fixture", "snapshot-w1.json"))
        blob = json.dumps(snap)
        self.assertNotIn("migration plan", blob)
        self.assertNotIn("sk_live", blob)

    def test_partial_pem_and_base64_and_quoted_keys(self):
        b64 = "\n".join(["QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2"] * 4)
        for text in ("-----BEGIN RSA PRIVATE KEY-----\n" + b64,                 # END cut off
                     b64 + "\n-----END PRIVATE KEY-----\nafter",             # BEGIN above window
                     "prefix\n" + b64 + "\nsuffix"):                          # bare base64 run
            out = inv.redact(text)
            self.assertNotIn("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVph", out, text[:30])
        out = inv.redact('{"API_KEY": "abc123def456", \'client_secret\': \'zzz999\', "authToken": "t0k3n"}')
        for leak in ("abc123def456", "zzz999", "t0k3n"):
            self.assertNotIn(leak, out)

    def test_titles_redacted(self):
        st = _load(fx("single"))
        st["panes"][0]["terminal_title_stripped"] = "export GITHUB_TOKEN=ghp_DUMMYdummyDUMMYdummy00"
        path = os.path.join(self.tmp.name, "t.json")
        _dump(st, path)
        code, out, _ = self.run_cli("--fixture", path, "list")
        self.assertNotIn("ghp_DUMMY", out)

    def test_plain_shell_panes_never_read(self):
        h, ctx, cache = self.live("mixed")
        i = inv.inventory_l1(h, ctx, cache)
        for s in [x for w in i["workspaces"] for x in w["sessions"]]:
            inv.evidence(h, s, inv.TAIL_LINES, False)
        self.assertNotIn("w1:p8", {c[2] for c in h.calls if c[:2] == ["pane", "read"]})
        code, out, _ = self.run_cli("--fixture", fx("mixed"), "watch")
        self.assertEqual(code, 0)

    def test_unsent_draft_never_in_evidence(self):
        screen = ("⏺ Finished the audit; two items remain.\n\n✻ Baked for 1m 2s · done 9:00 AM\n"
                  + "─" * 40 + "\n❯\u00a0ship the secret plan to prod\n" + "─" * 40 + "\n  ⏵⏵ auto mode on")
        self.assertNotIn("secret plan", "\n".join(inv.meaningful_lines(screen)))
        self.assertEqual(inv.last_line(screen), "Finished the audit; two items remain.")
        self.assertEqual(inv.fingerprint(screen), inv.fingerprint(screen.replace("ship the secret plan to prod", "different draft")))

    def test_redaction_patterns(self):
        s = inv.redact("AKIA" + "DUMMYDUMMYDUMMY0" + " x API_KEY=abc123 Bearer abcdefghijklmnopqrstu https://u:pw@host/x sk-ant-abcdefghijklmnopqrstu")
        for leak in ("AKIA" + "DUMMYDUMMYDUMMY0", "abc123", "abcdefghijklmnopqrstu", ":pw@"):
            self.assertNotIn(leak, s)


# --------------------------------------------------------------------------- VS Code: open-project / open-gui

class VSCodeTests(Base):
    def setUp(self):
        super().setUp()
        self.proj = os.path.join(self.tmp.name, "My Project")          # path with a space
        os.makedirs(self.proj)
        st = _load(fx("mixed"))
        for p in st["panes"]:
            p["cwd"] = p["foreground_cwd"] = self.proj
        self.path = os.path.join(self.tmp.name, "vs.json")
        _dump(st, self.path)
        os.environ["HSM_DRY_LAUNCH"] = "1"
        os.environ["HSM_CODE_BIN"] = "/opt/code bin/code"
        self._live = (inv.claude_session_live, inv.codex_session_live)

    def tearDown(self):
        inv.claude_session_live, inv.codex_session_live = self._live
        super().tearDown()

    def target(self, pane):
        h = inv.FixtureHerdr(self.path)
        ctx = inv.verify_context(h)
        i = inv.inventory_l1(h, ctx, inv.Cache(self.cache_dir, "fixture"))
        return h, inv.resolve_targets(i, [pane])[0][0]

    def test_open_project_argv_spaces_and_no_reuse_flag(self):
        h, s = self.target("p4")
        r = inv.open_project(h, s)
        self.assertEqual(r["argv"], ["/opt/code bin/code", self.proj])
        self.assertNotIn("-r", r["argv"])
        self.assertFalse(any(c[:2] in (["pane", "move"], ["tab", "rename"]) for c in h.calls))

    def test_open_project_default_is_self(self):
        code, out, _ = self.run_cli("--fixture", self.path, "open-project", "--json")
        self.assertEqual(json.loads(out)["pane"], "p9")

    def test_open_project_missing_dir(self):
        st = _load(self.path)
        st["panes"][0]["cwd"] = st["panes"][0]["foreground_cwd"] = "/nonexistent/dir"
        _dump(st, self.path)
        code, out, err = self.run_cli("--fixture", self.path, "open-project", "p1")
        self.assertEqual(code, inv.EXIT_ERR)
        self.assertIn("not an accessible directory", err)

    def test_open_gui_claude_live_refuses_session(self):
        inv.claude_session_live = lambda sid, d=None: True
        h, s = self.target("p4")
        r = inv.open_gui(h, s, open_session=True)
        self.assertTrue(r["capabilities"]["open_session"].startswith("refused"))
        self.assertIn("unsupported", r["capabilities"]["read_only_history"])
        self.assertIn("unsupported", r["capabilities"]["attach_live"])
        self.assertEqual(r["capabilities"]["project_folder"], "dry-run")

    def test_open_gui_claude_not_live_requires_flag(self):
        inv.claude_session_live = lambda sid, d=None: False
        h, s = self.target("p4")
        self.assertTrue(inv.open_gui(h, s, open_session=False)["capabilities"]["open_session"].startswith("not attempted"))
        r = inv.open_gui(h, s, open_session=True)
        self.assertIn("vscode://anthropic.claude-code/open?session=", r["capabilities"]["open_session"])

    def test_open_gui_codex_live_is_read_only_fallback(self):
        inv.codex_session_live = lambda sid, d=None: True
        h, s = self.target("p2")
        r = inv.open_gui(h, s, open_session=False)
        self.assertIn("read-only", r["capabilities"]["read_only_history"])

    def test_claude_liveness_reads_only_json(self):
        d = os.path.join(self.tmp.name, "sessions")
        os.makedirs(d)
        _dump({"pid": os.getpid(), "sessionId": "abc-123", "status": "busy"}, os.path.join(d, "1.json"))
        with open(os.path.join(d, "1.secret.key"), "w") as fh:
            fh.write("not json")
        self.assertTrue(inv.claude_session_live("abc-123", d))
        _dump({"pid": 999999, "sessionId": "dead-1"}, os.path.join(d, "2.json"))
        self.assertFalse(inv.claude_session_live("dead-1", d))


# --------------------------------------------------------------------------- files

class FilesTests(Base):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp.name, "repo with space")
        os.makedirs(self.root)
        os.environ["HSM_YAZI_BIN"] = "/bin/echo"            # never launch a real TUI in tests
        os.environ["HSM_SKIP_YAZI_CHECK"] = "1"

    def run_files(self, persistent, h=None):
        h = h or inv.FixtureHerdr(fx("w6_before"))
        ctx = inv.verify_context(h)
        cache = inv.Cache(self.cache_dir, "fixture")
        i = inv.inventory_l1(h, ctx, cache)
        return h, inv.files(h, ctx, i, self.root, persistent, wait=0.2, cache=cache)

    def test_opens_own_tab_in_current_workspace(self):
        h, r = self.run_files(False)
        self.assertEqual(r["result"], "opened")
        create = next(c for c in h.calls if c[:2] == ["tab", "create"])
        self.assertEqual(create[create.index("--workspace") + 1], "w6")
        self.assertEqual(create[create.index("--cwd") + 1], self.root)
        ran = h.state["ran"]
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0][0], r["pane_id"])                          # only in the pane it created
        self.assertIn("YAZI_CONFIG_HOME=", ran[0][1])
        self.assertIn("'" + self.root + "'", ran[0][1])                    # quoted path with a space
        self.assertTrue(ran[0][1].endswith("; exit"))                      # transient tab closes itself

    def test_persistent_reuses_single_files_tab(self):
        h, r1 = self.run_files(True)
        self.assertEqual(r1["result"], "opened")
        self.assertFalse(h.state["ran"][0][1].endswith("; exit"))
        h, r2 = self.run_files(True, h)
        self.assertEqual(r2["result"], "reused")
        self.assertEqual(sum(1 for t in h.state["tabs"] if t["label"] == "FILES"), 1)
        self.assertEqual(len(h.state["ran"]), 1)

    def test_persistent_restarts_when_shell_idle(self):
        h, r1 = self.run_files(True)
        info = h.state["process_info"][next(p["terminal_id"] for p in h.state["panes"] if p["pane_id"] == r1["pane_id"])]
        info["foreground_processes"] = info["foreground_processes"][:1]
        info["foreground_process_group_id"] = info["shell_pid"]
        h, r2 = self.run_files(True, h)
        self.assertEqual(r2["result"], "restarted")

    def test_never_runs_in_foreign_files_tab(self):
        h = inv.FixtureHerdr(fx("single"))
        h.state["tabs"].append({"tab_id": "w3:t2", "workspace_id": "w3", "label": "Files", "number": 2, "pane_count": 1, "focused": False})
        h.state["panes"].append({"pane_id": "w3:p2", "tab_id": "w3:t2", "workspace_id": "w3", "terminal_id": "term_user",
                                 "cwd": self.root, "foreground_cwd": self.root, "terminal_title_stripped": "", "agent_status": "unknown"})
        h.state["process_info"]["term_user"] = {"shell_pid": 7, "foreground_process_group_id": 7, "foreground_processes": [{"pid": 7, "argv0": "-zsh"}]}
        h, r = self.run_files(True, h)
        self.assertEqual(r["result"], "refused")
        self.assertIn("not created by this skill", r["reason"])
        self.assertNotIn("ran", h.state)

    def test_never_runs_in_agent_pane(self):
        h = inv.FixtureHerdr(fx("w6_before"))
        for t in h.state["tabs"]:
            if t["tab_id"] == "w6:t3":
                t["label"] = "Files"                                       # a tab named Files that holds an agent
        h, r = self.run_files(True, h)
        self.assertEqual(r["result"], "refused")
        self.assertNotIn("ran", h.state)

    def test_no_transcript_reads(self):
        h, _ = self.run_files(True)
        self.assertFalse(any(c[:2] == ["pane", "read"] for c in h.calls))

    def test_broken_guard_config_refuses_launch(self):
        os.environ.pop("HSM_SKIP_YAZI_CHECK")
        orig = inv.yazi_config_ok
        inv.yazi_config_ok = lambda: (False, "Yazi config failed to parse — the sensitive-preview guard would be dropped")
        try:
            h, r = self.run_files(True)
        finally:
            inv.yazi_config_ok = orig
        self.assertEqual(r["result"], "refused")
        self.assertFalse(any(c[:2] == ["tab", "create"] for c in h.calls))

    def test_sensitive_previews_disabled_in_config(self):
        with open(os.path.join(SKILL, "config", "yazi", "yazi.toml")) as fh:
            cfg = fh.read()
        self.assertRegex(cfg, r"show_hidden\s*=\s*true")
        for pat in (".env", "*.pem", "id_rsa", "credentials"):
            self.assertIn(pat, cfg)


# --------------------------------------------------------------------------- installation (both agents, hermetic)

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, HSM_CLAUDE_HOME=os.path.join(self.t.name, "claude"),
                        HSM_AGENTS_SKILLS=os.path.join(self.t.name, "agents"))
        self.claude = os.path.join(self.t.name, "claude", "skills")
        other = os.path.join(self.claude, "other-skill")
        os.makedirs(other)
        with open(os.path.join(other, "SKILL.md"), "w") as fh:
            fh.write("---\nname: other-skill\ndescription: unrelated skill that must survive\n---\n")
        self.link = os.path.join(self.t.name, "agents", "herdr-session-manager")

    def tearDown(self):
        self.t.cleanup()

    def sh(self, *args):
        return subprocess.run([os.path.join(SKILL, "scripts", "install.sh"), *args], env=self.env,
                              capture_output=True, text=True)

    def test_install_both_idempotent_and_uninstall(self):
        r = self.sh()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.claude, "herdr-session-manager", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.claude, "other-skill", "SKILL.md")))
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(SKILL))
        self.assertFalse(any("__pycache__" in d for d, _, _ in os.walk(os.path.join(self.claude, "herdr-session-manager"))))
        r2 = self.sh()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("no changes", r2.stdout)
        self.assertIn("already installed", r2.stdout)
        r3 = self.sh("--uninstall")
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.claude, "herdr-session-manager")))
        self.assertTrue(os.path.isfile(os.path.join(self.claude, "other-skill", "SKILL.md")))   # unrelated skill kept
        self.assertFalse(os.path.lexists(self.link))
        self.assertTrue(os.path.isfile(os.path.join(SKILL, "SKILL.md")))                       # checkout untouched

    def test_refuses_foreign_claude_entry(self):
        os.makedirs(os.path.join(self.claude, "herdr-session-manager"))
        r = self.sh("--claude-only")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to overwrite", r.stderr)

    def test_refuses_foreign_codex_entry(self):
        os.makedirs(self.link)
        r = self.sh("--codex-only")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to overwrite", r.stderr)

    def test_dry_run_writes_nothing(self):
        r = self.sh("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.claude, "herdr-session-manager")))
        self.assertFalse(os.path.lexists(self.link))


if __name__ == "__main__":
    unittest.main()
