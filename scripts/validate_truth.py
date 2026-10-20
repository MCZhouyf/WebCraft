#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path('/root/autodl-tmp/webcraft')
with (ROOT / 'truth' / 'separator_truth.json').open(encoding='utf-8') as handle:
    derived = json.load(handle)
with (ROOT / 'truth' / 'separator_empirical.json').open(encoding='utf-8') as handle:
    empirical = json.load(handle)

summary = {'versions': {}, 'total_cells': 0, 'empirical_matches_jar': 0, 'jar_matches_expected': 0}
for version, rows in derived['versions'].items():
    observed = {row['id']: row for row in empirical['versions'][version]['rows']}
    results = []
    for row in rows:
        merged = {
            'id': row['id'],
            'probe': row['probe'],
            'expected': row['expected'],
            'jar': row['jar'],
            'empirical': observed[row['id']]['empirical']
        }
        merged['empirical_matches_jar'] = merged['empirical'] == merged['jar']
        merged['jar_matches_expected'] = merged['jar'] == merged['expected']
        summary['total_cells'] += 1
        summary['empirical_matches_jar'] += int(merged['empirical_matches_jar'])
        summary['jar_matches_expected'] += int(merged['jar_matches_expected'])
        results.append(merged)
    summary['versions'][version] = results

summary['passed'] = (
    summary['empirical_matches_jar'] == summary['total_cells'] and
    summary['jar_matches_expected'] == summary['total_cells']
)
with (ROOT / 'reports' / 'truth_validation.json').open('w', encoding='utf-8') as handle:
    json.dump(summary, handle, indent=2, ensure_ascii=True)
    handle.write('\n')
print(json.dumps({key: summary[key] for key in ('total_cells', 'empirical_matches_jar', 'jar_matches_expected', 'passed')}))
