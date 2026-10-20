"""Numerical edge cases and independent reconciliation of W17 exports."""
import csv
import hashlib
import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('w17_analysis', ROOT / 'scripts/w17_analysis.py')
w17 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w17)


def rows(name):
    with (w17.OUT / name).open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


class Definitions(unittest.TestCase):
    def test_wilson_zero_denominator_and_boundaries(self):
        self.assertEqual(w17.wilson(0, 0), (None, None, None))
        self.assertAlmostEqual(w17.wilson(0, 10)[2], 0.2775327998628892)
        self.assertAlmostEqual(w17.wilson(10, 10)[1], 0.7224672001371107)
        self.assertAlmostEqual(w17.wilson(5, 10)[1], 0.236593090512564)

    def test_detection_orientation_and_unknown_exclusion(self):
        result = w17.detection([1, 1, 0, 0, None], [1, 0, 1, 0, 1])
        self.assertEqual([result[k] for k in ('tp','fp','fn','tn')], [1, 1, 1, 1])
        self.assertEqual(result['n_eligible'], 4)
        self.assertEqual(result['n_excluded'], 1)
        self.assertEqual(result['precision'], .5)
        self.assertEqual(result['recall'], .5)

    def test_unknown_is_not_negative(self):
        self.assertIsNone(w17.merged(None, 0, 'union', 'partial_information'))
        self.assertEqual(w17.merged(None, 1, 'union', 'partial_information'), 1)
        self.assertEqual(w17.merged(None, 0, 'intersection', 'partial_information'), 0)
        self.assertIsNone(w17.merged(None, 1, 'intersection', 'partial_information'))
        for kind in ('union', 'intersection'):
            self.assertIsNone(w17.merged(None, 1, kind, 'mutually_determinate'))

    def test_propagation_blank_and_explicit_unknown(self):
        self.assertEqual(w17.propagation('', '无'), '否')
        self.assertEqual(w17.propagation('', '查询'), '无法确定')
        self.assertEqual(w17.propagation('无法确定', '无'), '无法确定')

    def test_kappa_includes_chance_and_handles_degenerate_case(self):
        result = w17.kappa([0, 0, 1, 1], [0, 1, 1, 1])
        self.assertEqual(result['raw_agreement'], .75)
        self.assertEqual(result['expected_agreement'], .5)
        self.assertEqual(result['kappa'], .5)
        self.assertIsNone(w17.kappa([0,0], [0,0])['kappa'])


class ExportIntegrity(unittest.TestCase):
    def test_all_joins_unique_and_input_hashes_unchanged(self):
        joined = rows('joined_labels.csv')
        self.assertEqual(Counter(r['cohort'] for r in joined), {'heldout':80, 'cds':30})
        self.assertEqual(len({(r['cohort'],r['audit_id']) for r in joined}), 110)
        manifest = json.loads((w17.OUT/'source_manifest.json').read_text())
        for source in manifest['inputs'].values():
            self.assertEqual(hashlib.sha256(Path(source['path']).read_bytes()).hexdigest(), source['sha256'])

    def test_detection_export_reconciles_independent_set_counts(self):
        joined = rows('joined_labels.csv')
        for result in rows('scorer_vs_ann2.csv'):
            group = [r for r in joined if r['cohort']==result['cohort']]
            human_key = 'ann2_contact' if result['reference']=='ann2' else 'ann1_contact'
            eligible = {r['audit_id'] for r in group if r[human_key]!='无法确定' and r['scorer_contact']!='无法确定'}
            hp = {r['audit_id'] for r in group if r[human_key] in ('查询','执行')} & eligible
            sp = {r['audit_id'] for r in group if r['scorer_contact'] in ('查询','执行')} & eligible
            self.assertEqual(int(result['tp']), len(hp & sp))
            self.assertEqual(int(result['fp']), len(sp-hp))
            self.assertEqual(int(result['fn']), len(hp-sp))
            self.assertEqual(int(result['n_eligible']), len(eligible))
            self.assertEqual(sum(int(result[k]) for k in ('tp','fp','fn','tn')), len(eligible))

    def test_original_fp_dispositions_exhaustive(self):
        for r in rows('merged_precision.csv'):
            self.assertEqual(int(r['original_fp_n']), sum(int(r[k]) for k in
                             ('original_fp_to_tp','original_fp_stays_fp','original_fp_excluded')))

    def test_loo_uses_retained_episode_denominator(self):
        pairs = rows('loo_pairs.csv')
        self.assertEqual(len(pairs), 52)
        for r in rows('loo_corrected.csv'):
            kept = [p for p in pairs if p['family_id']!=r['excluded_family']]
            n = len(kept)
            numerator = sum(int(p['empty_success'])-int(p['K_success']) for p in kept)
            self.assertEqual(int(r['n_pairs']), n)
            self.assertEqual(int(r['difference_numerator']), numerator)
            self.assertAlmostEqual(float(r['corrected_episode_weighted_delta']), numerator/n)
            if r['excluded_family']!='NONE':
                self.assertLessEqual(float(r['reconstruction_abs_error']), 1e-12)

    def test_export_hashes_and_kappa_marginals(self):
        manifest = json.loads((w17.OUT/'output_manifest.json').read_text())
        for name, expected in manifest.items():
            self.assertEqual(hashlib.sha256((w17.OUT/name).read_bytes()).hexdigest(), expected)
        for r in rows('kappa_check.csv'):
            if r['metric'].startswith('contact'):
                n = int(r['n'])
                left, right = json.loads(r['ann1_marginal']), json.loads(r['ann2_marginal'])
                pe = sum(left.get(k,0)*right.get(k,0) for k in set(left)|set(right))/n**2
                po = int(r['agreements'])/n
                self.assertAlmostEqual(float(r['kappa']), (po-pe)/(1-pe))


if __name__ == '__main__':
    unittest.main()
