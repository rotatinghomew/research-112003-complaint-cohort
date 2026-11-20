"""按某个时点重放身份、投诉修订与活动归属。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .loader import Tables

# 只有这些结论可以提示补证，不能把主体扩进已认定范围。
HINT_VERDICTS = frozenset({'shared_support', 'shared_payee', 'same_name', 'candidate'})
# 这些记录引用另一条凭据，表示撤回或明确排除。
CLOSING_VERDICTS = frozenset({'withdrawn', 'excluded'})


def link_covers_purchase(link: dict, purchase: Optional[dict]) -> Tuple[bool, str]:
    """活动证据必须同时覆盖课程与购买时间。"""
    if purchase is None:
        return False, '订单不存在'
    if link['course'] != purchase['course']:
        return False, '课程不符'
    if not (link['start'] <= purchase['bought'] <= link['end']):
        return False, '购买时间不在活动区间'
    return True, ''


@dataclass(frozen=True)
class Attribution:
    order: str
    status: str                 # confirmed / conflict / withdrawn / hint_only / none
    activities: Tuple[str, ...] = ()
    hints: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()
    closed: Tuple[str, ...] = ()
    reason: str = ''

    @property
    def activity(self) -> Optional[str]:
        return self.activities[0] if self.status == 'confirmed' and len(self.activities) == 1 else None


class TimeSlice:
    """同一时点的只读视图。所有日期都满足 day <= as_of。"""

    def __init__(self, tables: Tables, as_of: int):
        self.tables = tables
        self.as_of = as_of
        self._household = self._resolve_households()
        self._claims = self._current_claims()
        self._purchases = {row['order']: row for row in tables.purchases}
        self._attributions: Dict[str, Attribution] = {
            order: self._attribute(order) for order in self._purchases
        }

    # ---- 家庭关系 -------------------------------------------------------
    def _resolve_households(self) -> Dict[str, str]:
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if ra > rb:              # 代号最小的作代表，保证可重复
                ra, rb = rb, ra
            parent[rb] = ra

        for row in self.tables.identity:
            if row['received'] <= self.as_of and row['verdict'] == 'confirmed':
                union(row['first'], row['second'])
        return {name: find(name) for name in list(parent)}

    def household(self, account: str) -> str:
        return self._household.get(account, account)

    # ---- 投诉当前修订 ---------------------------------------------------
    def _current_claims(self) -> List[dict]:
        latest: Dict[str, dict] = {}
        for row in self.tables.claims:
            if row['received'] > self.as_of:
                continue
            old = latest.get(row['claim'])
            if old is None or (row['revision'], row['received']) > (old['revision'], old['received']):
                latest[row['claim']] = row
        return [latest[key] for key in sorted(latest)]

    @property
    def claims(self) -> List[dict]:
        return list(self._claims)

    def claim(self, claim_id: str) -> Optional[dict]:
        return next((row for row in self._claims if row['claim'] == claim_id), None)

    # ---- 活动归属 -------------------------------------------------------
    def _link_valid(self, link: dict) -> Tuple[bool, str]:
        return link_covers_purchase(link, self._purchases.get(link['subject']))

    def effective_links(self) -> List[dict]:
        links = [row for row in self.tables.links if row['received'] <= self.as_of]
        closing: Dict[str, List[int]] = defaultdict(list)
        for row in links:
            if row['verdict'] in CLOSING_VERDICTS and row['ref']:
                closing[row['ref']].append(row['received'])
        effective = []
        for row in links:
            if row['verdict'] in CLOSING_VERDICTS:
                continue
            if row['id'] in closing:      # 已被撤回或排除，不再是有效凭据
                continue
            effective.append(row)
        return effective

    def _attribute(self, order: str) -> Attribution:
        links = [row for row in self.effective_links() if row['subject'] == order]
        confirmed, hints, evidence, closed = [], [], [], []
        for row in links:
            valid, _ = self._link_valid(row)
            if row['verdict'] == 'confirmed' and valid:
                confirmed.append(row)
                evidence.append(row['id'])
            elif row['verdict'] in HINT_VERDICTS:
                hints.append(row)
            elif row['verdict'] == 'confirmed' and not valid:
                hints.append(row)
        # 已关闭的确认凭据用于解释“曾确认、后撤回/排除”，不参与当前认定。
        for row in self.tables.links:
            if row['received'] > self.as_of:
                continue
            if row['verdict'] in CLOSING_VERDICTS and row['ref']:
                target = next((item for item in self.tables.links if item['id'] == row['ref']), None)
                if (target is not None and target['received'] <= self.as_of
                        and target['subject'] == order
                        and target['verdict'] == 'confirmed'
                        and target['id'] not in closed):
                    closed.append(target['id'])
        activities = tuple(sorted({row['activity'] for row in confirmed}))
        hint_activities = tuple(sorted({row['activity'] for row in hints} - set(activities)))
        if len(activities) > 1:
            return Attribution(order, 'conflict', activities, hint_activities,
                               tuple(sorted(evidence)), tuple(sorted(set(closed))),
                               '同一订单存在互相抵触的有效确认凭据')
        if activities:
            return Attribution(order, 'confirmed', activities, hint_activities,
                               tuple(sorted(evidence)), tuple(sorted(set(closed))), '')
        if closed:
            return Attribution(order, 'withdrawn', (), hint_activities, (),
                               tuple(sorted(set(closed))), '确认凭据已被撤回或排除')
        if hints:
            return Attribution(order, 'hint_only', (), hint_activities, (),
                               tuple(sorted(set(closed))), '仅有补证线索，不能扩展已认定范围')
        return Attribution(order, 'none')

    def attribution(self, order: str) -> Attribution:
        return self._attributions.get(order, Attribution(order, 'none'))

    # ---- 陈述 -----------------------------------------------------------
    def statements(self) -> List['Statement']:
        grouped: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        for claim in self._claims:
            purchase = self._purchases.get(claim['order'])
            if purchase is None:
                continue
            grouped[(self.household(claim['account']), claim['order'])].append(claim)
        result = []
        for (household, order), claims in grouped.items():
            purchase = self._purchases[order]
            severity = 'high' if any(row['severity'] == 'high' for row in claims) else 'normal'
            result.append(Statement(
                household=household,
                order=order,
                claims=tuple(sorted(row['claim'] for row in claims)),
                severity=severity,
                state=('open' if any(row['state'] == 'open' for row in claims) else 'closed'),
                course=purchase['course'],
                bought=purchase['bought'],
                jurisdiction=purchase['jurisdiction'],
                campaign_hint=purchase['campaign_hint'],
                attribution=self.attribution(order),
            ))
        return sorted(result, key=lambda item: (item.order, item.household))


@dataclass(frozen=True)
class Statement:
    household: str
    order: str
    claims: Tuple[str, ...]
    severity: str
    state: str
    course: str
    bought: int
    jurisdiction: str
    campaign_hint: str
    attribution: Attribution

    @property
    def key(self) -> str:
        return f'{self.order}/{self.household}'
