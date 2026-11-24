import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import contextlib
import io
from unittest.mock import patch
scripts=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(scripts if scripts.is_dir() else Path(__file__).resolve().parent))
import selected_space as selected
import inventory
from self_space import IdentityError

SOCKET='/fixture/confirmed.sock'
class Herdr:
    def __init__(self):
        self.env={'HERDR_ENV':'1'}  # No current-thread spoof or self-admission.
        self.calls=[]
        self.panes=[{'pane_id':'w1:p1','terminal_id':'term_left','tab_id':'w1:t1','workspace_id':'w1','cwd':'/fixture/left'},
                    {'pane_id':'w1:p2','terminal_id':'term_right','tab_id':'w1:t1','workspace_id':'w1','cwd':'/fixture/right'},
                    {'pane_id':'w2:p1','terminal_id':'term_other','tab_id':'w2:t1','workspace_id':'w2','cwd':'/fixture/other'}]
        self.tabs=[{'tab_id':'w1:t1','workspace_id':'w1','label':'RUNNER WORK'}, {'tab_id':'w2:t1','workspace_id':'w2','label':'OTHER'}]
        self.labels={'w1':'SOURCE','w2':'OTHER'}
        self.process={'shell_pid':101,'foreground_processes':[{'pid':102,'argv':['SECRET-MUST-NOT-RETURN']}],'env':'SECRET'}
        self.swapped=False
        self.move_error=False
        self.bad_label=False
        self.extra_destination=False
        self.missing_coverage=False
        self.process_changed=False
        self.moves=0

    def call(self,*args):
        self.calls.append(args)
        if args[:1]==('status',):
            return {'server':{'socket':SOCKET,'running':True,'compatible':True,'endpoint_compatible':True}}
        if args[:2]==('workspace','list'):
            rows=[{'workspace_id':wid,'label':label,'pane_count':sum(p['workspace_id']==wid for p in self.panes),'tab_count':sum(t['workspace_id']==wid for t in self.tabs)} for wid,label in self.labels.items()]
            return {'result':{'workspaces':rows}}
        if args[:2]==('pane','list'):
            wid=args[args.index('--workspace')+1]
            rows=[copy.deepcopy(p) for p in self.panes if p['workspace_id']==wid]
            if self.missing_coverage and wid=='w2':rows=[]
            return {'result':{'panes':rows}}
        if args[:2]==('tab','list'):
            wid=args[args.index('--workspace')+1]
            return {'result':{'tabs':[dict(t,pane_count=sum(p['tab_id']==t['tab_id'] for p in self.panes)) for t in self.tabs if t['workspace_id']==wid]}}
        if args[:2]==('pane','get'):
            return {'result':{'pane':copy.deepcopy(next(p for p in self.panes if p['pane_id']==args[2]))}}
        if args[:2]==('pane','layout'):
            ids=['w1:p2','w1:p1'] if self.swapped else ['w1:p1','w1:p2']
            return {'result':{'layout':{'panes':[{'pane_id':pid,'rect':{'x':i*10,'y':0,'width':10,'height':20}} for i,pid in enumerate(ids)]}}}
        if args[:2]==('pane','process-info'):
            p=copy.deepcopy(self.process)
            if self.moves and self.process_changed:p['shell_pid']=999
            return {'result':{'process_info':p}}
        if args[:2]==('pane','move'):
            self.moves+=1
            if self.move_error:raise TimeoutError('SECRET')
            p=next(p for p in self.panes if p['pane_id']==args[2]);old=copy.deepcopy(p)
            label=args[args.index('--label')+1]
            self.labels['w3']='WRONG' if self.bad_label else label
            p.update(pane_id='w3:p1',workspace_id='w3',tab_id='w3:t1')
            self.tabs.append({'tab_id':'w3:t1','workspace_id':'w3','label':label})
            if self.extra_destination:self.panes.append(dict(p,pane_id='w3:p2',terminal_id='term_extra'))
            return {'result':{'move_result':{'changed':True,'pane':copy.deepcopy(p),'previous_pane_id':old['pane_id'],'previous_tab_id':old['tab_id'],'previous_workspace_id':old['workspace_id']}}}
        raise AssertionError(args)

def move(h,**kwargs):
    return selected.move_selected(h,'w1:p2','term_right','w1:p1','RUNNER WORK','Own space',SOCKET,operator_selected=True,**kwargs)

class SelectedSpaceTests(unittest.TestCase):
    def test_preview_covers_all_workspaces_without_mutation_or_fake_session_proof(self):
        h=Herdr();r=move(h)
        self.assertTrue(r['verified']);self.assertFalse(r['applied']);self.assertFalse(r['automatic_self_identity'])
        self.assertEqual(r['reported_session_status'],'unavailable');self.assertEqual(r['workspace_coverage'],2)
        self.assertIn(('pane','list','--workspace','w2'),h.calls)
        self.assertEqual(h.moves,0);self.assertNotIn('SECRET',json.dumps(r))

    def test_one_move_preserves_processes_and_unrelated_topology(self):
        h=Herdr();r=move(h,apply=True)
        self.assertTrue(r['verified'],r);self.assertEqual(h.moves,1)
        self.assertTrue(all(r['checks'].values()));self.assertEqual(r['reported_session_continuity'],'unavailable')
        self.assertNotIn('reported_session_unchanged',r['checks'])
        self.assertEqual(r['identity']['pane_id'],'w3:p1')
        self.assertIn('--no-focus',next(a for a in h.calls if a[:2]==('pane','move')))

    def test_explicit_operator_binding_and_endpoint_are_required(self):
        h=Herdr()
        for op,endpoint in ((False,SOCKET),(True,None),(True,'/wrong')):
            with self.assertRaises(IdentityError):selected.move_selected(h,'w1:p2','term_right','w1:p1','RUNNER WORK','Own',endpoint,operator_selected=op,apply=True)
        self.assertEqual(h.moves,0)

    def test_missing_coverage_wrong_terminal_or_geometry_prevents_dispatch(self):
        for mode in ('coverage','terminal','geometry'):
            h=Herdr()
            if mode=='coverage':h.missing_coverage=True
            if mode=='terminal':h.panes[1]['terminal_id']='changed'
            if mode=='geometry':h.swapped=True
            with self.subTest(mode=mode),self.assertRaises(IdentityError):move(h,apply=True)
            self.assertEqual(h.moves,0)

    def test_geometry_or_neighbor_replacement_after_plan_prevents_dispatch(self):
        for mode in ('geometry','neighbor'):
            h=Herdr();original=selected._snapshot;count=0
            def racing(client):
                nonlocal count
                count+=1
                if count==2:
                    if mode=='geometry':h.swapped=True
                    else:h.panes[0]['terminal_id']='term_replacement'
                return original(client)
            with patch.object(selected,'_snapshot',side_effect=racing),self.assertRaises(IdentityError):move(h,apply=True)
            self.assertEqual(h.moves,0)

    def test_wrong_labels_extra_destination_or_process_change_are_unverified(self):
        for flag in ('bad_label','extra_destination','process_changed'):
            h=Herdr();setattr(h,flag,True)
            r=move(h,apply=True)
            self.assertFalse(r['verified'],r);self.assertTrue(r['move_attempted']);self.assertEqual(h.moves,1)

    def test_unknown_mutation_outcome_never_retries(self):
        h=Herdr();h.move_error=True;r=move(h,apply=True)
        self.assertIsNone(r['applied']);self.assertFalse(r['verified']);self.assertEqual(h.moves,1)
        self.assertNotIn('SECRET',json.dumps(r));self.assertIn('do not retry',r['error'])

    def test_present_reported_session_must_survive(self):
        h=Herdr();h.panes[1].update(agent='codex',agent_session={'agent':'codex','kind':'id','source':'herdr:codex','value':'reported-session'})
        r=move(h,apply=True)
        self.assertTrue(r['verified']);self.assertTrue(r['checks']['reported_session_unchanged'])

    def test_guards_stay_active_under_python_optimization(self):
        result=subprocess.run([sys.executable,'-O','-B',__file__,'--optimized-probe'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'guard_active')

class SelectedDispatchTests(unittest.TestCase):
    def args(self):
        return ['move-selected-space','--pane','w1:p2','--terminal','term_right','--right-of','w1:p1','--source-tab-label','RUNNER WORK','--label','Own space','--expected-socket',SOCKET,'--operator-selected','--json']

    def test_dispatch_never_uses_general_context_or_cache(self):
        for apply in (False,True):
            h=Herdr()
            with patch.object(inventory,'LiveHerdr',return_value=h), patch.object(inventory,'Cache',side_effect=AssertionError('cache')), patch.object(inventory,'verify_context',side_effect=AssertionError('general guard')), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(inventory.main(self.args()+(['--apply'] if apply else [])),0)
            self.assertEqual(h.moves,int(apply));self.assertFalse(json.loads(out.getvalue())['automatic_self_identity'])

    def test_fixture_refused_without_any_live_call(self):
        with patch.object(inventory,'LiveHerdr',side_effect=AssertionError('live')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inventory.main(['--fixture','must-not-open.json']+self.args()),3)

    def test_source_label_or_receipt_source_mismatch_stops(self):
        h=Herdr();h.tabs[0]['label']='CHANGED'
        with self.assertRaises(IdentityError):move(h,apply=True)
        self.assertEqual(h.moves,0)
        h=Herdr();original=h.call
        def wrong(*args):
            r=original(*args)
            if args[:2]==('pane','move'):r['result']['move_result']['previous_pane_id']='w1:p9'
            return r
        h.call=wrong;r=move(h,apply=True)
        self.assertFalse(r['verified']);self.assertFalse(r['checks']['source_binding']);self.assertEqual(h.moves,1)


if __name__=='__main__':
    if '--optimized-probe' in sys.argv:
        h=Herdr();h.swapped=True
        try:move(h,apply=True)
        except IdentityError:print('guard_active');raise SystemExit(0)
        raise SystemExit(1)
    unittest.main()
