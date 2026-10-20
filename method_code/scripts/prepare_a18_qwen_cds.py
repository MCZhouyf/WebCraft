"""Zero-call input freeze and GLM reference preparation for A18."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from arbiter.evaluation.cds_agents import public_rows, verify_w7a_freeze
from arbiter.evaluation.external_agents import public_system_prompt
from scripts.analyze_cds_agents_20261017 import bootstrap_difference, bootstrap_mean

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'reports/main/post_review/a18_qwen_cds_20261017'
LOCK = ROOT / 'prereg/a18_cds_inputs_20261017.json'
CONDITIONS = ('K', 'K+N', 'K+S', 'empty')
METRICS = ('success', 'discriminative_stale_contact', 'v3d_lower', 'v3d_upper')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def write_csv(path, values):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(values)


def build_lock():
    verify_w7a_freeze(WEB)
    manifest = WEB / 'cds_materials/cds_manifest.json'
    meta = json.loads(manifest.read_text())['rows']
    if len(meta) != 52 or len({r['instance_id'] for r in meta}) != 52:
        raise RuntimeError('cds_instance_scope_mismatch')
    jobs = []
    for condition in CONDITIONS:
        for row in meta:
            material_path = WEB / row['visible_path']
            if sha(material_path) != row['visible_sha256']:
                raise RuntimeError('visible_material_hash_mismatch')
            material = json.loads(material_path.read_text())
            jobs.append({'method': 'react', 'condition': condition,
                         'instance_id': row['instance_id'],
                         'visible_path': row['visible_path'],
                         'visible_sha256': row['visible_sha256'],
                         'fact_count': len(public_rows(material, condition))})
    hashes = {}
    for relative in ('src/arbiter/evaluation/external_agents.py',
                     'src/arbiter/evaluation/cds_agents.py',
                     'src/arbiter/evaluation/measurement_v3.py',
                     'scripts/analyze_cds_agents_20261017.py',
                     'prereg/cds_agents_20261017.md'):
        hashes[relative] = sha(ROOT / relative)
    return {'episodes': 208, 'conditions': list(CONDITIONS), 'jobs': jobs,
            'manifest_sha256': sha(manifest),
            'w7a_freeze_sha256': sha(WEB / 'config/ext_v2/freeze_manifest.json'),
            'webcraft_commit': subprocess.check_output(
                ['git', '-C', str(WEB), 'rev-parse', 'HEAD'], text=True).strip(),
            'server_lock_sha256': sha(Path('/root/autodl-tmp/server.lock')),
            'source_hashes': hashes,
            'react_prompt_sha256': hashlib.sha256(public_system_prompt('react').encode()).hexdigest(),
            'seed': 20261017, 'bootstrap_draws': 10000}


def main():
    if LOCK.exists():
        raise RuntimeError('a18_input_lock_already_exists_do_not_overwrite')
    OUT.mkdir(parents=True, exist_ok=True)
    lock = build_lock()
    if lock['react_prompt_sha256'] != '5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84':
        raise RuntimeError('react_prompt_changed')
    profile = ROOT / 'reports/main/post_review/cds_agents_20261017/episode_metrics.csv'
    original = [r for r in csv.DictReader(profile.open())
                if r['method'] == 'react' and r['condition'] in CONDITIONS]
    expected = {(r['condition'], r['instance_id']) for r in lock['jobs']}
    actual = [(r['condition'], r['instance_id']) for r in original]
    if len(actual) != 208 or len(set(actual)) != 208 or set(actual) != expected:
        raise RuntimeError('glm_reference_coverage_mismatch')
    numeric = ('success', 'true_feasible', 'discriminative_stale_contact',
               'v3d_lower', 'v3d_upper', 'action_model_calls', 'boundary_model_calls')
    reference = [{**r, **{k: int(r[k]) for k in numeric}} for r in original]
    condition_rows = []
    for condition in CONDITIONS:
        subset = [r for r in reference if r['condition'] == condition]
        for metric in (*METRICS, 'feasible_SR'):
            eligible = (lambda r: bool(r['true_feasible'])) if metric == 'feasible_SR' else (lambda r: True)
            column = 'success' if metric == 'feasible_SR' else metric
            selected = [r for r in subset if eligible(r)]
            point, low, high, n = bootstrap_mean(subset, column, eligible)
            families = len({r['family_id'] for r in selected})
            condition_rows.append({'model': 'glm-5.2', 'condition': condition, 'metric': metric,
                                   'point': point, 'ci_low': low if families >= 2 else '',
                                   'ci_high': high if families >= 2 else '', 'n': n, 'families': families})
    contrast_rows = []
    for left, right, metrics in (('K+S', 'K+N', METRICS),
                                 ('empty', 'K', ('success', 'feasible_SR'))):
        for metric in metrics:
            eligible = (lambda r: bool(r['true_feasible'])) if metric == 'feasible_SR' else (lambda r: True)
            column = 'success' if metric == 'feasible_SR' else metric
            a = [r for r in reference if r['condition'] == left]
            b = [r for r in reference if r['condition'] == right]
            point, low, high, n = bootstrap_difference(a, b, column, eligible)
            families = len({r['family_id'] for r in a if eligible(r)})
            contrast_rows.append({'model': 'glm-5.2', 'contrast': f'{left} - {right}',
                                  'metric': metric, 'point': point,
                                  'ci_low': low if families >= 2 else '',
                                  'ci_high': high if families >= 2 else '', 'paired_n': n,
                                  'families': families, 'bootstrap_seed': 20261017, 'draws': 10000})
    calls = sum(r['action_model_calls'] for r in reference)
    boundary = sum(r['boundary_model_calls'] for r in reference)
    forecast = {'estimated': True, 'basis': 'exact_208_A8_GLM_reference_episodes',
                'historical_action_calls': calls, 'historical_boundary_calls': boundary,
                'indicative_task_calls': calls + boundary,
                'max_action_calls': 208 * 40, 'max_boundary_calls': 208,
                'additional_canaries_and_retries_counted_separately': True,
                'qwen_time_estimate': None, 'monetary_cost': None,
                'currency': None, 'profile_sha256': sha(profile)}
    lock['glm_reference_sha256'] = sha(profile)
    write_json(LOCK, lock)
    write_csv(OUT / 'glm_reference_episodes.csv', original)
    write_csv(OUT / 'glm_reference_conditions.csv', condition_rows)
    write_csv(OUT / 'glm_reference_contrasts.csv', contrast_rows)
    write_json(OUT / 'call_forecast.json', forecast)
    write_json(OUT / 'status.json', {'status': 'BLOCKED_ENDPOINT_CONFIGURATION',
               'model_calls': 0, 'episodes_run': 0, 'requested_local_family': 'Qwen14B',
               'server_model_identifier': None, 'max_concurrent_requests': 2,
               'input_lock_sha256': sha(LOCK),
               'facts_per_condition': {c: dict(sorted(Counter(r['fact_count'] for r in lock['jobs']
                                                             if r['condition'] == c).items()))
                                       for c in CONDITIONS}})
    print(json.dumps({'status': 'BLOCKED_ENDPOINT_CONFIGURATION', 'episodes_prepared': 208,
                      'model_calls': 0, 'forecast_task_calls': calls + boundary}))


if __name__ == '__main__':
    main()
