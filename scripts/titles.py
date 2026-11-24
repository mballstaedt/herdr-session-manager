"""Consistent visible tab labels; never changes native conversation titles."""
def tab_title(value):
    return ''.join(c for c in str(value or '') if c.isprintable()).strip().upper()[:160]


def uppercase_tabs(h, ctx, apply=False):
    plan = []
    for ws in ctx['workspaces']:
        if ctx.get('workspace_id') and ws['workspace_id'] != ctx['workspace_id']:
            continue
        for tab in h.call('tab', 'list', '--workspace', ws['workspace_id'])['result']['tabs']:
            old = tab.get('label') or ''
            new = tab_title(old)
            if old != new and new:
                plan.append({'tab_id': tab['tab_id'], 'from': old, 'to': new})
    if apply:
        for op in plan:
            h.call('tab', 'rename', op['tab_id'], op['to'])
            got = h.call('tab', 'get', op['tab_id'])['result']['tab']['label']
            if got != op['to']:
                raise ValueError(f'Tab rename unverified: {op["tab_id"]}; stop and inspect')
    return {'result': 'applied' if apply else 'preview', 'changes': plan}
