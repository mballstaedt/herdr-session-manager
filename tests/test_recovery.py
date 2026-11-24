import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recovery

SID = '12345678-1234-1234-1234-123456789abc'

class RecoveryTests(unittest.TestCase):
    def test_claude_metadata_without_message_contents(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'.claude/projects/test'; p.mkdir(parents=True)
            (p/f'{SID}.jsonl').write_text('{"cwd":"/tmp","customTitle":"Recovered title","message":{"content":"private"}}\n')
            with patch.dict('os.environ', {'CODEX_HOME': d, 'CLAUDE_CONFIG_DIR': str(Path(d)/'.claude')}):
                rows, warnings = recovery.recent(None, home=d)
            self.assertEqual(rows[0]['title'], 'Recovered title')
            self.assertIn('unknown', rows[0]['state'])
            self.assertNotIn('private', str(rows))

    def test_preview_and_live_refusal(self):
        row = dict(kind='codex', id=SID, title='Title', cwd='/tmp', pane=None)
        with patch.object(recovery, 'recent', return_value=([row], [])):
            result = recovery.resume(None, {'workspace_id':'w1'}, [f'codex:{SID}'])
            self.assertEqual(result['result'], 'preview')
            row['pane'] = 'w2:p1'
            with self.assertRaises(ValueError):
                recovery.resume(None, {'workspace_id':'w1'}, [f'codex:{SID}'], True)

    def test_selected_resume_and_uncertain_start(self):
        from unittest.mock import Mock
        row = dict(kind='claude', id=SID, title='Title', cwd='/tmp', pane=None)
        h = Mock()
        h.call.side_effect = [{'result': {'tab': {'tab_id':'w1:t2'}, 'root_pane':{'pane_id':'w1:p2'}}}, RuntimeError('uncertain')]
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'HSM_CACHE_DIR':d}), patch.object(recovery, 'recent', return_value=([row], [])), patch.object(recovery, 'live_sessions', return_value={}), patch('inventory.claude_session_live', return_value=False), patch.object(recovery.subprocess, 'run', return_value=Mock(stdout='')):
            result = recovery.resume(h, {'workspace_id':'w1'}, [f'claude:{SID}'], True)
        self.assertEqual(result['result'], 'incomplete')
        self.assertEqual(h.call.call_count, 2)
        self.assertEqual(h.call.call_args.args[-2:], ('--resume', SID))

class RestoreTests(unittest.TestCase):
    def test_local_date_bounds_include_full_end_day(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('America/Denver')
        def row(day, hour):
            return dict(kind='codex', id=f'{day}-{hour}', title='Main work', cwd='/tmp',
                        updated=dt.datetime(2026,11,day,hour,tzinfo=zone).timestamp())
        rows = [row(6,23),row(7,0),row(8,23),row(9,0)]
        with patch.object(recovery, 'recent', return_value=(rows, [])):
            result = recovery.restore(None, None, '2026-11-07','2026-11-08','America/Denver')
        self.assertEqual([r['id'] for r in result['sessions']], ['7-0','8-23'])

    def test_apply_never_selects_bulk_implicitly(self):
        with patch.object(recovery, 'recent', return_value=([], [])):
            with self.assertRaises(ValueError):
                recovery.restore(None, None, '2026-11-07', apply=True)

    def test_selection_must_match_filters(self):
        with patch.object(recovery, 'recent', return_value=([], [])):
            with self.assertRaises(ValueError):
                recovery.restore(None, {}, title='Other', selected=[f'codex:{SID}'])

    def test_process_guard_ignores_python_and_shell_wrappers(self):
        from unittest.mock import Mock
        text = f'python3 inventory.py resume codex:{SID}\n/bin/zsh -lc codex resume {SID}'
        with patch.object(recovery.subprocess, 'run', return_value=Mock(stdout=text)):
            self.assertFalse(recovery.explicit_resume_live('codex', SID))
        with patch.object(recovery.subprocess, 'run', return_value=Mock(stdout=f'/opt/bin/codex resume --approve-for-me {SID}')):
            self.assertTrue(recovery.explicit_resume_live('codex', SID))

    def test_live_explicit_id_overrides_stale_metadata(self):
        from unittest.mock import Mock
        h=Mock(); h.call.side_effect=[{'result':{'workspaces':[{'workspace_id':'w1'}]}},
            {'result':{'panes':[{'agent':'codex','pane_id':'w1:p1','agent_session':{'value':'stale'}}]}},
            {'result':{'process_info':{'foreground_processes':[{'argv':['/bin/codex','resume',SID]}]}}}]
        live=recovery.live_sessions(h,{})
        self.assertIn(('codex',SID),live)
        self.assertNotIn(('codex','stale'),live)

    def test_titles_apply_is_idempotent(self):
        import inventory
        from titles import uppercase_tabs
        h=inventory.FixtureHerdr(str(Path(__file__).parent/'fixtures/single.json'))
        ctx=inventory.verify_context(h)
        first=uppercase_tabs(h,ctx,True)
        self.assertTrue(first['changes'])
        self.assertEqual(uppercase_tabs(h,ctx)['changes'],[])

    def test_new_repo_workspace_uses_root_pane_and_uppercase_title(self):
        from unittest.mock import Mock
        row = dict(kind='codex', id=SID, title='Recover project', cwd='/tmp', pane=None)
        h=Mock(); h.call.side_effect=[
            {'result':{'workspace':{'workspace_id':'w2'}}},
            {'result':{'tabs':[{'tab_id':'w2:t1'}]}},
            {'result':{'panes':[{'pane_id':'w2:p1'}]}},
            {'result':{}}, {'result':{}}]
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'HSM_CACHE_DIR':d}), patch.object(recovery,'recent',return_value=([row],[])), patch.object(recovery,'repo_destination',return_value={'workspace_id':None,'label':'PROJECT','repo':'/tmp'}), patch.object(recovery,'live_sessions',return_value={}), patch.object(recovery,'explicit_resume_live',return_value=False), patch('inventory.codex_session_live',return_value=False):
            result=recovery.resume(h,{'workspace_id':'w1'},[f'codex:{SID}'],True,approve_for_me=True,group_by_repo=True)
        self.assertEqual(result['result'],'started')
        calls=[c.args for c in h.call.call_args_list]
        self.assertFalse(any(c[:2]==('tab','create') for c in calls))
        self.assertIn(('tab','rename','w2:t1','RECOVER PROJECT'),calls)
        self.assertEqual(calls[-1][-3:],('resume','--approve-for-me',SID))

    def test_ambiguous_live_project_refuses_even_in_preview(self):
        row=dict(kind='codex',id=SID,title='Main work',cwd='/tmp',pane=None,ambiguous_panes=['w1:p2'])
        with patch.object(recovery,'recent',return_value=([row],[])):
            with self.assertRaisesRegex(ValueError,'Unresolved live pane'):
                recovery.resume(None,{'workspace_id':'w1'},[f'codex:{SID}'])
