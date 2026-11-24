"""Domain tests for retention lookup and date-bounded contract corrections.

These exercise the pure planning layer only, so they run without the service
stack: ``PYTHONPATH=backend python3 -m unittest discover -s backend/tests``.
"""
import copy
import unittest

from src.services import domain
from src.services.tape_erasure import (
    Amendment, amendment_overlaps, apply_erasure, catalog_copy, first_overlapping,
)


def sample():
    return copy.deepcopy(domain.SPEC['sample'])


class RetentionLookupTest(unittest.TestCase):
    def test_reports_what_the_batch_retained(self):
        view = domain.retention_view(sample())
        assets = {row['asset_id']: row for row in view['assets']}
        self.assertEqual(view['cutoff'], '2026-11-21')
        # Expired CT-208 does not protect anything on the check date.
        self.assertEqual(view['contracts'], ['CT-215', 'CT-216'])
        self.assertEqual(assets['T-05']['reason'], '合同留存')
        self.assertEqual(assets['T-05']['contracts'], ['CT-215'])
        self.assertEqual(assets['F-01']['reason'], '合同留存')
        self.assertEqual(assets['F-01']['contracts'], ['CT-216'])
        # The shared master survives on references, not a contract.
        self.assertEqual(assets['M-SHARED']['reason'], '仍有留存引用')
        self.assertEqual(assets['T-06']['reason'], '样带未到期')

    def test_does_not_include_deleted_assets(self):
        view = domain.retention_view(sample())
        retained = {row['asset_id'] for row in view['assets']}
        self.assertNotIn('T-01', retained)
        self.assertNotIn('A-HOT', retained)


class OverlapTest(unittest.TestCase):
    def test_overlapping_ranges_conflict(self):
        left = Amendment('CT-1', 'T-1', None, '2026-11-01', '2026-11-30')
        right = Amendment('CT-1', 'T-2', None, '2026-11-15', '2026-12-15')
        self.assertTrue(amendment_overlaps(left, right))

    def test_disjoint_ranges_coexist(self):
        left = Amendment('CT-1', 'T-1', None, '2026-11-01', '2026-11-30')
        right = Amendment('CT-1', 'T-2', None, '2026-12-01', '2026-12-31')
        self.assertFalse(amendment_overlaps(left, right))

    def test_open_ended_range_overlaps_everything_later(self):
        left = Amendment('CT-1', 'T-1', None, '2026-11-20', None)
        right = Amendment('CT-1', 'T-2', None, '2027-01-01', '2027-01-31')
        self.assertTrue(amendment_overlaps(left, right))

    def test_different_contracts_never_conflict(self):
        left = Amendment('CT-1', 'T-1', None, '2026-11-01', '2026-11-30')
        right = Amendment('CT-2', 'T-1', None, '2026-11-01', '2026-11-30')
        self.assertFalse(amendment_overlaps(left, right))

    def test_first_overlapping_returns_earliest_winner(self):
        accepted = [
            Amendment('CT-1', 'T-1', None, '2026-11-01', '2026-11-10'),
            Amendment('CT-1', 'T-2', None, '2026-11-05', '2026-11-20'),
        ]
        new = Amendment('CT-1', 'T-3', None, '2026-11-18', '2026-11-25')
        self.assertEqual(first_overlapping(accepted, new), 1)

    def test_disjoint_newcomer_has_no_winner(self):
        accepted = [Amendment('CT-1', 'T-1', None, '2026-11-01', '2026-11-10')]
        new = Amendment('CT-1', 'T-2', None, '2026-11-11', '2026-11-20')
        self.assertIsNone(first_overlapping(accepted, new))


class RecomputeTest(unittest.TestCase):
    def test_recompute_keeps_directory_and_only_overlays_holds(self):
        payload = sample()
        amended, applied, skipped = domain.apply_amendments(
            payload,
            [Amendment('CT-208', 'T-02', '2026-11-21', '2026-11-21', '2026-11-21')],
            '2026-11-21',
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(skipped, [])
        # Directory and references are byte-for-byte the recorded snapshot.
        self.assertEqual(amended['assets'], payload['assets'])
        self.assertEqual(amended['references'], payload['references'])
        # The original payload is untouched: CT-208 still ends on 2026-11-20.
        original = next(row for row in payload['holds'] if row['contract_id'] == 'CT-208')
        self.assertEqual(original['hold_until'], '2026-11-20')
        corrected = next(row for row in amended['holds'] if row['contract_id'] == 'CT-208')
        self.assertEqual(corrected['hold_until'], '2026-11-21')
        # With CT-208 extended to the check date, T-02 is now retained.
        result = domain.solve(amended, None)
        self.assertEqual(result['metrics']['擦除样带'], 3)
        self.assertEqual(result['metrics']['留存资产'], 11)
        self.assertEqual(result['metrics']['到期样带'], 5)

    def test_amendment_outside_its_date_range_is_ignored(self):
        payload = sample()
        amended, applied, _ = domain.apply_amendments(
            payload,
            [Amendment('CT-208', 'T-02', '2026-11-21', '2026-11-22', '2026-11-30')],
            '2026-11-21',
        )
        self.assertEqual(applied, [])
        self.assertEqual(amended, payload)

    def test_amendment_for_absent_asset_is_skipped(self):
        payload = sample()
        amended, applied, skipped = domain.apply_amendments(
            payload,
            [Amendment('CT-900', 'NOT-IN-BATCH', '2026-11-21', '2026-11-21', '2026-11-21')],
            '2026-11-21',
        )
        self.assertEqual(applied, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(amended, payload)

    def test_original_evidence_is_reproducible_from_snapshot(self):
        # Recomputing the untouched snapshot returns the original metrics.
        payload = sample()
        baseline = domain.solve(payload, None)['metrics']
        self.assertEqual(baseline['擦除资产'], 8)
        self.assertEqual(baseline['留存资产'], 7)
        amended, applied, _ = domain.apply_amendments(payload, [], '2026-11-21')
        self.assertEqual(amended, payload)
        self.assertEqual(domain.solve(amended, None)['metrics'], baseline)


if __name__ == '__main__':
    unittest.main()
