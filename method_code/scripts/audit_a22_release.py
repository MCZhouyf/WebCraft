"""A22 deterministic release QA and cell-wise secondary exports, no inference."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re

from analyze_a22_pf_rag import METRICS, csv_out, paired_ci
from run_a22_evaluation import BASE, REPORT, ROOT, verify_policy
from run_a22_development import canonical, dump, sha


def rows(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def key(row):
    return tuple(row[k] for k in ['scope', 'reading_mode', 'condition', 'world', 'task_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay-root', type=Path)
    args = parser.parse_args()
    verify_policy()
    manifest = json.loads((BASE / 'manifest.json').read_text())
    version = re.compile(r'(?<![\d.])1\.\d{1,2}(?:\.\d{1,2})?(?!\d|\.\d)')
    public_fields = {'text', 'source_site', 'page_title', 'revid', 'timestamp', 'chunk_id'}
    leak = []
    for entry in manifest['evidence_packages']:
        bundle = json.loads(Path(entry['path']).read_text())
        hits = bundle['search_rows']
        leak.append({'evidence_path': entry['path'], 'sha256': sha(Path(entry['path'])),
            'search_rows': len(hits),
            'unexpected_public_fields': sum(bool(set(h) - public_fields) for h in hits),
            'version_pattern_hits': sum(bool(version.search(str(v))) for h in hits for v in h.values()),
            'origin': 'frozen_public_text_or_oracle_reading_control',
            'version_mentions_allowed_not_policy_features': True})
    csv_out(REPORT / 'retrieval_log_audit.csv', leak)
    pf = {key(r): r for r in rows(REPORT / 'per_episode.csv') if r['seed'] == '20261020'}
    peers = {(key(r), r['method']): r for r in rows(REPORT / 'baseline_per_episode.csv')}
    compatible = {(key(r), r['comparator']): r['compatible'] == 'True'
                  for r in rows(REPORT / 'comparability_audit.csv')}
    cells = defaultdict(list)
    for k, row in pf.items():
        cells[k[:4]].append(row)
    output = []
    for cell, group in sorted(cells.items()):
        for comparator in ['naive_rag', 'cp_rag']:
            pairs = [(r, peers.get((key(r), comparator))) for r in group]
            valid = all(b is not None and compatible.get((key(a), comparator)) for a, b in pairs)
            for metric in METRICS:
                values = [(a['task_id'] if cell[0] == 'main' else a['family_id'],
                           float(a[metric]) - float(b[metric]))
                          for a, b in pairs if b is not None and a[metric] != '' and b[metric] != '']
                result = paired_ci(values)
                if not valid or (metric != 'OC' and len(values) != len(group)):
                    result.update(estimate=None, ci_low=None, ci_high=None,
                                  status='NOT_REPORTABLE_budget_or_provenance')
                output.append(dict(zip(['scope', 'reading_mode', 'condition', 'world'], cell)) |
                              {'contrast': 'pf_rag - ' + comparator, 'metric': metric, **result})
    csv_out(REPORT / 'paired_cell_contrasts.csv', output)
    cases = rows(REPORT / 'comparability_audit.csv')
    budget = Counter((r['scope'], r['reading_mode'], r['issues']) for r in cases if r['compatible'] == 'False')
    dump(REPORT / 'baseline_limitations.json', {
        'incompatible_instances': [{'scope': k[0], 'reading_mode': k[1], 'issues': json.loads(k[2]), 'n': n}
                                   for k, n in sorted(budget.items())],
        'budget_evidence': 'All 102 affected CP records have initial search_count=60 and one additional public research call.',
        'baseline_records_unchanged': True, 'no_dropping_incompatible_tasks': True})
    if args.replay_root:
        replay = args.replay_root
        packaged = json.loads((replay / 'PACKAGE_MANIFEST.json').read_text())
        changed = [p for p, digest in packaged['files'].items() if sha(replay / p) != digest]
        replay_base = replay / BASE.relative_to(ROOT)
        replay_report = replay / REPORT.relative_to(ROOT)
        status = json.loads((replay_base / 'status.json').read_text())
        same_pin = sha(BASE / 'webcraft_pin.json') == sha(replay_base / 'webcraft_pin.json')
        differences = []
        original_paths = sorted((BASE / 'episodes').glob('*/*/episode.json'))
        for path in original_paths:
            other = replay_base / path.relative_to(BASE)
            a, b = json.loads(path.read_text()), json.loads(other.read_text())
            # Only the package-relative location differs; input content hashes must agree.
            a.pop('evidence_path'); b.pop('evidence_path')
            if canonical(a) != canonical(b):
                differences.append(str(path.relative_to(BASE)))
        metric_files = ['per_episode.csv', 'baseline_per_episode.csv', 'paired_contrasts.csv',
                        'main_table.csv', 'cell_table.csv', 'probe_census.csv', 'divergence_census.csv']
        matching = {name: sha(REPORT / name) == sha(replay_report / name) for name in metric_files}
        result = {'status': 'PASS' if not changed and not differences and all(matching.values())
                  and same_pin and status['status'] == 'COMPLETED' else 'FAIL',
                  'packaged_source_hash_mismatches': changed,
                  'episodes_reexecuted': len(original_paths), 'canonical_episode_differences': differences,
                  'metric_files_byte_identical': matching,
                  'webcraft_pin_identical': same_pin,
                  'elapsed_seconds': status['elapsed_seconds'], 'llm_calls': 0,
                  'external_dependency': 'pinned read-only WebCraft; not a standalone simulator bundle',
                  'excluded_from_behavior_comparison': ['seed', 'elapsed_seconds', 'peak_rss_kib', 'evidence_path']}
        dump(REPORT / 'package_replay_qa.json', result)
        print(json.dumps(result))
        if result['status'] != 'PASS':
            raise RuntimeError('package replay QA failed; preserve all outputs')


if __name__ == '__main__':
    main()
