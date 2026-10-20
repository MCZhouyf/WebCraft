"""Read-only A17 artifact acceptance, with no credential or network imports."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/post_review/a17_ext_agents_20261017'
REPORT = ROOT / 'reports/main/post_review/a17_ext_agents_20261017'


def load_csv(name):
    with (REPORT / name).open(newline='') as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path):
    with path.open() as handle:
        return [json.loads(s) for s in handle if s.strip()]


def main():
    if not (OUT / 'status.json').is_file():
        print(json.dumps({'status': 'IN_PROGRESS', 'reason': 'final_acceptance_requires_closed_worker_logs'}))
        return 0
    issues = []
    manifest = json.loads((OUT / 'run_manifest.json').read_text())
    expected = {(r['method'], r['task_id'], r['world'], r['condition']) for r in manifest['jobs']}
    scored = load_csv('per_episode.csv')
    actual = [r for r in scored if r['source'] == 'A17_new_llm']
    keys = [(r['method'], r['task_id'], r['world'], r['condition']) for r in actual]
    if len(expected) != 180 or len(set(keys)) != len(keys) or not set(keys) <= expected:
        issues.append('episode_key_alignment')
    coverage = load_csv('planned_episode_coverage.csv')
    completed = {r['job_id'] for r in coverage if r['status'] == 'completed'}
    raw = [r for p in sorted(OUT.glob('worker_*/episodes.jsonl')) for r in read_jsonl(p)]
    if completed != {r['job_id'] for r in raw}:
        issues.append('completed_queue_raw_alignment')
    replay_disagreements = []
    prompt_violations = []
    prompt_count = 0
    material_mismatches = []
    baseline = {tuple(r[k] for k in ('task_id', 'world', 'condition')): r
                for r in read_jsonl(OUT / 'pipeline_comparison_snapshot.jsonl') if r['method'] == 'naive_rag'}
    for row in raw:
        match = next((r for r in actual if all(r[k] == row[k] for k in ('method', 'task_id', 'world', 'condition'))), None)
        if match is None or int(match['SR']) != row['gold_score']['SR'] or match['terminal'] != row['gold_score']['terminal']:
            replay_disagreements.append(row['job_id'])
        if row['model_calls'] > 40:
            issues.append('call_budget_exceeded')
        if not (ROOT / row['action_log']).is_file() or not (ROOT / row['prompt_log']).is_file():
            issues.append('missing_raw_log')
            continue
        events = read_jsonl(ROOT / row['action_log'])
        reference = baseline.get(tuple(row[k] for k in ('task_id', 'world', 'condition')))
        initial = next((e['result'] for e in events if e['method'] == 'inventory'), None)
        other_initial = next((e['result'] for e in reference['events'] if e['method'] == 'inventory'), None) if reference else None
        if initial != other_initial:
            material_mismatches.append(row['job_id'])
        for request in read_jsonl(ROOT / row['prompt_log']):
            if request.get('record_type') != 'full_prompt' or not request.get('call_type', '').startswith('external_'):
                continue
            prompt_count += 1
            messages = request['messages']
            payload = json.loads(messages[-1]['content'])
            system_hash = hashlib.sha256(messages[0]['content'].encode()).hexdigest()
            valid = (system_hash == manifest['prompt_sha256'][row['method']]
                     and set(payload) <= {'task', 'observation', 'trace', 'temporary_lesson'}
                     and set(payload['task']) == {'goal', 'initial_inventory'}
                     and request['parameters'] == dict(temperature=0.0, max_tokens=4096, json_object=True, thinking='disabled'))
            if not valid:
                prompt_violations.append(row['job_id'])
    if replay_disagreements:
        issues.append('online_offline_gold_disagreement')
    if prompt_violations:
        issues.append('prompt_or_runtime_metadata_contract')
    if material_mismatches:
        issues.append('frozen_material_package_disagreement')
    responses = [r for p in OUT.glob('**/*ledger.jsonl') for r in read_jsonl(p) if r.get('status') == 'ok']
    models = sorted({r.get('model') for r in responses if r.get('model')})
    if models != ['glm-5.2']:
        issues.append('model_identity')
    prereg_time = datetime.fromisoformat(subprocess.check_output(
        ['git', 'show', '-s', '--format=%cI', '77324cc'], cwd=ROOT, text=True).strip())
    first_response = min(datetime.fromisoformat(r['timestamp']) for r in responses)
    first_request = min(datetime.fromisoformat(r['timestamp']) - timedelta(seconds=float(r.get('elapsed_seconds', 0)))
                        for r in responses)
    if prereg_time >= first_request:
        issues.append('prereg_not_before_first_request')
    canonical = load_csv('attribution_comparison.csv')
    if any(r[k] not in ('', None) for r in canonical if r['method'] in ('direct_llm', 'react', 'reflexion')
           for k in ('canonical_query_contact', 'canonical_action_contact', 'canonical_propagation')):
        issues.append('fabricated_canonical_value')
    forbidden_pattern = re.compile(rb'sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}')
    secret_hits = []
    for directory in (OUT, REPORT):
        for path in directory.rglob('*'):
            if path.is_file() and path.suffix in ('.json', '.jsonl', '.csv', '.md', '.log'):
                if forbidden_pattern.search(path.read_bytes()):
                    secret_hits.append(str(path.relative_to(ROOT)))
    if secret_hits:
        issues.append('secret_pattern_found')
    result = dict(status='PASS' if not issues else 'FAIL', issues=sorted(set(issues)),
                  planned_episodes=len(expected), completed_raw_episodes=len(raw), scored_episodes=len(actual),
                  complete=len(actual) == 180, replay_disagreements=replay_disagreements,
                  audited_action_prompts=prompt_count, prompt_contract_violations=prompt_violations,
                  material_package_mismatches=material_mismatches,
                  model_identifiers=models, prereg_commit='77324cc', prereg_timestamp=prereg_time.isoformat(),
                  earliest_successful_request=first_request.isoformat(),
                  earliest_response_timestamp=first_response.isoformat(),
                  request_timestamp_basis='response_timestamp_minus_recorded_elapsed_seconds',
                  prereg_precedes_requests=prereg_time < first_request,
                  secret_pattern_hit_files=secret_hits,
                  missing_episodes=len(expected - set(keys)))
    (REPORT / 'acceptance_checks.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))
    return int(bool(issues))


if __name__ == '__main__':
    raise SystemExit(main())
