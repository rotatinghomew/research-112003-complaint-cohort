"""把可处理的事项排进联合复核席位，而不是按报警条数排榜。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .model import Statement, TimeSlice
from .signals import evaluate

SERIOUS = 'high'


@dataclass(frozen=True)
class Seat:
    shift: str
    statement_key: str
    household: str
    order: str
    claims: Tuple[str, ...]
    activity: Optional[str]
    basis: str            # serious / anomaly / serious_pending
    serious: bool
    reason: str
    activities: Tuple[str, ...] = ()


@dataclass
class CommitmentView:
    receipt: str
    shift: str
    activity: str
    household: str
    claim: str
    agreed: int
    original_scope_kept: bool
    transfer_suspended: bool
    remaining_actionable: bool
    note: str
    current_activity: Optional[str] = None
    current_status: str = ''
    severity: str = ''


@dataclass
class ShiftPlan:
    shift: str
    day: int
    jurisdiction: str
    slots: int
    min_households: int
    min_high: int
    commitments: List[CommitmentView] = field(default_factory=list)
    seats: List[Seat] = field(default_factory=list)
    free_slots: int = 0
    households: Tuple[str, ...] = ()
    high_count: int = 0
    status: str = ''
    gaps: List[str] = field(default_factory=list)
    evidence_requests: List[dict] = field(default_factory=list)
    watchpoints: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    excluded: List[dict] = field(default_factory=list)

    @property
    def distinct_households(self) -> int:
        return len(self.households)

    @property
    def receipt_groups(self) -> List[dict]:
        """同一 receipt 是一个席位；组内逐条说明哪些部分仍可继续。"""
        groups: Dict[str, dict] = {}
        for view in self.commitments:
            group = groups.setdefault(view.receipt, {
                'receipt': view.receipt, 'shift': view.shift,
                'households': set(), 'claims': [], 'activities': set(),
                'disputed': [], 'remaining': [], 'notes': []})
            group['households'].add(view.household)
            group['claims'].append(view.claim)
            group['activities'].add(view.activity)
            if view.transfer_suspended:
                group['disputed'].append(view.claim)
            elif view.remaining_actionable:
                group['remaining'].append(view.claim)
            group['notes'].append(view.note)
        result = []
        for group in groups.values():
            group['households'] = sorted(group['households'])
            group['activities'] = sorted(group['activities'])
            result.append(group)
        return sorted(result, key=lambda item: item['receipt'])


def _claims_by_id(tables) -> Dict[str, dict]:
    return {row['claim']: row for row in tables.claims}


def _statement_activity(statement: Statement) -> Optional[str]:
    return statement.attribution.activity


def _available_signals(tables, day: int) -> Dict[str, dict]:
    """班次当日已发出的信号请求，按活动取最新一次比较。"""
    latest: Dict[str, dict] = {}
    for signal in tables.signals:
        if signal['compare_at'] > day:
            continue
        old = latest.get(signal['activity'])
        if old is None or signal['compare_at'] > old['compare_at']:
            latest[signal['activity']] = signal
    return latest


def _signal_states(tables, day: int) -> Dict[str, dict]:
    """按班次当日已知的材料评估各活动的已发信号（保留发出时的判断）。"""
    states = {}
    for activity, signal in _available_signals(tables, day).items():
        states[activity] = evaluate(tables, signal)
    return states


def _commitment_views(tables, shift: dict, day: int) -> List[CommitmentView]:
    """同一 receipt 只算一个已占席位；按 day 当日已知材料保留办理归属。"""
    claims = _claims_by_id(tables)
    original_slice = TimeSlice(tables, day)
    views = []
    seen = set()
    for row in tables.commitments:
        if row['shift'] != shift['shift']:
            continue
        claim = claims.get(row['claim'])
        order = claim['order'] if claim else None
        agreed_slice = TimeSlice(tables, row['agreed'])
        original = agreed_slice.attribution(order) if order else None
        current = original_slice.attribution(order) if order else None
        kept = bool(original and original.status == 'confirmed'
                    and original.activity == row['activity'])
        suspended = bool(current and (
            current.status in ('withdrawn', 'conflict')
            or current.activity != row['activity']))
        actionable = bool(claim and claim['state'] == 'open')
        household = original_slice.household(row['household'])
        if suspended:
            note = ('确认归属后来被撤回或抵触；原办理归属保留，'
                    '有争议部分暂停转交，余下仍可继续')
        elif kept:
            note = '认领时确认归属成立，原办理归属仍有效'
        elif original and original.status in ('withdrawn', 'conflict'):
            note = '认领时归属已撤回或抵触，按当时材料保留原办理记录'
        elif original and original.status == 'hint_only':
            note = '认领时仅有补证线索，按当时材料未扩展活动范围'
        else:
            note = '按当时材料未能确认该活动归属，仅保留原办理记录'
        if row['receipt'] in seen:
            note += '（同 receipt 的另一行材料，不重复占席）'
        seen.add(row['receipt'])
        views.append(CommitmentView(
            receipt=row['receipt'], shift=row['shift'], activity=row['activity'],
            household=household, claim=row['claim'], agreed=row['agreed'],
            original_scope_kept=kept, transfer_suspended=suspended,
            remaining_actionable=actionable and not suspended, note=note,
            current_activity=current.activity if current else None,
            current_status=current.status if current else 'none',
            severity=claim['severity'] if claim else ''))
    return views


def plan_shift(tables, shift: dict, prior_households: Optional[Set[str]] = None,
               as_of: Optional[int] = None) -> ShiftPlan:
    """为一个班次排席位。

    - 已认领家庭（此前班次或本班次的 commitments）不再进入新的组合；
    - 同一 receipt 的已认领材料只占一个席位；
    - 先排政策规定的严重争议；非严重事项只有在该活动当日已有异常证据时才占席；
    - 待核归属不用于扩展活动范围，也不把两个活动并在一起；
    - 认为异常但未达到最低确认覆盖的，只留补证需求，不占联合席位；
    - 独立家庭覆盖不足时如实留缺口，不空排席位凑数。
    """
    day = shift['day']
    plan_day = min(day, as_of) if as_of is not None else day
    slice_ = TimeSlice(tables, plan_day)
    prior_households = set(prior_households or ())
    commitments = _commitment_views(tables, shift, plan_day)
    claimed = set(prior_households) | {c.household for c in commitments}
    state_by_activity = _signal_states(tables, plan_day)
    anomalous = {a for a, e in state_by_activity.items() if e['status'] == 'abnormal'}
    suspected = {
        a for a, e in state_by_activity.items()
        if e['status'] in ('insufficient', 'no_denominator', 'no_baseline')
    }
    plan = ShiftPlan(
        shift=shift['shift'], day=day, jurisdiction=shift['jurisdiction'],
        slots=shift['slots'], min_households=shift['min_households'],
        min_high=shift['min_high'], commitments=commitments)

    distinct_receipts = {c.receipt for c in commitments}
    plan.free_slots = max(0, shift['slots'] - len(distinct_receipts))

    candidates = [
        item for item in slice_.statements()
        if item.jurisdiction == shift['jurisdiction']
    ]
    serious: List[Statement] = []
    pending_serious: List[Statement] = []
    anomaly_backed: List[Statement] = []
    requested = set()
    for item in candidates:
        attribution = item.attribution
        activity = attribution.activity
        if item.household in claimed:
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': activity, 'activities': list(attribution.activities),
                'kind': 'claimed',
                'reason': ('家庭已在此前或本班次认领，限制新的组合；'
                           '可在原认领事项内继续办理，不另开组合席位')})
            continue
        if attribution.status == 'conflict':
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': activity, 'activities': list(attribution.activities),
                'kind': 'conflict',
                'reason': '有效确认凭据互相抵触，待核；不得按到达顺序择一或跨活动合并'})
            if item.severity == SERIOUS:
                pending_serious.append(item)
            continue
        if attribution.status == 'withdrawn':
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': activity, 'activities': list(attribution.activities),
                'kind': 'withdrawn',
                'reason': '确认归属已被撤回或排除，按当时材料不能扩展活动范围'})
            if item.severity == SERIOUS:
                pending_serious.append(item)
            continue
        if item.severity == SERIOUS:
            serious.append(item)
            continue
        if activity and activity in anomalous:
            anomaly_backed.append(item)
        elif activity and activity in suspected:
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': activity, 'activities': list(attribution.activities),
                'kind': 'insufficient',
                'reason': f'{activity} 当日只有不足证据，不占联合席位，改列补证需求'})
            marker = ('statement_coverage', activity)
            if marker not in requested:
                requested.add(marker)
                plan.evidence_requests.append({
                    'kind': 'statement_coverage',
                    'statement': item.key, 'activity': activity,
                    'status': state_by_activity[activity]['status'],
                    'reason': state_by_activity[activity]['channels'][0]['reason']
                    if state_by_activity[activity]['channels'] else '覆盖不足',
                })
        else:
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': activity, 'activities': list(attribution.activities),
                'kind': 'no_anomaly',
                'reason': '非严重争议，且当日没有该活动的异常证据，不占联合席位'})

    # 严重争议优先：每个家庭只排一席，先确认归属，再待核归属。
    serious.sort(key=lambda item: (
        0 if _statement_activity(item) else 1, item.order, item.household))
    chosen: List[Statement] = []
    seen_households: Set[str] = set()
    for item in serious + pending_serious:
        if len(chosen) >= plan.free_slots:
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': _statement_activity(item),
                'activities': list(item.attribution.activities), 'kind': 'slots_full',
                'reason': '席位已满；严重争议按受理顺序顺延，不压缩独立家庭覆盖'})
            continue
        if item.household in seen_households:
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': _statement_activity(item),
                'activities': list(item.attribution.activities), 'kind': 'duplicate_household',
                'reason': '同户已有席位；重复排席不增加独立家庭覆盖'})
            continue
        seen_households.add(item.household)
        chosen.append(item)
    # 异常证据支持的席位：只在仍有空位时补，且不挤占独立家庭覆盖。
    for item in anomaly_backed:
        if len(chosen) >= plan.free_slots:
            plan.excluded.append({
                'statement': item.key, 'household': item.household,
                'activity': _statement_activity(item),
                'activities': list(item.attribution.activities), 'kind': 'slots_full',
                'reason': '严重争议已占满席位；异常证据事项顺延，不挤占严重争议'})
            break
        if item.household in seen_households:
            continue
        seen_households.add(item.household)
        chosen.append(item)

    for item in chosen:
        attribution = item.attribution
        if item.severity == SERIOUS and attribution.activity:
            basis, reason = 'serious', '政策规定的严重争议，确认归属支持'
        elif item.severity == SERIOUS:
            basis = 'serious_pending'
            reason = '政策规定的严重争议；归属待核，只作单件办理，不扩展活动范围'
        else:
            basis = 'anomaly'
            reason = f'{attribution.activity} 当日已有异常证据'
        plan.seats.append(Seat(
            shift=shift['shift'], statement_key=item.key, household=item.household,
            order=item.order, claims=item.claims, activity=attribution.activity,
            basis=basis, serious=item.severity == SERIOUS, reason=reason,
            activities=attribution.activities))

    plan.households = tuple(sorted(
        {s.household for s in plan.seats} | {c.household for c in commitments}))
    claims = _claims_by_id(tables)
    plan.high_count = sum(1 for s in plan.seats if s.serious) + sum(
        1 for c in commitments
        if claims.get(c.claim) and claims[c.claim]['severity'] == SERIOUS)

    occupied = len(distinct_receipts) + len(plan.seats)
    if occupied < shift['slots']:
        plan.gaps.append(
            f'仅 {occupied}/{shift["slots"]} 席有可处理事项')
    if plan.distinct_households < shift['min_households']:
        plan.gaps.append(
            f'独立家庭覆盖 {plan.distinct_households} 户，'
            f'低于最低 {shift["min_households"]} 户；不空排席位凑数')
    if plan.high_count < shift['min_high']:
        plan.gaps.append(
            f'高严重度事项 {plan.high_count} 项，低于最低 {shift["min_high"]} 项')
    if shift['slots'] < shift['min_households']:
        plan.gaps.append(
            f'班次配置仅 {shift["slots"]} 席，低于最低独立家庭覆盖 '
            f'{shift["min_households"]} 户，按现配置无法满足')
    plan.status = 'ready' if not plan.gaps else 'under_covered'

    # 严重争议不因后续信号失效或归属更正而撤下实体判断。
    for item in serious + pending_serious:
        activity = _statement_activity(item)
        if item.severity == SERIOUS and activity and activity not in anomalous:
            plan.watchpoints.append(
                f'{item.key} 为严重争议；{activity} 当日信号'
                f'{state_by_activity.get(activity, {}).get("status", "未发出")}，'
                '信号失效或归属更正不替代对已受理严重争议的判断')
    for commitment in commitments:
        if commitment.transfer_suspended and claims.get(commitment.claim) \
                and claims[commitment.claim]['severity'] == SERIOUS:
            plan.watchpoints.append(
                f'{commitment.receipt} 的 {commitment.claim} 为严重争议；'
                '归属撤回只暂停争议部分转交，不撤销实体判断')

    # 补证需求：证据不足/无分母的信号，与待核归属分开列。
    for activity, state in state_by_activity.items():
        for channel in state['channels']:
            if channel['status'] in ('insufficient', 'no_denominator', 'no_baseline'):
                plan.evidence_requests.append({
                    'kind': 'signal',
                    'activity': activity,
                    'channel': channel['channel'],
                    'status': channel['status'],
                    'reason': channel['reason'],
                    'detail': {
                        'complaints': channel['complaints'],
                        'denominator': channel['denominator'],
                        'baseline': f'{channel["baseline_k"]}/{channel["baseline_n"]}',
                        'coverage_complete_at': channel['coverage_complete_at'],
                    },
                })
    for item in pending_serious:
        plan.evidence_requests.append({
            'kind': 'attribution',
            'order': item.order,
            'household': item.household,
            'status': item.attribution.status,
            'reason': item.attribution.reason or '只有补证线索，不能扩展已认定范围',
        })
    return plan


def plan_all(tables, as_of: Optional[int] = None) -> List[ShiftPlan]:
    plans = []
    claimed: Set[str] = set()
    for shift in sorted(tables.shifts, key=lambda row: (row['day'], row['shift'])):
        if as_of is not None and shift['day'] > as_of:
            break
        plan = plan_shift(tables, shift, claimed, as_of=as_of)
        claimed |= {c.household for c in plan.commitments}
        claimed |= {seat.household for seat in plan.seats}
        plans.append(plan)
    return plans
