"""按渠道、活动日龄和观察完整性评估独立家庭投诉率。"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from .model import TimeSlice

ALPHA = 0.05
STATUS_ORDER = (
    'abnormal', 'insufficient', 'no_denominator', 'sparse',
    'no_baseline', 'no_anomaly',
)


def _binom_tail(k: int, n: int, p: float) -> float:
    """P(X >= k)，X ~ Binomial(n, p)。"""
    if k <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i) * p ** i * (1 - p) ** (n - i)
    return min(1.0, total)


def _coverage(tables, activity: str, channel: str, age_day: int,
              cutoff: int) -> Optional[int]:
    """若该活动该渠道的 age_day 覆盖已在 cutoff 前报齐，返回报齐日。"""
    best = None
    for row in tables.coverage:
        if row['activity'] != activity or row['channel'] != channel:
            continue
        if row['completed_age'] >= age_day and row['known'] <= cutoff:
            if best is None or row['known'] < best:
                best = row['known']
    return best


def _household_exposures(slice_: TimeSlice, activity: str, age_day: int,
                         cutoff: int) -> Dict[str, Dict[str, dict]]:
    """活动开场 age_day 天内、且在 cutoff 前已报入的购买家庭，按渠道分组。"""
    result: Dict[str, Dict[str, dict]] = {}
    for row in slice_.tables.exposures:
        if row['activity'] != activity or row['age_day'] > age_day:
            continue
        if row['observed'] > cutoff:
            continue
        household = slice_.household(row['household'])
        result.setdefault(row['channel'], {})[household] = row
    return result


def _complaint_households(slice_: TimeSlice, activity: str,
                          cutoff: int) -> Dict[str, List[str]]:
    """截至 cutoff，可归到该活动的投诉家庭。

    历史活动通过 incidents 的 receipt 追到购买家庭；当前活动通过当前投诉修订
    及其已确认归属追到家庭。待核归属不进入投诉范围，单独列为补证线索。
    """
    confirmed: Dict[str, List[str]] = {}
    receipts = {
        row['receipt']: row for row in slice_.tables.exposures
        if row['activity'] == activity
    }
    for row in slice_.tables.incidents:
        if row['receipt'] in receipts and row['known'] <= cutoff:
            exposure = receipts[row['receipt']]
            household = slice_.household(exposure['household'])
            confirmed.setdefault(household, []).append(row['report_id'])
    for claim in slice_.claims:
        attribution = slice_.attribution(claim['order'])
        if attribution.status == 'confirmed' and attribution.activity == activity:
            household = slice_.household(claim['account'])
            confirmed.setdefault(household, []).append(claim['claim'])
    return confirmed


def _baseline(slice_: TimeSlice, signal: dict, evaluated_households: set,
              cutoff: int) -> Dict[str, dict]:
    """比较基准：比较日前观察期已满的历史活动，每户只计一次。

    同一户在多个旧活动出现时，只取最早一个观察期已满的活动，避免把同一户
    当成多次独立观察；参加当前活动的家庭不进入基准。历史投诉只取 cutoff
    前已报入的事件，独立复核结论不充当历史报警。
    """
    age = signal['age_day']
    activity_start: Dict[str, int] = {}
    for row in slice_.tables.exposures:
        if row['observed'] > cutoff or not row['activity'].startswith('old'):
            continue
        activity_start[row['activity']] = min(
            activity_start.get(row['activity'], row['observed']), row['observed'])
    observations = []
    for row in slice_.tables.exposures:
        if row['observed'] > cutoff or not row['activity'].startswith('old'):
            continue
        if row['activity'] == signal['activity']:
            continue
        observations.append((
            activity_start[row['activity']], row['activity'],
            row['age_day'], row['receipt'], row))
    observations.sort(key=lambda item: item[:4])

    baseline: Dict[str, dict] = {}
    seen: set = set()
    for _, activity, _, receipt, row in observations:
        household = slice_.household(row['household'])
        if household in seen or household in evaluated_households:
            continue
        # 最早一次旧活动观察期未满，就不换用同一户后来的活动来充数。
        seen.add(household)
        if _coverage(slice_.tables, activity, row['channel'], age, cutoff) is None:
            continue
        incident = next(
            (item for item in slice_.tables.incidents
             if item['receipt'] == receipt and item['known'] <= cutoff), None)
        entry = baseline.setdefault(
            row['channel'], {'n': 0, 'k': 0, 'activities': set()})
        entry['n'] += 1
        entry['k'] += 1 if incident else 0
        entry['activities'].add(activity)
    return {
        channel: {'n': value['n'], 'k': value['k'],
                  'activities': sorted(value['activities'])}
        for channel, value in baseline.items()
    }


def _overall(channels: List[dict]) -> str:
    statuses = [c['status'] for c in channels]
    if not statuses:
        return 'no_denominator'
    for status in STATUS_ORDER:
        if status in statuses:
            return status
    return statuses[0]


def evaluate(tables, signal: dict, cutoff: Optional[int] = None,
             baseline_cutoff: Optional[int] = None) -> dict:
    """在 cutoff 时点重算信号；baseline_cutoff 固定比较基准的观察截止。

    切点之后才到达的投诉修订、归属凭据、暴露和事件都不参与计算。
    """
    cutoff = signal['compare_at'] if cutoff is None else cutoff
    baseline_cutoff = signal['reference_before'] if baseline_cutoff is None else baseline_cutoff
    slice_ = TimeSlice(tables, cutoff)
    age = signal['age_day']
    channel_rows = _household_exposures(slice_, signal['activity'], age, cutoff)
    complaints = _complaint_households(slice_, signal['activity'], cutoff)
    evaluated = set().union(*channel_rows.values()) if channel_rows else set()
    baseline = _baseline(slice_, signal, evaluated, baseline_cutoff)
    pending = _pending_scope(slice_, signal['activity'])
    channels = []
    for channel in sorted(channel_rows):
        households = channel_rows[channel]
        n = len(households)
        k = len(set(households) & set(complaints))
        complete_at = _coverage(slice_.tables, signal['activity'], channel, age, cutoff)
        base = baseline.get(channel, {'n': 0, 'k': 0, 'activities': []})
        base_n = base['n']
        activity_count = max(1, len(base['activities']))
        entry = {
            'channel': channel,
            'denominator': n,
            'complaints': k,
            'complaint_households': sorted(set(households) & set(complaints)),
            'rate': (k / n) if (n and complete_at is not None) else None,
            'coverage_complete_at': complete_at,
            'denominator_observed_through': max(
                (row['observed'] for row in households.values()), default=None),
            'baseline_n': base['n'],
            'baseline_k': base['k'],
            'baseline_activities': base['activities'],
            'baseline_rate': (base['k'] / base['n']) if base['n'] else None,
            'baseline_avg_denominator': (base_n / activity_count) if base_n else None,
            'high_volume': bool(base_n and n > 1.5 * base_n / activity_count),
            'volume_note': '',
            'p_value': None,
            'status': None,
            'reason': '',
        }
        if complete_at is None:
            entry['status'] = 'no_denominator'
            entry['reason'] = (
                f'{signal["activity"]}/{channel} 在 {cutoff} 前未报齐 {age} 日龄覆盖')
        elif n < signal['min_families']:
            entry['status'] = 'sparse'
            entry['reason'] = (
                f'合格购买家庭 {n} 少于最小家庭数 {signal["min_families"]}')
        elif base['n'] < signal['min_families']:
            entry['status'] = 'no_baseline'
            entry['reason'] = (
                f'{channel} 同期完整基准只有 {base["n"]} 户，不足以估计')
        else:
            p0 = base['k'] / base['n']
            p = _binom_tail(k, n, p0)
            entry['p_value'] = p
            if k / n > p0 and p < ALPHA:
                entry['status'] = 'abnormal'
                entry['reason'] = (
                    f'{k}/{n} 高于基准 {base["k"]}/{base["n"]}，'
                    f'单侧精确检验 p={p:.3f}')
                if entry['high_volume']:
                    entry['volume_note'] = (
                        f'购买家庭 {n} 户也多于基准每次活动平均 '
                        f'{base_n / activity_count:.1f} 户；这里既有规模，'
                        '独立家庭投诉率本身也高于基准')
            elif k / n > p0:
                entry['status'] = 'insufficient'
                entry['reason'] = (
                    f'{k}/{n} 高于基准 {base["k"]}/{base["n"]}，'
                    f'但样本不足以确认（p={p:.3f}）')
            else:
                entry['status'] = 'no_anomaly'
                entry['reason'] = f'{k}/{n} 未高于基准 {base["k"]}/{base["n"]}'
                if entry['high_volume']:
                    entry['volume_note'] = (
                        f'购买家庭 {n} 户多于基准每次活动平均 '
                        f'{base_n / activity_count:.1f} 户，但独立家庭投诉率未升高；'
                        '这是购买量较大，不是异常率')
        channels.append(entry)
    return {
        'signal': signal,
        'evaluated_at': cutoff,
        'baseline_cutoff': baseline_cutoff,
        'channels': channels,
        'pending_scope': pending,
        'status': _overall(channels),
    }


def _pending_scope(slice_: TimeSlice, activity: str) -> List[dict]:
    """指向该活动但未确认归属的订单；不进入分母/分子。

    互相抵触的确认（conflict）对涉及的每个活动都是待核范围，但只在陈述里
    单列，不用来把两个活动并成一份。
    """
    result = []
    for order in sorted({row['subject'] for row in slice_.tables.links}):
        attribution = slice_.attribution(order)
        if activity in attribution.hints and attribution.activity != activity:
            status = attribution.status
        elif attribution.status == 'conflict' and activity in attribution.activities:
            status = 'conflict'
        else:
            continue
        purchase = next(
            (row for row in slice_.tables.purchases if row['order'] == order), None)
        result.append({
            'order': order,
            'household': slice_.household(purchase['household']) if purchase else None,
            'status': ('confirmed_elsewhere' if attribution.activity
                       else status),
            'confirmed_activity': attribution.activity,
            'candidate_activities': list(attribution.activities),
            'hint_activities': list(attribution.hints),
            'reason': attribution.reason,
        })
    return result


def followup(tables, signal: dict) -> dict:
    """在追踪期后重看当日信号；当日判断原样保留，不被后续归属改写。"""
    original = evaluate(tables, signal)
    later = signal['compare_at'] + signal['followup']
    tail = evaluate(tables, signal, cutoff=later,
                    baseline_cutoff=signal['reference_before'])
    original_status = original['status']
    if original_status in ('no_denominator', 'no_baseline'):
        verdict = 'not_screened'
        note = (f'当日无法出率，未形成判警；追踪期末状态为 {tail["status"]}。'
                '后续补到分母/归属只作参考资料，不改写当日判断')
    elif original_status == 'sparse':
        verdict = 'unresolved_sparse'
        note = '当日样本过小，追踪期后仍须区分“活动小、证据不足”与“确无异常”'
    elif original_status in ('insufficient',):
        if tail['status'] == 'abnormal':
            verdict = 'confirmed'
            note = '追踪期后异常证据达到确认程度'
        elif tail['status'] == 'no_anomaly':
            verdict = 'not_confirmed'
            note = '追踪期后未见异常，但当日只是证据不足，不能倒推为当日无异常'
        else:
            verdict = 'unresolved_insufficient'
            note = '追踪期后证据仍不足，不能当作确无异常'
    elif original_status == 'no_anomaly':
        if tail['status'] == 'no_anomaly':
            verdict = 'no_alert_confirmed'
            note = '当日未形成判警，追踪期内也未见异常'
        elif tail['status'] == 'abnormal':
            verdict = 'late_anomaly'
            note = '当日未形成判警，追踪期内才出现异常；不改写当日判断'
        else:
            verdict = 'no_alert_unresolved'
            note = f'当日未形成判警；追踪期末状态 {tail["status"]}'
    elif tail['status'] == 'abnormal':
        verdict = 'confirmed'
        note = '追踪期后仍为异常'
    elif tail['status'] == 'no_anomaly':
        verdict = 'refuted'
        note = '追踪期后未见异常'
    else:
        verdict = 'unresolved'
        note = f'追踪期后状态 {tail["status"]}'
    changes = _scope_changes(original, tail)
    return {
        'original': original,
        'followup': tail,
        'followup_at': later,
        'verdict': verdict,
        'note': note,
        'scope_changes': changes,
    }


def _scope_changes(original: dict, tail: dict) -> List[dict]:
    """比较两个时点的投诉家庭，标记追踪期内新补到的归属；不回写当日判警。"""
    before = {
        (channel['channel'], h)
        for channel in original['channels']
        for h in channel['complaint_households']
    }
    changes = []
    for channel in tail['channels']:
        for household in channel['complaint_households']:
            if (channel['channel'], household) not in before:
                changes.append({'channel': channel['channel'], 'household': household})
    return changes
