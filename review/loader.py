"""读取本地 CSV 资料。所有表都是 UTF-8、首行列名的 CSV。"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

TABLE_COLUMNS = {
    'purchases': ('order', 'household', 'campaign_hint', 'course', 'bought', 'jurisdiction'),
    'claims': ('claim', 'revision', 'received', 'order', 'account', 'severity', 'state'),
    'identity': ('id', 'first', 'second', 'received', 'verdict'),
    'links': ('id', 'subject', 'course', 'activity', 'start', 'end', 'received', 'verdict', 'ref'),
    'shifts': ('shift', 'day', 'jurisdiction', 'slots', 'min_households', 'min_high'),
    'commitments': ('receipt', 'shift', 'activity', 'household', 'claim', 'agreed'),
    'exposures': ('receipt', 'activity', 'household', 'channel', 'age_day', 'observed'),
    'incidents': ('receipt', 'report_id', 'known', 'independent_review'),
    'coverage': ('activity', 'channel', 'completed_age', 'known'),
    'signals': ('activity', 'compare_at', 'age_day', 'followup', 'reference_before', 'min_families', 'scope'),
}

INT_COLUMNS = {
    'purchases': ('bought',),
    'claims': ('revision', 'received'),
    'identity': ('received',),
    'links': ('start', 'end', 'received'),
    'shifts': ('day', 'slots', 'min_households', 'min_high'),
    'commitments': ('agreed',),
    'exposures': ('age_day', 'observed'),
    'incidents': ('known',),
    'coverage': ('completed_age', 'known'),
    'signals': ('compare_at', 'age_day', 'followup', 'reference_before', 'min_families'),
}


@dataclass(frozen=True)
class Tables:
    purchases: List[dict]
    claims: List[dict]
    identity: List[dict]
    links: List[dict]
    shifts: List[dict]
    commitments: List[dict]
    exposures: List[dict]
    incidents: List[dict]
    coverage: List[dict]
    signals: List[dict]

    def by(self, table: str, key: str) -> Dict[str, List[dict]]:
        grouped: Dict[str, List[dict]] = {}
        for row in getattr(self, table):
            grouped.setdefault(row[key], []).append(row)
        return grouped


def read_table(data_dir, name: str) -> List[dict]:
    path = Path(data_dir) / f'{name}.csv'
    with path.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    expected = TABLE_COLUMNS[name]
    for row in rows:
        if tuple(row) != expected:
            raise ValueError(f'{name}: 列名不符：{tuple(row)!r}')
        for column in INT_COLUMNS.get(name, ()):
            if row[column] != '':
                row[column] = int(row[column])
    return rows


def load(data_dir) -> Tables:
    return Tables(**{name: read_table(data_dir, name) for name in TABLE_COLUMNS})
