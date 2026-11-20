"""核对关联、信号与席位规则在这批资料上的行为。"""
import unittest

from review.audit import audit_activity, review_commitments
from review.loader import load
from review.model import TimeSlice, link_covers_purchase
from review.seats import plan_all, plan_shift
from review.signals import evaluate, followup

DATA = __import__('pathlib').Path(__file__).resolve().parents[1] / 'data'


def tables():
    return load(DATA)


class Statements(unittest.TestCase):
    def test_same_household_same_order_merges(self):
        slice_ = TimeSlice(tables(), 30)
        keys = {(s.household, s.order) for s in slice_.statements()}
        self.assertEqual(len(keys), len(slice_.statements()))
        merged = next(s for s in slice_.statements() if s.order == 'o1')
        self.assertEqual(merged.claims, ('x1', 'x2'))
        self.assertEqual(merged.household, 'alias1')

    def test_same_household_different_activities_stay_separate(self):
        slice_ = TimeSlice(tables(), 30)
        household = next(s for s in slice_.statements() if s.order == 'o5').household
        self.assertEqual(household, 'alias1')
        orders = {s.order for s in slice_.statements() if s.household == household}
        self.assertEqual(orders, {'o1', 'o5'})

    def test_candidate_identity_does_not_merge(self):
        slice_ = TimeSlice(tables(), 30)
        self.assertNotEqual(slice_.household('h4'), slice_.household('h5'))

    def test_confirmation_before_receipt_does_not_apply(self):
        self.assertEqual(TimeSlice(tables(), 23).household('h1'), 'h1')
        self.assertEqual(TimeSlice(tables(), 24).household('h1'), 'alias1')

    def test_revision_replaces_earlier_version(self):
        latest = {row['claim']: row for row in TimeSlice(tables(), 33).claims}
        self.assertEqual(latest['x3']['revision'], 2)
        self.assertEqual(len(latest), 8)


class Attribution(unittest.TestCase):
    def test_hint_only_verdicts_do_not_expand_scope(self):
        slice_ = TimeSlice(tables(), 40)
        result = slice_.attribution('o3')
        self.assertIn('a', result.hints)
        self.assertEqual(result.activity, 'b')

    def test_shared_payee_is_hint_only(self):
        result = TimeSlice(tables(), 72).attribution('o7')
        self.assertEqual(result.status, 'hint_only')
        self.assertEqual(result.hints, ('a',))
        self.assertIsNone(result.activity)

    def test_withdrawn_link_closes_confirmation(self):
        self.assertEqual(TimeSlice(tables(), 31).attribution('o2').status, 'confirmed')
        self.assertEqual(TimeSlice(tables(), 32).attribution('o2').status, 'withdrawn')
        self.assertIn('l2', TimeSlice(tables(), 32).attribution('o2').closed)

    def test_conflicting_confirmations_are_unresolved(self):
        result = TimeSlice(tables(), 32).attribution('o4')
        self.assertEqual(result.status, 'conflict')
        self.assertIsNone(result.activity)

    def test_exclusion_removes_later_claim(self):
        result = TimeSlice(tables(), 34).attribution('o4')
        self.assertEqual(result.status, 'confirmed')
        self.assertEqual(result.activity, 'b')

    def test_course_and_window_must_cover_purchase(self):
        purchase = {'order': 'o1', 'course': 'c1', 'bought': 12}
        good = {'subject': 'o1', 'course': 'c1', 'start': 1, 'end': 30}
        wrong_course = dict(good, course='c2')
        too_late = dict(good, start=13)
        self.assertEqual(link_covers_purchase(good, purchase), (True, ''))
        self.assertEqual(link_covers_purchase(wrong_course, purchase)[1], '课程不符')
        self.assertEqual(link_covers_purchase(too_late, purchase)[1], '购买时间不在活动区间')


class Signals(unittest.TestCase):
    def signal(self, activity, compare_at):
        return next(row for row in tables().signals
                    if row['activity'] == activity and row['compare_at'] == compare_at)

    def test_missing_coverage_produces_no_rate(self):
        result = evaluate(tables(), self.signal('b', 28))
        self.assertEqual(result['status'], 'no_denominator')
        self.assertIsNone(result['channels'][0]['rate'])

    def test_abnormal_only_after_denominator_complete(self):
        result = evaluate(tables(), self.signal('b', 34))
        self.assertEqual(result['status'], 'abnormal')
        self.assertEqual(result['channels'][0]['denominator'], 8)
        self.assertEqual(result['channels'][0]['complaints'], 4)

    def test_pending_attribution_does_not_fill_gap(self):
        result = evaluate(tables(), self.signal('a', 28))
        self.assertEqual(result['status'], 'insufficient')
        self.assertEqual(result['channels'][0]['complaints'], 2)
        self.assertIn('o3', {item['order'] for item in result['pending_scope']})

    def test_baseline_uses_only_completed_history(self):
        result = evaluate(tables(), self.signal('old13', 7))
        activities = result['channels'][0]['baseline_activities']
        self.assertNotIn('old13', activities)
        self.assertNotIn('a', activities)

    def test_baseline_excludes_households_in_current_activity(self):
        result = evaluate(tables(), self.signal('a', 28))
        baseline_activities = set(result['channels'][0]['baseline_activities'])
        self.assertTrue(baseline_activities <= {f'old{n:02d}' for n in range(1, 14)})

    def test_followup_does_not_rewrite_day_of_call(self):
        result = followup(tables(), self.signal('b', 28))
        self.assertEqual(result['original']['status'], 'no_denominator')
        self.assertEqual(result['verdict'], 'not_screened')

    def test_small_activity_is_insufficient_not_clear(self):
        result = followup(tables(), self.signal('old08', -33))
        self.assertEqual(result['verdict'], 'unresolved_insufficient')

    def test_volume_is_not_anomaly(self):
        result = evaluate(tables(), self.signal('old13', 7))
        channel = result['channels'][0]
        self.assertEqual(channel['status'], 'no_anomaly')
        self.assertTrue(channel['high_volume'])
        self.assertIn('购买量', channel['volume_note'])

    def test_conflicting_attribution_stays_out_of_both_activities(self):
        result = evaluate(tables(), self.signal('a', 28), cutoff=32)
        pending = {item['order']: item for item in result['pending_scope']}
        self.assertEqual(pending['o4']['status'], 'conflict')
        self.assertNotIn('h4', result['channels'][0]['complaint_households'])


class Seats(unittest.TestCase):
    def plans(self):
        return {plan.shift: plan for plan in plan_all(tables(), as_of=72)}

    def test_claim_holds_one_seat_per_receipt(self):
        plan = self.plans()['s1']
        self.assertEqual(len(plan.receipt_groups), len({c.receipt for c in plan.commitments}))
        self.assertEqual(plan.free_slots, 0)

    def test_claimed_household_cannot_form_new_group(self):
        plan = self.plans()['s2']
        self.assertNotIn('alias1', plan.households)
        self.assertTrue(any(item['kind'] == 'claimed' for item in plan.excluded))

    def test_serious_cases_take_precedence(self):
        plan = self.plans()['s2']
        self.assertEqual({seat.statement_key for seat in plan.seats}, {'o3/h3'})
        self.assertEqual(plan.seats[0].basis, 'serious')

    def test_conflicting_attribution_does_not_get_combined(self):
        plan = self.plans()['s2']
        self.assertTrue(any(item['kind'] == 'conflict' for item in plan.excluded))

    def test_unknown_scope_case_is_single_item(self):
        plan = self.plans()['s3']
        self.assertEqual(plan.seats[0].basis, 'serious_pending')
        self.assertIsNone(plan.seats[0].activity)

    def test_under_coverage_is_left_open_not_filled(self):
        plan = self.plans()['s3']
        self.assertEqual(plan.status, 'under_covered')
        self.assertTrue(any('独立家庭覆盖' in gap for gap in plan.gaps))

    def test_withdrawn_confirmation_keeps_original_scope(self):
        group = next(g for g in review_commitments(tables(), 72)
                     if g['receipt'] == 'k2')
        self.assertEqual(group['activity'], 'a')
        self.assertEqual(group['disputed'], ['x3'])
        self.assertTrue(group['original_scope_kept'])

    def test_seat_plan_is_frozen_at_shift_day(self):
        # 撤回发生在 32，不回溯改写 29 日班次已占的席位。
        group = next(g for g in self.plans()['s1'].receipt_groups
                     if g['receipt'] == 'k2')
        self.assertEqual(group['disputed'], [])

    def test_retrospective_signal_change_does_not_remove_serious_case(self):
        plan = self.plans()['s2']
        self.assertTrue(any('不替代' in point for point in plan.watchpoints))


class Audit(unittest.TestCase):
    def test_audit_shows_denominator_support_seats_and_material(self):
        result = audit_activity(tables(), 'a', 34)
        channel = result['denominator_completeness'][0]
        self.assertEqual(channel['complete_at'], 27)
        self.assertEqual(channel['denominator'], 8)
        self.assertEqual({row['claim'] for row in result['sent_material']}, {'x1', 'x3'})
        self.assertTrue(any(row['state'] == 'not_occupied' for row in result['seats']))
        self.assertTrue(any(row['order'] == 'o1' for row in result['support']))

    def test_audit_separates_confirmed_and_pending_support(self):
        entries = {row['order']: row for row in audit_activity(tables(), 'a', 34)['support']}
        self.assertEqual(entries['o1']['status'], 'confirmed')
        self.assertEqual(entries['o2']['status'], 'withdrawn')
        self.assertIn('a', entries['o3']['hints'])
        self.assertEqual(entries['o3']['status'], 'confirmed')

    def test_audit_shows_seats_sent_for_that_activity(self):
        result = audit_activity(tables(), 'b', 34)
        occupied = [row for row in result['seats'] if row['state'] == 'occupied']
        self.assertEqual([row['statement'] for row in occupied], ['o3/h3'])
        self.assertEqual(result['sent_material'], [])

    def test_audit_reports_no_rate_without_coverage(self):
        result = audit_activity(tables(), 'b', 28)
        self.assertIsNone(result['denominator_completeness'][0]['rate'])


if __name__ == '__main__':
    unittest.main()
