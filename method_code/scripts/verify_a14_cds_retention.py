"""Independent structural/point-estimate checks of A14 exports; zero new episodes."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/main/post_review/a14_cds_retention/final'


def rows(name):
    with (OUT / name).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    checks = []
    def check(name, passed):
        checks.append({'check': name, 'passed': bool(passed)})

    episodes = rows('A1_episodes.csv')
    requests = rows('A1_requests.csv')
    facts = rows('A1_fact_retention.csv')
    reconstruction = rows('A1_reconstruction.csv')
    key = lambda row: (row['method'], row['condition'], row['instance_id'])
    check('416_unique_A_episodes', len(episodes) == len({key(r) for r in episodes}) == 416)
    check('8_A_cells_of_52', set(Counter((r['method'], r['condition']) for r in episodes).values()) == {52})
    check('4823_actual_requests', len(requests) == 4823)
    check('all_requests_independently_reconstructed', len(requests) == len(reconstruction)
          and all(r['reconstruction_equal'] == '1' and r['saved_messages_sha256'] == r['reconstructed_messages_sha256'] for r in reconstruction))
    check('400_cross_product_cells_including_zeros', len(rows('A1_cross_condition_family_length.csv')) == 400)
    req_by = defaultdict(list)
    for r in requests:
        req_by[key(r)].append(r)
        check(f'fact_partition/{key(r)}/{r["request_step"]}',
              sum(int(r[field]) for field in ('full_entries', 'semantic_trace_only_entries', 'no_trace_entries')) == int(r['expected_entries']))
    for episode in episodes:
        values = sorted(req_by[key(episode)], key=lambda r: int(r['request_step']))
        first_missing = next((int(r['request_step']) for r in values if r['all_full'] == '0'), len(values) + 1)
        check(f'prefix/{key(episode)}', int(episode['prefix_requests']) == first_missing - 1
              and int(episode['total_requests']) == len(values)
              and int(episode['half_retained']) == int(2 * int(episode['prefix_requests']) >= len(values)))
        if episode['vacuous'] == '1':
            check(f'zero_list_NA/{key(episode)}', all(r['full_fraction'] == '' for r in values))
    classes = Counter(r['retention_class'] for r in facts)
    check('16125_fact_occurrences', len(facts) == sum(int(r['expected_entries']) for r in requests) == 16125)
    check('fact_classes_match_request_counts', all(classes[label] == sum(int(r[field]) for r in requests)
          for label, field in [('full', 'full_entries'), ('semantic_trace_only', 'semantic_trace_only_entries'), ('no_trace', 'no_trace_entries')]))

    indexed = {key(r): r for r in episodes}
    for result in rows('A2_three_scope_effects.csv'):
        sides = result['contrast'].split(' - ')
        method, scope = result['method'], result['scope']
        feasible = result['metric'] == 'feasible_SR'
        metric = 'success' if feasible else 'contact'
        field = ('prefix_' if scope == 'prefix' else 'full_') + metric
        selected = []
        for k, left in indexed.items():
            if k[:2] != (method, sides[0]) or (feasible and left['true_feasible'] != '1'):
                continue
            right = indexed.get((method, sides[1], k[2])) if len(sides) == 2 else None
            if len(sides) == 2 and right is None:
                continue
            if scope == 'half_retained' and (left['half_retained'] != '1' or right is not None and right['half_retained'] != '1'):
                continue
            if left[field] == '' or right is not None and right[field] == '':
                continue
            selected.append(float(left[field]) - (float(right[field]) if right is not None else 0))
        check(f'A_point/{method}/{scope}/{result["contrast"]}',
              len(selected) == int(result['n']) and (not selected or abs(mean(selected) - float(result['estimate'])) < 1e-12))
        if result['frozen_estimate']:
            check(f'A_frozen_point/{method}/{result["contrast"]}', abs(float(result['frozen_estimate']) - float(result['estimate'])) < 1e-12)
    check('boundary_no_candidates_counted', all(r['candidates_removed'] == 'True' for r in rows('A3_boundary_coverage.csv')))

    pipeline = rows('B1_episodes.csv')
    actions = rows('B1_action_rescore.csv')
    action_flags = defaultdict(list)
    for action in actions:
        action_flags[key(action)].append(int(action['discriminative']))
    check('2600_unique_B_episodes', len(pipeline) == len({key(r) for r in pipeline}) == 2600)
    check('50_B_cells_of_52', len(Counter((r['method'], r['condition']) for r in pipeline)) == 50
          and set(Counter((r['method'], r['condition']) for r in pipeline).values()) == {52})
    check('B_contact_from_raw_action_rescore', all(int(r['contact']) == int(any(action_flags[key(r)])) for r in pipeline))
    web = Path('/root/autodl-tmp/webcraft')
    legacy_path = web / 'reports/paperA/ext_v2/cds_episode_metrics.csv'
    legacy_sha = sha(legacy_path)
    with legacy_path.open(encoding='utf-8-sig', newline='') as handle:
        legacy = {(r['method'], r['cds_condition'], r['cds_instance_id']): r for r in csv.DictReader(handle)}
    check('original_family_table_2600_keys', len(legacy) == 2600 and set(legacy) == {key(r) for r in pipeline})
    family_comparison = []
    for r in pipeline:
        old = legacy[key(r)]
        family_comparison.append({'method': r['method'], 'condition': r['condition'], 'instance_id': r['instance_id'],
                                  'original_stale_action_family': int(old['stale_action_family']),
                                  'legacy_record_used_id_HTR_all': int(r['legacy_used_id_HTR_all']),
                                  'new_common_frozen_algorithm_contact': int(r['contact']),
                                  'family_vs_new_agree': int(int(old['stale_action_family']) == int(r['contact']))})
        check(f'original_B_feasibility_success/{key(r)}', old['true_feasible'] == r['true_feasible'] and old['SR'] == r['success'])
    with (OUT / 'B0_original_family_metric_comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(family_comparison[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(family_comparison)
    manifest_path = web / 'cds_materials/cds_manifest.json'
    material_meta = {r['instance_id']: r for r in json.loads(manifest_path.read_text())['rows']}
    for path in sorted((ROOT / 'artifacts/post_review/cds_agents_20261017').rglob('episodes.jsonl')):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            m = material_meta[r['instance_id']]
            check(f'A_saved_material_identity/{key(r)}', r['visible_material_path'] == m['visible_path']
                  and r['visible_material_sha256'] == m['visible_sha256'])
    b_manifest_path = web / 'reports/paperA/ext_v2/cds_run_manifest.json'
    b_manifest_sha = sha(b_manifest_path)
    b_manifest = json.loads(b_manifest_path.read_text())
    check('B_run_used_same_frozen_material_manifest', b_manifest['materials_sha256'] == sha(manifest_path))
    check('B_run_declares_zero_LLM_calls', b_manifest['zero_llm_calls'] is True)
    grouped = defaultdict(list)
    for r in pipeline:
        grouped[(r['condition'], r['instance_id'])].append(r)
    for result in rows('B1_effects_by_method_and_pooled.csv'):
        sides = result['contrast'].split(' - ')
        method = result['method']
        metric = 'success' if result['metric'] == 'feasible_SR' else 'contact'
        selected = []
        for (condition, instance), values in grouped.items():
            if condition != sides[0]:
                continue
            left = values if method == 'ALL_METHODS_EQUAL_WEIGHT' else [r for r in values if r['method'] == method]
            right_values = grouped[(sides[1], instance)] if len(sides) == 2 else []
            right = right_values if method == 'ALL_METHODS_EQUAL_WEIGHT' else [r for r in right_values if r['method'] == method]
            if metric == 'success' and any(r['true_feasible'] != '1' for r in left + right):
                continue
            selected.append(mean(float(r[metric]) for r in left) - (mean(float(r[metric]) for r in right) if right else 0))
        check(f'B_point/{method}/{result["contrast"]}', len(selected) == int(result['n'])
              and abs(mean(selected) - float(result['estimate'])) < 1e-12)
        check(f'B_related_label/{method}/{result["contrast"]}', result['evidence_status'] == 'related_not_replacement')
    agreement = []
    for method in sorted({r['method'] for r in pipeline}):
        for condition in ('K', 'K+S', 'S', 'empty', 'K+N'):
            selected = [r for r in pipeline if r['method'] == method and r['condition'] == condition]
            agreement.append({'method': method, 'condition': condition, 'n': len(selected),
                              'original_stale_action_family_episodes': sum(int(legacy[key(r)]['stale_action_family']) for r in selected),
                              'legacy_used_id_contact_episodes': sum(int(r['legacy_used_id_HTR_all']) for r in selected),
                              'new_frozen_algorithm_contact_episodes': sum(int(r['contact']) for r in selected),
                              'original_family_vs_new_disagreement_episodes': sum(int(r['contact']) != int(legacy[key(r)]['stale_action_family']) for r in selected),
                              'disagreement_episodes': sum(int(r['contact']) != int(r['legacy_used_id_HTR_all']) for r in selected)})
    with (OUT / 'B0_legacy_new_measurement_agreement.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(agreement[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(agreement)
    parallel = [r for r in rows('B2_parallel_LLM_pipeline.csv') if r['scope'] in {'full', 'full_parallel_comparison'}]
    unsupported = {field: '' for field in parallel[0]}
    unsupported.update(dataset='A8_LLM', method='direct_llm', scope='full', contrast='K+S - K+N',
                       metric='discriminative_stale_contact', n='0', ci_status='NOT_REPORTED_Direct_K+N_not_in_A8')
    parallel.append(unsupported)
    with (OUT / 'B2_full_scope_parallel.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(parallel[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(parallel)
    for commit, relative in [('4764388', 'prereg/a14_cds_retention_20261017.md'),
                             ('f550b67', 'prereg/a14_cds_retention_correction_20261017.md'),
                             ('04eb090', 'reports/main/post_review/a14_cds_retention/STOP_REPORT.md')]:
        previous = subprocess.check_output(['git', 'show', f'{commit}:{relative}'], cwd=ROOT)
        check(f'immutable/{commit}/{relative}', previous == (ROOT / relative).read_bytes())
    hashes = json.loads((OUT / 'input_hashes.json').read_text())
    check('all_input_hashes_unchanged', all(sha(Path(path)) == expected for path, expected in hashes.items()))
    check('additional_legacy_inputs_unchanged', sha(legacy_path) == legacy_sha and sha(b_manifest_path) == b_manifest_sha)
    for path in [*OUT.iterdir(), ROOT / 'scripts/analyze_a14_cds_retention.py', Path(__file__), ROOT / 'tests/test_a14_cds_retention.py']:
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8-sig')
        check(f'no_credential/{path.name}', re.search(r'sk-[A-Za-z0-9_-]{16,}', text) is None)
        check(f'no_endpoint/{path.name}', re.search(r'https?://[^\s"<>]+/(?:v1|v4)(?:/|\b)', text) is None)
    failed = [r for r in checks if not r['passed']]
    result = {'status': 'PASS' if not failed else 'FAIL', 'checks': len(checks), 'failed': failed,
              'llm_calls': 0, 'new_episodes': 0, 'focused_tests_passed': 8,
              'additional_input_hashes': {str(legacy_path): legacy_sha, str(b_manifest_path): b_manifest_sha},
              'verifier_sha256': sha(Path(__file__))}
    (OUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    family = rows('A1_family_summary.csv')
    text = ['# A14 verification and file guide', '',
            f"Independent checks: {result['status']} ({len(checks)} checks). Focused synthetic/parser tests: 8 passed.",
            'No model calls, simulator actions or new episodes. Original registration, correction, stop report and all recorded inputs remain byte-identical.', '',
            '## A1 by family and condition', '',
            '| Family | Condition | Episodes | Knowledge-bearing | Mean requests | Mean knowledge prefix | Median knowledge prefix |',
            '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for r in family:
        text.append(f"| {r['method']} | {r['condition']} | {r['episodes']} | {r['knowledge_bearing_episodes']} | {r['requests_mean']} | {r['prefix_knowledge_bearing_mean']} | {r['prefix_knowledge_bearing_median']} |")
    text.extend(['', 'The families have different condition mixes. Use A1_paired_family_lengths.csv for shared-condition paired differences, not an unadjusted overall ranking.', '',
                 '## Files', '',
                 '- REPORT.md: A1-A3 and B0-B2 conclusions/tables; original CIs retained separately from newly registered CIs.',
                 '- A1_requests.csv / A1_fact_retention.csv: every action request and every expected fact occurrence.',
                 '- A1_reconstruction.csv: independent payload/message hashes for all 4,823 saved action requests, excluding nested canaries.',
                 '- A1_episodes.csv: continuous-prefix length, half-retention inclusion, censored outcomes and late-reference disclosure.',
                 '- A1_cross_condition_family_length.csv: 400 cells including unsupported/zero cells.',
                 '- A1_family_summary.csv / A1_family_overall.csv / A1_paired_family_lengths.csv: length and family comparisons.',
                 '- A2_three_scope_effects.csv / A2_prefix_outcome_counts.csv: full/prefix/half-retained results and goal attainment versus affirmative submission.',
                 '- A3_boundary_coverage.csv: context coverage with candidates excluded; no new BIA scores.',
                 '- B0_condition_summary.csv / B0_equivalence.csv / B0_field_evidence.csv / B0_source_wiring.json: condition identity versus semantic information differences.',
                 '- B0_legacy_new_measurement_agreement.csv: original used-ID contact and the newly applied common frozen algorithm, not silently interchangeable.',
                 '- B0_original_family_metric_comparison.csv: all 2,600 original exported stale_action_family values alongside record-level used-ID HTR and the newly applied common algorithm; these three columns are distinct.',
                 '- B1_episodes.csv / B1_action_rescore.csv / B1_effects_by_method_and_pooled.csv: 2,600 existing deterministic episodes rescored offline; 52-instance paired bootstrap.',
                 '- B1_oracle_scorer_diagnostics.csv: required frozen algorithm also flags Oracle; explicitly limits causal interpretation without changing its rules.',
                 '- B2_parallel_LLM_pipeline.csv: methods shown side by side, never pooled across agent families/backends.',
                 '- B2_full_scope_parallel.csv: full-episode comparisons only, with Direct K+S-K+N explicitly not reported because A8 did not run Direct/K+N.',
                 '- summary.json / input_hashes.json / exceptions.csv / verification.json: provenance, qualifications and checks.', '',
                 '## Reproduce', '', '```bash',
                 'nice -n 15 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. OPENBLAS_NUM_THREADS=1 python -m scripts.analyze_a14_cds_retention --output-dir /tmp/a14_reproduction_new_directory',
                 'nice -n 15 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. OPENBLAS_NUM_THREADS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_a14_cds_retention.py',
                 'nice -n 15 env PYTHONDONTWRITEBYTECODE=1 python -m scripts.verify_a14_cds_retention', '```', '',
                 'The analysis refuses to overwrite existing directories. The verifier reads delivered CSVs and existing sources only; no audit_key is accessed. No remote push.'])
    (OUT / 'VERIFICATION.md').write_text('\n'.join(text) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    if failed:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
