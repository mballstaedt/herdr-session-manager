import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import contextlib
import io
scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts if scripts.is_dir() else Path(__file__).resolve().parent))
import self_space
import inventory

THREAD = '00000000-0000-4000-8000-000000000002'
SOCKET = '/fixture/confirmed.sock'

class Failure(Exception):
    def __init__(self, code):
        self.payload = {'error': {'code': code}}

class Herdr:
    def __init__(self):
        self.env = {'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w6:p3', 'CODEX_THREAD_ID': THREAD}
        self.calls = []
        self.pane = {'pane_id': 'wE:p7', 'tab_id': 'wE:t6', 'workspace_id': 'wE', 'terminal_id': 'term_123abc', 'cwd': '/fixture/project', 'agent': 'codex', 'agent_session': {'agent': 'codex', 'kind': 'id', 'source': 'herdr:codex', 'value': THREAD}}
        self.error = 'pane_not_found'
        self.extra = []
        self.socket = SOCKET
        self.change_direct = None
        self.bad_move = None
        self.status_calls = 0
        self.switch_endpoint = False
        self.move_error = False

    def call(self, *args):
        self.calls.append(args)
        if args[:1] == ('status',):
            self.status_calls += 1
            return {'server': {'running': True, 'compatible': True, 'endpoint_compatible': True, 'socket': '/wrong' if self.switch_endpoint and self.status_calls > 1 else self.socket}}
        if args[:2] == ('tab', 'list'):
            return {'result': {'tabs': [{'tab_id': self.pane['tab_id'], 'pane_count': 1}]}}
        if args[:2] == ('pane', 'current'):
            if self.error:
                raise Failure(self.error)
            return {'result': {'type': 'pane_current', 'pane': copy.deepcopy(self.pane)}}
        if args[:2] == ('pane', 'list'):
            return {'result': {'type': 'pane_list', 'panes': [copy.deepcopy(self.pane), *copy.deepcopy(self.extra)]}}
        if args[:2] == ('pane', 'get'):
            p = copy.deepcopy(self.pane)
            if self.change_direct:
                p.update(self.change_direct)
            return {'result': {'type': 'pane_get', 'pane': p}}
        if args[:2] == ('pane', 'move'):
            if self.move_error:
                raise TimeoutError()
            before = copy.deepcopy(self.pane)
            self.pane.update(pane_id='wJ:p1', tab_id='wJ:t1', workspace_id='wJ')
            if self.bad_move:
                self.pane.update(self.bad_move)
            return {'result': {'move_result': {'changed': True, 'pane': copy.deepcopy(self.pane),
                'previous_pane_id': before['pane_id'], 'previous_tab_id': before['tab_id'], 'previous_workspace_id': before['workspace_id'],
                'created_workspace': {'workspace_id': 'wJ', 'pane_count': 1, 'tab_count': 1},
                'created_tab': {'workspace_id': 'wJ', 'tab_id': 'wJ:t1', 'pane_count': 1}}}}
        raise AssertionError(args)

class SelfSpaceTests(unittest.TestCase):
    def test_stale_alias_reconciles_exact_host_thread_without_env_change(self):
        h = Herdr(); env = dict(h.env)
        r = self_space.resolve_self(h, SOCKET)
        self.assertTrue(r['verified'])
        self.assertEqual(r['method'], 'exact_conversation_reconciliation')
        self.assertEqual(r['identity']['pane_id'], 'wE:p7')
        self.assertEqual(h.env, env)
        self.assertFalse(any(a[:2] == ('pane', 'move') for a in h.calls))
        self.assertTrue(all(a[0] in ('status', 'pane') for a in h.calls))

    def test_wrong_or_missing_endpoint_prevents_fallback_inventory(self):
        for expected in (None, '/wrong'):
            h = Herdr()
            with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, expected)
            self.assertNotIn(('pane', 'list'), h.calls)

    def test_missing_host_identity_and_outside_context_do_not_call(self):
        for key in ('CODEX_THREAD_ID', 'HERDR_ENV'):
            h = Herdr(); del h.env[key]
            with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, SOCKET)
            self.assertEqual(h.calls, [])

    def test_non_stale_error_never_falls_back(self):
        for error in ('access_denied', 'timeout', 'unavailable'):
            h = Herdr(); h.error = error
            with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, SOCKET)
            self.assertNotIn(('pane', 'list'), h.calls)

    def test_missing_duplicate_and_untrusted_session_source_refused(self):
        for mode in ('missing', 'duplicate', 'source'):
            h = Herdr()
            if mode == 'missing': h.pane['agent_session']['value'] = 'other'
            if mode == 'source': h.pane['agent_session']['source'] = 'title'
            if mode == 'duplicate': h.extra = [copy.deepcopy(h.pane)]
            with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, SOCKET)
            self.assertFalse(any(a[:2] == ('pane', 'move') for a in h.calls))

    def test_live_caller_must_agree_with_host_thread(self):
        h = Herdr(); h.error = None
        self.assertEqual(self_space.resolve_self(h)['method'], 'current_pane')
        h.pane['agent_session']['value'] = 'other'
        with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, SOCKET)

    def test_racing_pane_or_endpoint_refused(self):
        for key, value in (('terminal_id', 'term_replaced'), ('cwd', '/changed'), ('pane_id', 'wE:p8')):
            h = Herdr(); h.change_direct = {key: value}
            with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, SOCKET)
        h = Herdr(); h.switch_endpoint = True
        with self.assertRaises(self_space.IdentityError): self_space.resolve_self(h, SOCKET)

    def test_preview_never_moves_apply_rechecks_and_preserves_identity(self):
        h = Herdr()
        preview = self_space.move_self(h, 'Personal instructions', SOCKET)
        self.assertFalse(preview['applied'])
        self.assertFalse(any(a[:2] == ('pane', 'move') for a in h.calls))
        h.calls.clear()
        done = self_space.move_self(h, 'Personal instructions', SOCKET, apply=True)
        self.assertTrue(done['applied']); self.assertTrue(done['verified'])
        self.assertEqual(done['identity']['pane_id'], 'wJ:p1')
        for key in ('agent_session_id', 'terminal_id', 'cwd'):
            self.assertEqual(done['identity'][key], preview['identity'][key])
        moves = [a for a in h.calls if a[:2] == ('pane', 'move')]
        self.assertEqual(moves, [('pane', 'move', 'wE:p7', '--new-workspace', '--label', 'PERSONAL INSTRUCTIONS', '--tab-label', 'PERSONAL INSTRUCTIONS', '--no-focus')])

    def test_changed_terminal_or_session_after_move_stops_without_retry(self):
        for change in ({'terminal_id': 'term_changed'}, {'cwd': '/changed'}, {'agent_session': {'agent': 'codex', 'kind': 'id', 'source': 'herdr:codex', 'value': 'other'}}):
            h = Herdr(); h.bad_move = change
            r = self_space.move_self(h, 'Personal instructions', SOCKET, apply=True)
            self.assertTrue(r['applied']); self.assertFalse(r['verified'])
            self.assertEqual(sum(a[:2] == ('pane', 'move') for a in h.calls), 1)
            self.assertIn('Do not retry', r['next_action'])

    def test_timeout_does_not_retry_and_bad_label_does_not_inspect(self):
        h = Herdr(); h.move_error = True
        with self.assertRaises(self_space.IdentityError): self_space.move_self(h, 'Own space', SOCKET, apply=True)
        self.assertEqual(sum(a[:2] == ('pane', 'move') for a in h.calls), 1)
        h = Herdr()
        with self.assertRaises(self_space.IdentityError): self_space.move_self(h, 'bad\nlabel', SOCKET)
        self.assertEqual(h.calls, [])

class ReviewRegressionTests(unittest.TestCase):
    def test_operator_captured_envelopes_and_receipt(self):
        fixture = json.loads((Path(__file__).resolve().parent / "fixtures/self-space-cli-2026-11-12.json").read_text())
        h = Herdr()
        h.pane = copy.deepcopy(fixture['pane_list']['result']['panes'][0])
        h.env['CODEX_THREAD_ID'] = h.pane['agent_session']['value']
        call = h.call
        moved = False
        def captured(*args):
            nonlocal moved
            if args[:2] == ('pane', 'list') and not moved:
                h.calls.append(args)
                return copy.deepcopy(fixture['pane_list'])
            if args[:2] == ('pane', 'move'):
                h.calls.append(args)
                moved = True
                h.pane = copy.deepcopy(fixture['move']['result']['move_result']['pane'])
                return copy.deepcopy(fixture['move'])
            return call(*args)
        h.call = captured
        result = self_space.move_self(h, 'SHARED WORK STUDIO', SOCKET, apply=True)
        self.assertTrue(result['verified'])
        self.assertEqual(result['identity']['terminal_id'], 'term_0000000000000a1')

    def test_terminal_handle_is_opaque_not_prefix_dependent(self):
        h = Herdr(); h.pane['terminal_id'] = 'term65fdc739d574310'
        self.assertTrue(self_space.resolve_self(h, SOCKET)['verified'])

    def test_replacement_after_resolution_before_dispatch_aborts_without_move(self):
        h = Herdr(); original = self_space.resolve_self
        def resolve_then_replace(*args):
            result = original(*args)
            h.pane['agent_session']['value'] = 'other-session'
            return result
        with patch.object(self_space, 'resolve_self', side_effect=resolve_then_replace):
            with self.assertRaisesRegex(self_space.IdentityError, 'before move dispatch'):
                self_space.move_self(h, 'OWN SPACE', SOCKET, apply=True)
        self.assertFalse(any(a[:2] == ('pane', 'move') for a in h.calls))

    def test_malformed_inventory_direct_identity_and_conflicting_current(self):
        for mode in ('wrong_type', 'missing_type', 'non_list', 'direct_session', 'current_session', 'bad_terminal'):
            h = Herdr(); call = h.call
            if mode == 'current_session': h.error = None
            def altered(*args):
                result = call(*args)
                if args[:2] == ('pane', 'list'):
                    if mode == 'wrong_type': result['result']['type'] = 'agent_list'
                    if mode == 'missing_type': del result['result']['type']
                    if mode == 'non_list': result['result']['panes'] = {}
                    if mode == 'bad_terminal': result['result']['panes'][0]['terminal_id'] = 'bad handle\n'
                if args[:2] == ('pane', 'get') and mode == 'direct_session':
                    result['result']['pane']['agent_session']['value'] = 'other-session'
                if args[:2] == ('pane', 'current') and mode == 'current_session':
                    result['result']['pane']['agent_session']['value'] = 'other-session'
                return result
            h.call = altered
            with self.subTest(mode=mode), self.assertRaises(self_space.IdentityError):
                self_space.move_self(h, 'OWN SPACE', SOCKET, apply=True)
            self.assertFalse(any(a[:2] == ('pane', 'move') for a in h.calls))

    def test_receipt_mismatch_and_extra_live_destination_are_unverified_no_retry(self):
        for mode in ('source', 'workspace', 'pane_count', 'tabs', 'extra_pane', 'extra_tab', 'missing_receipt', 'not_changed'):
            h = Herdr(); call = h.call
            def altered(*args):
                result = call(*args)
                if args[:2] == ('pane', 'move'):
                    receipt = result['result']['move_result']
                    if mode == 'source': receipt['previous_pane_id'] = 'wE:p8'
                    if mode == 'workspace': receipt['created_workspace']['workspace_id'] = 'wX'
                    if mode == 'pane_count': receipt['created_workspace']['pane_count'] = 2
                    if mode == 'tabs': receipt['created_workspace']['tab_count'] = 2
                    if mode == 'missing_receipt': del result['result']['move_result']
                    if mode == 'not_changed': receipt['changed'] = False
                if args[:2] == ('pane', 'list') and '--workspace' in args and mode == 'extra_pane':
                    result['result']['panes'].append(copy.deepcopy(h.pane))
                if args[:2] == ('tab', 'list') and mode == 'extra_tab':
                    result['result']['tabs'].append({'tab_id': 'wJ:t2', 'pane_count': 1})
                return result
            h.call = altered
            with self.subTest(mode=mode):
                result = self_space.move_self(h, 'OWN SPACE', SOCKET, apply=True)
                self.assertFalse(result['verified'])
                self.assertTrue(result['applied'])
                self.assertEqual(sum(a[:2] == ('pane', 'move') for a in h.calls), 1)
                self.assertIn('Do not retry', result['next_action'])

    def test_post_move_metadata_failure_has_unknown_verification_no_second_dispatch(self):
        h = Herdr(); call = h.call; moved = False
        def unavailable(*args):
            nonlocal moved
            if moved and args[:1] == ('status',): raise Failure('unavailable')
            result = call(*args)
            if args[:2] == ('pane', 'move'): moved = True
            return result
        h.call = unavailable
        result = self_space.move_self(h, 'OWN SPACE', SOCKET, apply=True)
        self.assertFalse(result['verified']); self.assertTrue(result['applied'])
        self.assertEqual(sum(a[:2] == ('pane', 'move') for a in h.calls), 1)


class ErrorReceiptTests(unittest.TestCase):
    def test_metadata_failure_is_classified_without_leaking_exception(self):
        h = Herdr()
        h.call = lambda *args: (_ for _ in ()).throw(PermissionError("SECRET Operation not permitted"))
        with patch.object(inventory, "LiveHerdr", return_value=h), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inventory.main(["reconcile-context", "--expected-socket", SOCKET]), 3)
        result = json.loads(output.getvalue())
        self.assertEqual(result['error_code'], 'access_denied')
        self.assertEqual(result['stage'], 'status/--json')
        self.assertNotIn('SECRET', output.getvalue())

    def test_missing_and_duplicate_match_counts_are_explicit(self):
        for count in (0, 2):
            h = Herdr()
            if count == 0: h.pane['agent_session']['value'] = 'other'
            else: h.extra = [copy.deepcopy(h.pane)]
            with patch.object(inventory, "LiveHerdr", return_value=h), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(inventory.main(["reconcile-context", "--expected-socket", SOCKET]), 3)
            result = json.loads(output.getvalue())
            self.assertEqual(result['error_code'], 'exact_match_count')
            self.assertEqual(result['details']['exact_matches'], count)
            self.assertNotIn('other', output.getvalue())


class DispatchTests(unittest.TestCase):
    def test_new_modes_do_not_initialize_cache_or_general_inventory(self):
        for argv in (["reconcile-context", "--expected-socket", SOCKET],
                     ["move-self-space", "--expected-socket", SOCKET, "--label", "Own space"],
                     ["move-self-space", "--expected-socket", SOCKET, "--label", "Own space", "--apply"]):
            h = Herdr()
            with patch.object(inventory, "LiveHerdr", return_value=h), patch.object(inventory, "Cache", side_effect=AssertionError("cache")), patch.object(inventory, "inventory_l1", side_effect=AssertionError("general inventory")), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(inventory.main(argv), 0)
            self.assertEqual(any(a[:2] == ("pane", "move") for a in h.calls), "--apply" in argv)

    def test_inventory_fixture_never_contacts_live_server(self):
        with patch.object(inventory, "LiveHerdr", side_effect=AssertionError("live")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inventory.main(["--fixture", "nonexistent.json", "reconcile-context", "--expected-socket", SOCKET]), 3)

    def test_untrusted_input_flags_not_accepted(self):
        for flag in ("--thread-id", "--pane-id", "--workspace"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                inventory.build_parser().parse_args(["reconcile-context", "--expected-socket", SOCKET, flag, "guess"])

if __name__ == '__main__': unittest.main()
