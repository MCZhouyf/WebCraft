"""Offline coverage, provenance, budget and credential-pattern completion audit."""
from __future__ import annotations

import hashlib
import json
import platform
import re
from collections import Counter
from datetime import datetime

from scripts import run_a18_remote_cds as run


def main():
    roots = [run.OUT, run.OUT.parent / 'remote_service_resume1']
    records = [r for directory in roots for r in run.read_jsonl(directory / 'episodes.jsonl')]
    expected = {(r['condition'], r['instance_id']) for r in run.verify_inputs()}
    keys = [(r['condition'], r['instance_id']) for r in records]
    assert len(records) == len(set(keys)) == 208 and set(keys) == expected
    assert Counter(r['condition'] for r in records) == {c: 52 for c in ('K', 'K+N', 'K+S', 'empty')}
    assert Counter(r['condition'] for r in records if r['true_feasible']) == {c: 30 for c in ('K', 'K+N', 'K+S', 'empty')}
    manifests = [json.loads((directory / 'run_manifest.json').read_text()) for directory in roots]
    assert manifests[1]['parent_completed_sha256'] == run.sha(roots[0] / 'episodes.jsonl')
    assert manifests[1]['parent_manifest_sha256'] == run.sha(roots[0] / 'run_manifest.json')
    assert manifests[0]['input_lock_sha256'] == run.sha(run.ROOT / 'prereg/a18_cds_inputs_20261017.json')
    statuses = [json.loads((directory / 'status.json').read_text()) for directory in roots]
    assert statuses[0]['completed'] == 182 and statuses[0]['safe_code'] == 'request_or_queue_timeout'
    assert statuses[1]['completed'] == 26 and statuses[1]['status'] == 'COMPLETED'
    assert all(r['owned_forward_closed'] and not r['task_private_credentials_created'] for r in statuses)
    replayed = prompt_requests = prompt_responses = 0
    for row in records:
        assert row['server_model_identifiers'] == ['coder390'] and row['local_model_identifiers'] == []
        assert 1 <= row['action_model_calls'] <= 40 and row['boundary_model_calls'] == 1
        assert row['model_calls'] == row['action_model_calls'] + row['boundary_model_calls']
        assert row['primary_cost'] <= 90
        assert (run.ROOT / row['action_log']).is_file()
        journal = run.read_jsonl(run.ROOT / row['prompt_log'])
        requests = [r for r in journal if r['record_type'] == 'full_prompt']
        responses = [r for r in journal if r['record_type'] == 'full_response']
        assert len(requests) == len(responses) == row['model_calls']
        for request in requests:
            assert request['request_sha256'] == hashlib.sha256(json.dumps(
                request['messages'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            parameters = request['parameters']
            assert parameters['temperature'] == 0.0 and parameters['max_tokens'] == 4096
            assert parameters['json_object'] and parameters['thinking'] == 'disabled'
            if request['call_type'] == 'external_react':
                assert request['messages'][0]['content'] == run.public_system_prompt('react')
        assert all(r['model'] == 'coder390' for r in responses)
        assert sum(r['usage']['prompt_tokens'] for r in responses) == row['input_tokens']
        assert sum(r['usage']['completion_tokens'] for r in responses) == row['output_tokens']
        replayed += sum(bool(r.get('replayed_from_saved_prefix')) for r in responses)
        prompt_requests += len(requests)
        prompt_responses += len(responses)
    assert replayed == sum(r.get('replayed_saved_responses', 0) for r in records) == 62
    ledgers = [r for directory in roots for r in run.read_jsonl(directory / 'ledger.jsonl')]
    good = [r for r in ledgers if r.get('record_type') == 'generation' and r.get('status') == 'ok']
    errors = [r for r in ledgers if r.get('record_type') == 'infrastructure_error']
    assert len(errors) == 1 and errors[0]['code'] == 'request_or_queue_timeout'
    assert all(r['model'] == 'coder390' for r in good)
    task = [r for r in good if not r.get('canary')]
    assert len(task) == sum(r['model_calls'] for r in records) == prompt_requests == prompt_responses
    assert sum(r['input_tokens'] for r in records) == sum(r['usage']['prompt_tokens'] for r in task)
    assert sum(r['output_tokens'] for r in records) == sum(r['usage']['completion_tokens'] for r in task)
    report = run.REPORT
    pattern = re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----|sk-[A-Za-z0-9]{16,}')
    benchmark = run.OUT.parent / 'remote_transport_benchmark'
    benchmark_good = [r for r in run.read_jsonl(benchmark / 'ledger.jsonl') if r.get('status') == 'ok']
    assert len(benchmark_good) == 13 and all(r['model'] == 'coder390' for r in benchmark_good)
    files = [p for directory in (*roots, benchmark, report, report.parent / 'remote_service_resume1')
             for p in directory.rglob('*') if p.is_file()]
    assert not any(pattern.search(p.read_text()) for p in files)
    started = datetime.fromisoformat(manifests[0]['started_at_utc'])
    finished = max(datetime.fromisoformat(r['timestamp']) for r in good)
    result = {'status': 'PASS', 'completed_unique_keys': 208, 'missing_keys': 0,
              'conditions': {c: 52 for c in ('K', 'K+N', 'K+S', 'empty')},
              'feasible_per_condition': 30, 'infeasible_per_condition': 22,
              'server_model_identifiers': ['coder390'], 'mixed_model_identifiers': False,
              'local_14B_rows_pooled': False, 'frozen_source_material_hashes': 'PASS',
              'original_completed_rows_retained': 182, 'recovered_completed_rows': 26,
              'offline_replayed_responses_not_new_generations': replayed,
              'successful_task_generations': len(task),
              'successful_canaries': len(good) - len(task), 'successful_generations': len(good),
              'additional_benchmark_generations_including_its_canary': len(benchmark_good),
              'total_successful_generations_including_benchmark': len(good) + len(benchmark_good),
              'total_inference_attempts_including_timeout_and_benchmark': len(good) + len(benchmark_good) + len(errors),
              'infrastructure_timeouts': len(errors), 'timeouts_counted_as_cognitive_failure': 0,
              'max_action_calls': max(r['action_model_calls'] for r in records),
              'max_primary_cost': max(r['primary_cost'] for r in records),
              'all_episode_prompts_responses_and_actions_present': True,
              'prompt_hashes_and_actual_usage_match': True,
              'credential_pattern_matches': 0, 'files_scanned': len(files),
              'owned_forwards_closed': True, 'private_key_files_created': False,
              'python_version': platform.python_version(),
              'started_at_utc': started.isoformat(), 'last_response_at_utc': finished.isoformat(),
              'observed_main_wall_seconds': (finished - started).total_seconds(),
              'peak_client_RSS_KiB': max(r['peak_RSS_KiB'] for r in statuses),
              'physical_remote_GPU_topology_verified': False, 'remote_push': False}
    run.write(report / 'VALIDATION.json', result)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
