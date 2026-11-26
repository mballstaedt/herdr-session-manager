import json
import os
import stat
import sys
import tempfile
import unittest
import datetime as dt
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prerestart as ps

SID = '12345678-1234-1234-1234-123456789abc'


def layout(n_agents=2):
    panes = {}
    for i in range(n_agents):
        panes[str(i + 1)] = {'cwd': '/tmp', 'agent_session': {'agent': 'claude', 'kind': 'id', 'value': f'{SID[:-1]}{i}'}}
    panes['9'] = {'cwd': '/tmp', 'agent_session': None}
    return {'version': 1, 'workspaces': [{'id': 'w1', 'custom_name': 'ONE', 'tabs': [{'custom_name': 'T', 'panes': panes}]}]}


class PreShutdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_session(self, data):
        (self.dir / 'session.json').write_text(json.dumps(data))

    def test_backup_is_byte_identical_private_and_summarized(self):
        self.write_session(layout(2))
        r = ps.backup(self.dir, now=dt.datetime(2026, 11, 15, 19, 50, 1))
        self.assertTrue(r['ok'])
        dest = Path(r['backup'])
        self.assertEqual(dest.parent, self.dir / ps.BACKUP_DIR)
        self.assertEqual(dest.name, 'session-20261115T195001.json')
        self.assertEqual(dest.read_bytes(), (self.dir / 'session.json').read_bytes())
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(dest.parent.stat().st_mode), 0o700)
        self.assertEqual(r['summary'], {'workspaces': 1, 'tabs': 1, 'panes': 3, 'agent_sessions': {'claude': 2}})

    def test_missing_or_invalid_session_refuses_without_writing(self):
        r = ps.backup(self.dir)
        self.assertFalse(r['ok'])
        (self.dir / 'session.json').write_text('{not json')
        r = ps.backup(self.dir)
        self.assertFalse(r['ok'])
        self.assertIn('not valid JSON', r['error'])
        self.assertFalse((self.dir / ps.BACKUP_DIR).exists())

    def test_prune_keeps_newest_and_ignores_foreign_files(self):
        self.write_session(layout(1))
        bdir = self.dir / ps.BACKUP_DIR
        bdir.mkdir()
        (bdir / 'keep-me.txt').write_text('x')
        for i in range(5):
            ps.backup(self.dir, keep=3, now=dt.datetime(2026, 1, 1, 0, 0, i))
        names = sorted(p.name for p in bdir.iterdir())
        self.assertEqual(names, ['keep-me.txt', 'session-20260101T000002.json',
                                 'session-20260101T000003.json', 'session-20260101T000004.json'])

    def test_same_second_does_not_overwrite(self):
        self.write_session(layout(1))
        t = dt.datetime(2026, 1, 1)
        a = ps.backup(self.dir, now=t)['backup']
        b = ps.backup(self.dir, now=t)['backup']
        self.assertNotEqual(a, b)
        self.assertTrue(Path(a).exists() and Path(b).exists())

    def test_resume_disabled_in_config_is_a_warning(self):
        self.write_session(layout(1))
        (self.dir / 'config.toml').write_text('[session]\nresume_agents_on_restore = false\n')
        r = ps.backup(self.dir)
        self.assertFalse(r['checks']['resume_agents_on_restore'])
        self.assertTrue(any('resume_agents_on_restore' in w for w in r['warnings']))

    def test_live_busy_agents_and_old_integration_warn(self):
        self.write_session(layout(1))
        panes = {'result': {'panes': [
            {'pane_id': 'w1:p1', 'agent': 'claude', 'agent_status': 'working', 'terminal_title_stripped': 'Busy one'},
            {'pane_id': 'w1:p2', 'agent': 'claude', 'agent_status': 'idle'},
            {'pane_id': 'w1:p3', 'agent': 'claude', 'agent_status': 'working'}]}}

        def fake(args):
            if args[:2] == ['pane', 'list']:
                return json.dumps(panes)
            if args[:2] == ['integration', 'status']:
                return 'claude: outdated (v4) (/x)\ncodex: current (v10) (/y)\n'
            raise AssertionError(args)

        with patch.object(ps, '_herdr', side_effect=fake), patch.dict(os.environ, {'HERDR_PANE_ID': 'w1:p3'}):
            r = ps.backup(self.dir, live=True)
        self.assertEqual(r['busy'], [{'pane': 'w1:p1', 'status': 'working', 'title': 'Busy one'}])
        self.assertEqual(r['checks']['integrations']['claude'], 4)
        self.assertTrue(any('claude integration v4' in w for w in r['warnings']))
        self.assertTrue(any('w1:p1' in w for w in r['warnings']))

    def test_live_failure_is_a_warning_not_a_crash(self):
        self.write_session(layout(1))
        with patch.object(ps, '_herdr', side_effect=OSError('no server')):
            r = ps.backup(self.dir, live=True)
        self.assertTrue(r['ok'])
        self.assertTrue(any('live pane state unavailable' in w for w in r['warnings']))

    def test_session_dir_resolution_prefers_explicit_then_socket_match(self):
        self.assertEqual(ps.resolve_session_dir('/explicit'), Path('/explicit'))
        listing = json.dumps({'sessions': [
            {'name': 'default', 'default': True, 'session_dir': '/a', 'socket_path': '/a/herdr.sock'},
            {'name': 'work', 'default': False, 'session_dir': '/b', 'socket_path': '/b/herdr.sock'}]})
        with patch.object(ps, '_herdr', return_value=listing), patch.dict(os.environ, {'HERDR_SOCKET_PATH': '/b/herdr.sock'}):
            self.assertEqual(ps.resolve_session_dir(None), Path('/b'))
        with patch.object(ps, '_herdr', return_value=listing), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ps.resolve_session_dir(None), Path('/a'))
        with patch.object(ps, '_herdr', side_effect=OSError), patch.dict(os.environ, {'HOME': '/h'}, clear=True):
            self.assertEqual(ps.resolve_session_dir(None), Path('/h/.config/herdr'))

    def test_render_has_restore_and_rollback_and_never_stops_server(self):
        self.write_session(layout(1))
        text = ps.render(ps.backup(self.dir))
        self.assertIn('Safety copy saved', text)
        self.assertIn('herdr server stop', text)  # only as an instruction for the human rollback
        self.assertIn('Not done by this command', text)


if __name__ == '__main__':
    unittest.main()
