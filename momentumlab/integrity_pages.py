"""Stable review-group filtering and pagination of an already validated report."""
from .issue_review import review_groups, repair_eligible

PAGE_SIZES = (25, 50, 100, 250)


def page(report, *, number=1, size=50, filters=None, sort='ticker', descending=False):
    filters = filters or {}
    size = int(size)
    if size not in PAGE_SIZES:
        raise ValueError('Page size must be 25, 50, 100, or 250.')
    groups = review_groups(report)
    overall_counts = {'unresolved_groups': sum(any(r.get('blocks_research') for r in g['members']) for g in groups),
                      'confirmed_groups': sum(any(r.get('resolution_status') == 'CONFIRMED' for r in g['members']) for g in groups)}
    blockers=[dict(issue_id=r['issue_id'],scope=r.get('scope_label',r['ticker']),component=r['affected_component'],
                   start=r.get('date',''),end=r.get('end_date',''),status=r['resolution_status'],
                   explanation=r.get('explanation') or r['classification'])
              for g in groups for r in g['members'] if r.get('blocks_research')]
    def matches(group):
        members = group['members']
        def any_text(field, value):
            return not value or any(value.lower() in str(row.get(field, '')).lower() for row in members)
        return (any_text('resolution_status', filters.get('status', '')) and
                any_text('ticker', filters.get('ticker', '')) and
                any_text('affected_component', filters.get('component', '')) and
                any_text('classification', filters.get('classification', '')) and
                (not filters.get('group') or filters['group'].lower() in group['group_id'].lower()) and
                (not filters.get('blocking') or any(r.get('blocks_research') for r in members)) and
                (not filters.get('repair_eligible') or any(repair_eligible(r) for r in members)))
    groups = [g for g in groups if matches(g)]
    keys = {'severity': lambda g: {'FAIL': 0, 'WARNING': 1, 'INFO': 2}.get(g['members'][0].get('severity'), 3),
            'status': lambda g: g['members'][0].get('resolution_status', ''),
            'ticker': lambda g: g['members'][0].get('ticker', ''),
            'date': lambda g: g['members'][0].get('date', ''),
            'component': lambda g: g['members'][0].get('affected_component', ''),
            'updated': lambda g: g['members'][0].get('confirmed_at', '')}
    if sort not in keys: raise ValueError('Unsupported findings sort.')
    groups.sort(key=lambda g: (keys[sort](g), g['group_id']), reverse=bool(descending))
    total = len(groups); pages = max(1, (total + size - 1) // size)
    number = max(1, min(int(number), pages))
    start = (number - 1) * size
    selected = groups[start:start + size]
    return {'ok': True, 'page': number, 'page_size': size, 'pages': pages,
            'overall_counts': overall_counts,
            'blocking_reasons': blockers[:50], 'blocking_reason_count': len(blockers),
            'total_groups': total, 'total_findings': sum(len(g['members']) for g in groups),
            'from': start + 1 if total else 0, 'to': min(start + size, total),
            'groups': [{'group_id': g['group_id'], 'issue_ids': g['issue_ids'],
                        'repair_issue_ids':[r['issue_id'] for r in g['members'] if repair_eligible(r)],
                        'members': g['members']} for g in selected],
            'all_group_ids': [g['group_id'] for g in groups]}
