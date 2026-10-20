"""Independent denominator checks for the W17b table replacement."""
import csv
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import w17b_cds_loo as analysis


def read(name):
    with (analysis.OUT/name).open() as f:
        return list(csv.DictReader(f))


class W17bChecks(unittest.TestCase):
    def test_paired_numerators_and_denominators(self):
        pairs = read('paired_inputs.csv')
        for r in read('loo_deletions.csv'):
            keys = ('model','method','contrast','metric')
            subset = [p for p in pairs if all(p[k]==r[k] for k in keys) and p['family_id']!=r['excluded_family']]
            self.assertEqual(len(subset), int(r['n_pairs']))
            numerator = sum(int(p['left_value'])-int(p['right_value']) for p in subset)
            self.assertEqual(numerator, int(r['delta_numerator']))
            self.assertAlmostEqual(numerator/len(subset), float(r['delta']))
            self.assertEqual(len({p['instance_id'] for p in subset}), len(subset))

    def test_summary_ranges_and_least_favourable_selection(self):
        details = read('loo_deletions.csv')
        summary = read('loo_corrected_cds_v2.csv')
        self.assertEqual(len(summary),5)
        for r in summary:
            rr = [p for p in details if all(p[k]==r[k] for k in ('model','method','contrast','metric')) and p['excluded_family']!='NONE']
            self.assertEqual(len(rr),12)
            self.assertAlmostEqual(float(r['loo_min_pp']),100*min(float(p['delta']) for p in rr))
            self.assertAlmostEqual(float(r['loo_max_pp']),100*max(float(p['delta']) for p in rr))
            worst = min(rr,key=lambda p:(abs(float(p['delta'])),p['excluded_family']))
            self.assertEqual(worst['excluded_family'],r['worst_excluded_family'])
            self.assertEqual(r['worst_crosses_zero'],str(float(worst['ci95_low'])<=0<=float(worst['ci95_high'])))

    def test_pair_identity_mismatch_is_rejected(self):
        common = dict(method='react',family_id='f',task_id='t',target_world='w',success='0')
        rr = [dict(common,condition=c,instance_id=str(i)) for c in ('K','empty') for i in range(52)]
        rr[-1]['target_world']='different_world'
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            analysis.pair_rows(rr,'react','empty','K','success')

    def test_worst_ties_sort_by_family(self):
        rr = [dict(excluded_family=f,delta=d) for f,d in [('NONE',0),('z',-.2),('b',.1),('a',-.1)]]
        self.assertEqual(analysis.worst_deletion(rr)['excluded_family'],'a')

    def test_source_and_output_integrity(self):
        manifest = json.loads((analysis.OUT/'source_manifest.json').read_text())
        for r in manifest['inputs'].values():
            self.assertEqual(hashlib.sha256(Path(r['path']).read_bytes()).hexdigest(),r['sha256'])
        for file, value in json.loads((analysis.OUT/'output_manifest.json').read_text()).items():
            self.assertEqual(hashlib.sha256((analysis.OUT/file).read_bytes()).hexdigest(),value)


if __name__=='__main__':
    unittest.main()
