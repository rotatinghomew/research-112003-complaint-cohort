"""组长查异常时的可追溯视图：分母、关联、席位和已送材料。"""
from __future__ import annotations

from typing import Dict, List

from .model import TimeSlice
from .seats import plan_all
from .signals import evaluate


def _sent_material(tables, day: int) -> List[dict]:
    """截至 day 已送出的材料：认领记录。"""
    claims = {row['claim']: row for row in tables.claims}
    slice_ = TimeSlice(tables, day)
    material = []
    for row in tables.commitments:
        if row['agreed'] > day:
            continue
        claim = claims.get(row['claim'])
        material.append({
            'kind': 'commitment', 'receipt': row['receipt'], 'shift': row['shift'],
            'activity': row['activity'],
            'household': slice_.household(row['household']),
            'account': row['household'],
            'claim': row['claim'], 'sent': row['agreed'],
            'severity': claim['severity'] if claim else None,
        })
    return material


def audit_activity(tables, activity: str, as_of: int) -> dict:
    signals = [row for row in tables.signals
               if row['activity'] == activity and row['compare_at'] <= as_of]
    latest = max(signals, key=lambda row: row['compare_at']) if signals else None
    slice_ = TimeSlice(tables, as_of)
    evaluation = evaluate(tables, latest) if latest else None

    # 可支持的关联：确认、待核、已撤回/排除分开，逐条附凭据。
    effective = {row['id'] for row in slice_.effective_links()}
    support = []
    for order in sorted({row['subject'] for row in tables.links}):
        attribution = slice_.attribution(order)
        links = [row for row in tables.links
                 if row['subject'] == order and row['received'] <= as_of
                 and row['activity'] == activity]
        if not (attribution.activity == activity or activity in attribution.hints or links):
            continue
        purchase = next(row for row in tables.purchases if row['order'] == order)
        support.append({
            'order': order,
            'household': slice_.household(purchase['household']),
            'status': attribution.status,
            'activities': list(attribution.activities),
            'hints': list(attribution.hints),
            'evidence': list(attribution.evidence),
            'closed': list(attribution.closed),
            'reason': attribution.reason,
            'links': [
                {
                    'id': row['id'], 'verdict': row['verdict'], 'ref': row['ref'],
                    'received': row['received'],
                    'effective': row['id'] in effective,
                    'course_matches': row['course'] == purchase['course'],
                    'bought_in_window': (
                        row['start'] <= purchase['bought'] <= row['end']),
                    'closes': row['ref'] if row['id'] not in effective and row['ref'] else '',
                }
                for row in links
            ],
        })

    # 席位占用与未占用原因：截至 as_of 已排班次里的相关事项。
    seat_lines = []
    for plan in plan_all(tables, as_of=as_of):
        for seat in plan.seats:
            if seat.activity == activity or activity in seat.activities:
                seat_lines.append({
                    'shift': plan.shift, 'day': plan.day, 'state': 'occupied',
                    'statement': seat.statement_key, 'household': seat.household,
                    'basis': seat.basis, 'reason': seat.reason})
        for item in plan.excluded:
            if (item.get('activity') != activity
                    and activity not in item.get('activities', [])):
                continue
            seat_lines.append({
                'shift': plan.shift, 'day': plan.day, 'state': 'not_occupied',
                'statement': item['statement'], 'household': item['household'],
                'basis': None, 'reason': item['reason']})

    return {
        'activity': activity,
        'as_of': as_of,
        'signal': latest,
        'evaluation': evaluation,
        'denominator_completeness': [
            {
                'channel': channel['channel'],
                'complete_at': channel['coverage_complete_at'],
                'denominator': channel['denominator'],
                'observed_through': channel['denominator_observed_through'],
                'rate': channel['rate'],
                'status': channel['status'],
            }
            for channel in (evaluation['channels'] if evaluation else [])
        ],
        'support': support,
        'seats': seat_lines,
        'sent_material': [row for row in _sent_material(tables, as_of)
                          if row['activity'] == activity],
    }


def review_commitments(tables, as_of: int) -> List[dict]:
    """按 as_of 重看已认领材料：原办理归属保留，争议部分暂停，其余仍可继续。"""
    claims = {row['claim']: row for row in tables.claims}
    slice_ = TimeSlice(tables, as_of)
    groups: Dict[str, dict] = {}
    for row in tables.commitments:
        if row['agreed'] > as_of:
            continue
        claim = claims.get(row['claim'])
        order = claim['order'] if claim else None
        at_agreed = TimeSlice(tables, row['agreed']).attribution(order) if order else None
        now = slice_.attribution(order) if order else None
        kept = bool(at_agreed and at_agreed.status == 'confirmed'
                    and at_agreed.activity == row['activity'])
        disputed = bool(now and (
            now.status in ('withdrawn', 'conflict')
            or now.activity != row['activity']))
        group = groups.setdefault(row['receipt'], {
            'receipt': row['receipt'], 'shift': row['shift'],
            'activity': row['activity'], 'households': set(),
            'accounts': set(),
            'claims': [], 'disputed': [], 'remaining': [],
            'original_scope_kept': True, 'notes': []})
        group['households'].add(slice_.household(row['household']))
        group['accounts'].add(row['household'])
        group['claims'].append(row['claim'])
        if disputed:
            group['disputed'].append(row['claim'])
        elif claim and claim['state'] == 'open':
            group['remaining'].append(row['claim'])
        if not kept:
            group['original_scope_kept'] = False
        if disputed:
            detail = '归属现为 ' + now.status
            if now.activity:
                detail += f'（活动 {now.activity}）'
            if now.closed:
                detail += '，原凭据 ' + '/'.join(now.closed) + ' 已关闭'
            group['notes'].append(f'{row["claim"]}：{detail}')
    result = []
    for group in groups.values():
        group['households'] = sorted(group['households'])
        group['accounts'] = sorted(group['accounts'])
        result.append(group)
    return sorted(result, key=lambda item: (item['shift'], item['receipt']))
