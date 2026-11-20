"""命令行入口。

    python3 -m review                         按资料最新时点输出完整复盘
    python3 -m review --as-of 34              按指定时点重放
    python3 -m review --audit a --as-of 34    组长视角查看某一异常
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audit import audit_activity
from .loader import load
from .report import full_report


def latest_day(tables) -> int:
    return max(
        [row['received'] for row in tables.claims]
        + [row['known'] for row in tables.incidents]
        + [row['observed'] for row in tables.exposures]
        + [row['received'] for row in tables.links]
        + [row['agreed'] for row in tables.commitments])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='投诉关联证据与联合复核')
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1] / 'data'))
    parser.add_argument('--as-of', type=int, default=None,
                        help='按该时点重放陈述与信号；默认取资料中的最大日期')
    parser.add_argument('--audit', default=None,
                        help='只输出该活动的异常复盘（分母、关联、席位、已送材料）')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出')
    args = parser.parse_args(argv)
    tables = load(args.data)
    as_of = latest_day(tables) if args.as_of is None else args.as_of
    if args.audit:
        result = audit_activity(tables, args.audit, as_of)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(_audit_text(result))
        return 0
    if args.json:
        from .seats import plan_all
        from .signals import evaluate, followup
        payload = {
            'as_of': as_of,
            'signals': [evaluate(tables, row) for row in tables.signals
                        if row['compare_at'] <= as_of],
            'followups': [followup(tables, row) for row in tables.signals],
            'seats': plan_all(tables, as_of=as_of),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    print(full_report(tables, as_of))
    return 0


def _audit_text(result: dict) -> str:
    lines = [f'活动 {result["activity"]}（截至 {result["as_of"]}）']
    lines.append('分母完整性：')
    for row in result['denominator_completeness']:
        status = row['status']
        rate = ('—' if row['rate'] is None else f'{row["rate"]:.3f}')
        lines.append(
            f'  - {row["channel"]}：分母 {row["denominator"]} 户，报齐于 '
            f'{row["complete_at"]}，暴露报入至 {row["observed_through"]}，'
            f'率 {rate}，{status}')
    lines.append('可支持的关联：')
    if not result['support']:
        lines.append('  - 无')
    for row in result['support']:
        detail = f'  - {row["order"]} 家庭 {row["household"]}：{row["status"]}'
        if row['activities']:
            detail += f'，活动 {"/".join(row["activities"])}'
        if row['hints']:
            detail += f'，补证线索 {"/".join(row["hints"])}'
        if row['closed']:
            detail += f'，已关闭凭据 {"/".join(row["closed"])}'
        if row['reason']:
            detail += f'（{row["reason"]}）'
        lines.append(detail)
        for link in row['links']:
            flags = []
            if not link['effective']:
                flags.append('已关闭')
            if not link['course_matches']:
                flags.append('课程不符')
            if not link['bought_in_window']:
                flags.append('购买时间不在区间')
            if link.get('closes'):
                flags.append(f'关闭了 {link["closes"]}')
            suffix = f' [{",".join(flags)}]' if flags else ''
            lines.append(f'      凭据 {link["id"]} {link["verdict"]} '
                         f'@{link["received"]}{suffix}')
    lines.append('席位：')
    if not result['seats']:
        lines.append('  - 无')
    for row in result['seats']:
        state = '占用' if row['state'] == 'occupied' else '未占用'
        basis = f'（{row["basis"]}）' if row['basis'] else ''
        lines.append(f'  - 班次 {row["shift"]} 日 {row["day"]} {state} '
                     f'{row["statement"]}{basis}：{row["reason"]}')
    lines.append('截至该时点已送出的材料：')
    if not result['sent_material']:
        lines.append('  - 无')
    for row in result['sent_material']:
        account = (f'，原账号 {row["account"]}'
                   if row['account'] != row['household'] else '')
        lines.append(f'  - receipt {row["receipt"]} 班次 {row["shift"]} '
                     f'活动 {row["activity"]} 家庭 {row["household"]}{account} '
                     f'投诉 {row["claim"]}（{row["severity"]}，送出于 {row["sent"]}）')
    return '\n'.join(lines)


if __name__ == '__main__':
    raise SystemExit(main())
