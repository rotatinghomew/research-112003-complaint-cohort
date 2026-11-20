"""把复盘结果整理成值班组可用的文本报告。"""
from __future__ import annotations

from typing import List

from .audit import review_commitments
from .model import TimeSlice
from .seats import plan_all
from .signals import evaluate, followup


def _claim_summary(statement) -> str:
    attribution = statement.attribution
    if attribution.activity:
        scope = f'活动 {attribution.activity}（确认）'
    elif attribution.status == 'conflict':
        scope = '活动归属相互抵触，待查'
    elif attribution.status == 'withdrawn':
        scope = '原确认归属已撤回/排除'
    elif attribution.status == 'hint_only':
        scope = '仅有补证线索（' + '、'.join(attribution.hints) + '）'
    else:
        scope = '未归属'
    return f'{statement.order} 家庭 {statement.household} 投诉 {",".join(statement.claims)} ' \
           f'[{statement.severity}] {scope}'


def statements_report(tables, as_of: int) -> List[str]:
    slice_ = TimeSlice(tables, as_of)
    lines = [f'时点 {as_of} 的陈述（同一家庭 + 同一订单合一份）：']
    for statement in slice_.statements():
        lines.append('  - ' + _claim_summary(statement) +
                     (f'；{statement.attribution.reason}' if statement.attribution.reason else ''))
    return lines


def signals_report(tables, as_of: int) -> List[str]:
    lines = [f'时点 {as_of} 的信号（按各自比较日重放）：']
    for signal in tables.signals:
        if signal['compare_at'] > as_of:
            continue
        result = evaluate(tables, signal)
        lines.append(
            f'  - {signal["activity"]} 比较日 {signal["compare_at"]} '
            f'（{signal["scope"]}，日龄 {signal["age_day"]}）：{result["status"]}')
        for channel in result['channels']:
            lines.append(
                f'      {channel["channel"]} {channel["complaints"]}/{channel["denominator"]}'
                f'，基准 {channel["baseline_k"]}/{channel["baseline_n"]}'
                f'（{",".join(channel["baseline_activities"]) or "无"}）'
                f'，覆盖报齐于 {channel["coverage_complete_at"]}'
                f'，{channel["status"]}：{channel["reason"]}')
            if channel['volume_note']:
                lines.append(f'      说明：{channel["volume_note"]}')
        if result['pending_scope']:
            lines.append(f'      待核归属（不进入分子/分母）：'
                         + '；'.join(f'{item["order"]}/{item["household"]}'
                                     f'（{item["status"]}）'
                                     for item in result['pending_scope']))
    return lines


def followup_report(tables) -> List[str]:
    lines = ['追踪期复核（保留当日判断，不改写）：']
    for signal in tables.signals:
        result = followup(tables, signal)
        lines.append(
            f'  - {signal["activity"]} 比较日 {signal["compare_at"]} 当日 '
            f'{result["original"]["status"]}；追踪至 {result["followup_at"]}：'
            f'{result["verdict"]}（{result["note"]}）')
        if result['scope_changes']:
            lines.append('      追踪期新补到的投诉家庭（仅参考，不回写当日判警）：'
                         + '、'.join(f'{item["channel"]}/{item["household"]}'
                                     for item in result['scope_changes']))
    return lines


def seats_report(tables, as_of=None) -> List[str]:
    lines = ['联合复核席位：']
    for plan in plan_all(tables, as_of=as_of):
        lines.append(
            f'  - 班次 {plan.shift}（日 {plan.day}，{plan.jurisdiction}，'
            f'{plan.slots} 席）：{plan.status}')
        for group in plan.receipt_groups:
            lines.append(f'      已认领 receipt {group["receipt"]} 家庭 '
                         f'{"/".join(group["households"])} 投诉 '
                         f'{"/".join(group["claims"])}：'
                         + '；'.join(sorted(set(group['notes']))))
        for seat in plan.seats:
            lines.append(f'      席位 {seat.statement_key} 家庭 {seat.household} '
                         f'{",".join(seat.claims)}：{seat.reason}')
        for gap in plan.gaps:
            lines.append(f'      缺口：{gap}')
        seen_requests = set()
        for request in plan.evidence_requests:
            if request['kind'] == 'signal':
                key = ('signal', request['activity'], request['channel'])
                line = (f'      补证/分母：{request["activity"]}/{request["channel"]} '
                        f'{request["status"]}：{request["reason"]}')
            elif request['kind'] == 'statement_coverage':
                key = ('coverage', request['activity'])
                line = (f'      补证：{request["statement"]} 所属 {request["activity"]} '
                        f'{request["status"]}：{request["reason"]}')
            else:
                key = ('attribution', request['order'])
                line = (f'      补证/归属：{request["order"]}/{request["household"]} '
                        f'{request["status"]}：{request["reason"]}')
            if key in seen_requests:
                continue
            seen_requests.add(key)
            lines.append(line)
        if plan.watchpoints:
            for point in plan.watchpoints:
                lines.append(f'      注意：{point}')
        for note in plan.excluded:
            lines.append(f'      未排 {note["statement"]}：{note["reason"]}')
    return lines


def commitments_report(tables, as_of: int) -> List[str]:
    lines = [f'已认领材料复核（按 {as_of} 已知材料）：']
    for group in review_commitments(tables, as_of):
        state = '原办理归属保留'
        if not group['original_scope_kept']:
            state += '，但认领时未确认该活动'
        accounts = ''
        if set(group['accounts']) - set(group['households']):
            accounts = f'，原账号 {"/".join(group["accounts"])}'
        parts = [f'receipt {group["receipt"]}（班次 {group["shift"]}，活动 '
                 f'{group["activity"]}，家庭 {"/".join(group["households"])}'
                 f'{accounts}）：{state}']
        if group['disputed']:
            parts.append('暂停转交 ' + '/'.join(group['disputed']))
        if group['remaining']:
            parts.append('仍可继续 ' + '/'.join(group['remaining']))
        if group['notes']:
            parts.append('；'.join(group['notes']))
        lines.append('  - ' + '；'.join(parts))
    return lines


def full_report(tables, as_of: int) -> str:
    lines: List[str] = []
    lines += statements_report(tables, as_of)
    lines.append('')
    lines += signals_report(tables, as_of)
    lines.append('')
    lines += followup_report(tables)
    lines.append('')
    lines += seats_report(tables, as_of)
    lines.append('')
    lines += commitments_report(tables, as_of)
    return '\n'.join(lines)
