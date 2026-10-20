import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')


class B3SampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['python3', 'scripts/prepare_b3_samples.py'], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        cls.manifest = json.loads((ROOT / 'reports/b3_sample_manifest.json').read_text())

    def test_exact_sample_counts(self):
        for rows in self.manifest['versions'].values():
            self.assertEqual(30, len(rows['recipes']))
            self.assertEqual(10, len(rows['drops']))
            self.assertEqual(25, sum(row['method'] == 'craft_grid' for row in rows['recipes']))
            self.assertEqual(5, sum(row['method'] == 'smelt' for row in rows['recipes']))

    def test_unique_samples(self):
        for rows in self.manifest['versions'].values():
            self.assertEqual(30, len({row['recipe_id'] for row in rows['recipes']}))
            self.assertEqual(10, len({row['block'] for row in rows['drops']}))

    def test_manifest_is_frozen_before_live_result(self):
        self.assertTrue(self.manifest['created_before_live_b3'])


if __name__ == '__main__':
    unittest.main()
