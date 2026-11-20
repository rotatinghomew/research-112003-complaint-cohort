"""核对业务资料的格式与可关联性。"""
import csv
from pathlib import Path
import unittest

DATA = Path(__file__).resolve().parents[1] / 'data'
TABLES = {'purchases': ('order', 'household', 'campaign_hint', 'course', 'bought', 'jurisdiction'), 'claims': ('claim', 'revision', 'received', 'order', 'account', 'severity', 'state'), 'identity': ('id', 'first', 'second', 'received', 'verdict'), 'links': ('id', 'subject', 'course', 'activity', 'start', 'end', 'received', 'verdict', 'ref'), 'shifts': ('shift', 'day', 'jurisdiction', 'slots', 'min_households', 'min_high'), 'commitments': ('receipt', 'shift', 'activity', 'household', 'claim', 'agreed')}
RELATION = ('claims', 'order', 'purchases', 'order')


class SourceContract(unittest.TestCase):
    def read(self, table):
        with (DATA / f'{table}.csv').open(encoding='utf-8', newline='') as file:
            return list(csv.DictReader(file))

    def test_shape(self):
        for name, columns in TABLES.items():
            with self.subTest(table=name):
                rows = self.read(name)
                self.assertTrue(rows)
                for row in rows:
                    self.assertEqual(tuple(row), columns)
                    self.assertNotIn(None, row.values())

    def test_references(self):
        left, left_key, right, right_key = RELATION
        self.assertLessEqual({row[left_key] for row in self.read(left)}, {row[right_key] for row in self.read(right)})

    def test_source_variation(self):
        self.assertGreater(sum(row['subject'] == 'o4' and row['verdict'] == 'confirmed' for row in self.read('links')), 1)
        self.assertTrue(any(row['ref'] == 'l2' and int(row['received']) > 29 for row in self.read('links')))
