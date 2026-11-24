"""Saved-session recovery. Reads metadata only; never replays prompts or permissions."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import shlex
from titles import tab_title
from zoneinfo import ZoneInfo

ID = re.compile(r'^[0-9a-fA-F-]{32,36}$')


def saved_sessions(home=None):
    home = Path(home or Path.home())
    rows, warnings = {}, []
    codex = Path(os.environ.get('CODEX_HOME', home / '.codex'))
    for db in sorted(codex.glob('state_*.sqlite'), reverse=True)[:1]:
        try:
            with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as con:
                for sid, title, cwd, updated, path in con.execute(
                    'select id,title,cwd,updated_at,rollout_path from threads where archived=0'):
                    if ID.fullmatch(sid) and Path(path).is_file():
                        rows[('codex', sid)] = dict(kind='codex', id=sid, title=title or sid,
                            cwd=cwd, updated=updated, source='Codex thread metadata')
        except (sqlite3.Error, OSError) as e:
            warnings.append(f'Codex metadata unavailable: {type(e).__name__}')
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude'))
    for path in (claude / 'projects').glob('*/*.jsonl'):
        if not ID.fullmatch(path.stem):
            continue
        try:
            # Only top-level metadata fields, never message text or tool payloads.
            with path.open('rb') as f:
                head = f.read(65536).splitlines()
                size = f.seek(0, 2)
                f.seek(max(0, size - 65536))
                tail = f.read().splitlines()[1:] if size > 65536 else []
            meta = {}
            for raw in head + tail:
                try:
                    rec = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                for key in ('cwd', 'customTitle', 'summary', 'slug'):
                    if isinstance(rec.get(key), str):
                        meta[key] = rec[key]
            if meta.get('cwd'):
                sid = path.stem
                rows[('claude', sid)] = dict(kind='claude', id=sid,
                    title=meta.get('customTitle') or meta.get('summary') or meta.get('slug') or sid,
                    cwd=meta['cwd'], updated=path.stat().st_mtime,
                    source='Claude transcript metadata; file modification time')
        except OSError:
            warnings.append('A Claude session metadata file could not be read')
    return sorted(rows.values(), key=lambda r: r['updated'], reverse=True), warnings


def live_sessions(h, ctx):
    live = {}
    for ws in h.call('workspace', 'list')['result']['workspaces']:
        for p in h.call('pane', 'list', '--workspace', ws['workspace_id'])['result']['panes']:
            kind = (p.get('agent') or '').lower()
            if not kind:
                continue
            sid = (p.get('agent_session') or {}).get('value')
            info = h.call('pane', 'process-info', '--pane', p['pane_id'])['result']['process_info']
            explicit = set()
            for proc in info.get('foreground_processes', []):
                argv = proc.get('argv') or []
                if not argv or Path(argv[0]).name != kind:
                    continue
                if kind == 'codex' and 'resume' in argv:
                    explicit.update(a for a in argv[argv.index('resume')+1:] if ID.fullmatch(a))
                if kind == 'claude' and '--resume' in argv:
                    explicit.update(a for a in argv[argv.index('--resume')+1:] if ID.fullmatch(a))
            if len(explicit) == 1:
                sid = next(iter(explicit))
            key = (kind, sid) if sid else (kind, 'unresolved:' + p['pane_id'])
            entry = dict(p)
            entry['identity_unresolved'] = not bool(sid)
            if key in live:
                entry['matching_panes'] = live[key].get('matching_panes', [live[key]['pane_id']]) + [p['pane_id']]
            live[key] = entry
    return live


def recent(h, ctx=None, home=None):
    rows, warnings = saved_sessions(home)
    live = live_sessions(h, ctx) if ctx else None
    for r in rows:
        p = (live or {}).get((r['kind'], r['id']))
        r['state'] = 'running in Herdr' if p else ('not open on this Herdr server' if live is not None else 'unknown (Herdr context unavailable)')
        r['pane'] = p.get('pane_id') if p else None
        r['matching_panes'] = p.get('matching_panes', [p['pane_id']]) if p else []
        r['ambiguous_panes'] = [v['pane_id'] for v in (live or {}).values()
            if v.get('identity_unresolved') and (v.get('foreground_cwd') or v.get('cwd')) == r['cwd']]
        if p:
            r['title'] = p.get('terminal_title_stripped') or r['title']
        r['last_worked_at'] = dt.datetime.fromtimestamp(r['updated'], dt.timezone.utc).isoformat()
    return rows, warnings


def render(rows, warnings):
    lines = ['Recent saved sessions (last recorded activity, UTC):']
    for r in rows:
        title = ''.join(c for c in r['title'] if c.isprintable())[:160]
        lines.append(f"{r['kind']}:{r['id']} | {r['state']} | {r['last_worked_at']} | {title} | {r['cwd']}")
    return '\n'.join(lines + warnings + ['Select exact kind:session-id values to resume. Absence on this server does not prove stopped elsewhere.'])


def resume(h, ctx, selected, apply=False, home=None, approve_for_me=False, group_by_repo=False):
    rows, warnings = recent(h, ctx, home)
    lookup = {f"{r['kind']}:{r['id']}": r for r in rows}
    if len(set(selected)) != len(selected):
        raise ValueError('Duplicate selections')
    plan = []
    # Validate the entire selection before creating any tabs.
    for key in selected:
        if key not in lookup:
            raise ValueError(f'Saved session not found: {key}')
        r = lookup[key]
        if approve_for_me and r['kind'] != 'codex':
            raise ValueError('--approve-for-me is supported only for Codex')
        if r.get('ambiguous_panes'):
            raise ValueError(f'Unresolved live pane in this project: {r["ambiguous_panes"]}; inspect identity before resume')
        if r['pane']:
            raise ValueError(f'Already running: {key} in {r["pane"]}')
        if not Path(r['cwd']).is_dir():
            raise ValueError(f'Working directory unavailable: {r["cwd"]}')
        item = dict(r)
        item['tab_title'] = tab_title(r['title']) or r['id'].upper()
        if group_by_repo:
            item['destination'] = repo_destination(h, ctx, r['cwd'])
        plan.append(item)
    if not apply:
        return {'result': 'preview', 'workspace': ctx['workspace_id'], 'sessions': plan, 'warnings': warnings}
    # Hold a local lock across checks and launches to serialize this manager's restores.
    import fcntl
    lockroot = Path(os.environ.get('HSM_CACHE_DIR', Path.home() / '.cache/herdr-session-manager'))
    lockroot.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (lockroot / 'recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for r in plan:
                live = live_sessions(h, ctx)
                if any(p.get('identity_unresolved') and (p.get('foreground_cwd') or p.get('cwd')) == r['cwd'] for p in live.values()):
                    raise ValueError('Unresolved live pane in selected project; inspect before resume')
                if (r['kind'], r['id']) in live:
                    raise ValueError(f'Session became live: {r["id"]}')
                # Detect explicit resume processes outside this server, without exposing argv.
                if explicit_resume_live(r['kind'], r['id']):
                    raise ValueError(f'Session has an existing local resume process: {r["id"]}')
                from inventory import claude_session_live, codex_session_live
                held = (claude_session_live(r['id'], str(Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home()/'.claude'))/'sessions'))
                        if r['kind'] == 'claude' else codex_session_live(r['id'], str(Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))/'thread-writer-locks')))
                if held:
                    raise ValueError(f'Session has a local writer marker: {r["id"]}')
                title = r['tab_title']
                dest = r.get('destination') or {'workspace_id': ctx['workspace_id']}
                if not dest.get('workspace_id'):
                    created = h.call('workspace', 'create', '--cwd', r['cwd'], '--label', dest['label'], '--no-focus')['result']
                    # Herdr creates one root tab/pane with a workspace; use that pane.
                    wid = created['workspace']['workspace_id']
                    r['created_workspace_id'] = wid
                    tabs = h.call('tab', 'list', '--workspace', wid)['result']['tabs']
                    panes = h.call('pane', 'list', '--workspace', wid)['result']['panes']
                    if len(tabs) != 1 or len(panes) != 1:
                        raise ValueError('New workspace layout unexpected; inspect before retrying')
                    created = {'tab': tabs[0], 'root_pane': panes[0]}
                    h.call('tab', 'rename', tabs[0]['tab_id'], title)
                    for pending in plan:
                        if pending.get('destination') == dest:
                            pending['destination'] = {'workspace_id': wid, 'label': dest['label']}
                else:
                    created = h.call('tab', 'create', '--workspace', dest['workspace_id'], '--cwd', r['cwd'],
                                     '--label', title, '--no-focus')['result']
                r['tab_id'] = created['tab']['tab_id']
                r['pane_id'] = created['root_pane']['pane_id']
                args = ['resume', r['id']] if r['kind'] == 'codex' else ['--resume', r['id']]
                if approve_for_me:
                    if r['kind'] != 'codex':
                        raise ValueError('--approve-for-me is supported only for Codex')
                    args.insert(1, '--approve-for-me')
                # Admission can be uncertain on timeout: preserve tab and never retry automatically.
                try:
                    h.call('agent', 'start', title, '--kind', r['kind'], '--pane', r['pane_id'], '--', *args)
                    r['result'] = 'start admitted; verify session identity in the new tab'
                except Exception as e:
                    r['result'] = 'start uncertain; inspect this tab before retrying'
                    return {'result': 'incomplete', 'sessions': plan, 'error': type(e).__name__}
        except Exception as e:
            return {'result': 'incomplete', 'sessions': plan, 'error': str(e),
                    'note': 'Preserve created tabs/workspaces; inspect before retrying.'}
    return {'result': 'started', 'sessions': plan}


def explicit_resume_live(kind, sid):
    """Match actual provider executable + exact resume ID, never our Python wrapper."""
    cp = subprocess.run(['ps', '-axo', 'command='], capture_output=True, text=True, check=True)
    for line in cp.stdout.splitlines():
        try:
            argv = shlex.split(line)
        except ValueError:
            continue
        if not argv or Path(argv[0]).name != kind:
            continue
        if kind == 'codex' and 'resume' in argv[1:] and sid in argv[2:]:
            return True
        if kind == 'claude' and any(arg == '--resume' and i+1 < len(argv) and argv[i+1] == sid
                                    for i, arg in enumerate(argv)):
            return True
    return False


def repo_root(cwd):
    cp = subprocess.run(['git', '-C', cwd, 'rev-parse', '--show-toplevel'],
                        capture_output=True, text=True)
    return str(Path(cp.stdout.strip()).resolve()) if cp.returncode == 0 else str(Path(cwd).resolve())


def repo_destination(h, ctx, cwd):
    root = repo_root(cwd)
    label = tab_title(Path(root).name)
    matches = [w for w in ctx['workspaces'] if (w.get('label') or '').upper() == label]
    if len(matches) > 1:
        raise ValueError(f'Multiple workspaces named {label}; use explicit --workspace')
    return {'workspace_id': matches[0]['workspace_id'] if matches else None, 'label': label, 'repo': root}


def restore(h, ctx, date=None, until=None, timezone='UTC', repo=None, title=None,
            selected=None, apply=False, approve_for_me=False, workspace_explicit=False):
    """Date is last recorded activity since local midnight, not a historical snapshot."""
    if not any((date, repo, title)):
        raise ValueError('Supply a start date, --repo, or --title')
    try:
        zone = ZoneInfo(timezone)
    except KeyError as e:
        raise ValueError(f'Unknown timezone: {timezone}') from e
    start = dt.datetime.combine(dt.date.fromisoformat(date), dt.time.min, zone).timestamp() if date else None
    end = (dt.datetime.combine(dt.date.fromisoformat(until) + dt.timedelta(days=1), dt.time.min, zone).timestamp()
           if until else None)
    if start is not None and end is not None and end <= start:
        raise ValueError('--until must be on or after the start date')
    rows, warnings = recent(h, ctx)
    candidates = []
    for r in rows:
        if start is not None and r['updated'] < start or end is not None and r['updated'] >= end:
            continue
        if repo and repo_root(r['cwd']) != repo_root(repo):
            continue
        if title and title.casefold() not in r['title'].casefold():
            continue
        if r['title'] == r['id'] or r['title'].casefold() == 'guardian review' or r.get('auxiliary'):
            continue
        candidates.append(r)
    keys = {f"{r['kind']}:{r['id']}" for r in candidates}
    if selected:
        if not set(selected) <= keys:
            raise ValueError('Selected IDs must belong to the filtered candidate list')
        if ctx is None:
            raise ValueError('Verified Herdr context required for restore selection')
        return resume(h, ctx, selected, apply=apply, approve_for_me=approve_for_me,
                      group_by_repo=not workspace_explicit)
    if apply:
        raise ValueError('--apply requires explicit --select kind:session-id values')
    return {'result': 'candidates', 'timezone': timezone, 'since': date, 'until_inclusive': until,
            'sessions': candidates, 'warnings': warnings,
            'note': 'Last-recorded activity filter, not proof of historical Herdr membership. Select exact IDs; live or ambiguous sessions are preserved.'}
