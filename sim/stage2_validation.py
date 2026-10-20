"""Run the preregistered S2 validation suite for the deterministic backend."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metrics.episode import aggregate, episode_metrics
from sim.runner import ROOT, load_tasks, run_oracle
from sim.semantic_agent import PUBLIC_METHODS, _ApiFacade, _public_value, _safe_error, create_simulated_agent

WORLDS = ('1.16.5', '1.19', '1.21.11')
OUTPUT = ROOT / 'reports'
LOGS = ROOT / 'logs' / 'sim_stage2'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def stable_result(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != 'elapsed_seconds'}


def plan_actions(task: dict[str, Any], world: str) -> list[dict[str, Any]] | None:
    plan = task['oracle_plans'][world]
    return None if plan is None else plan['actions']


def observed_stale_action(task: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
    """Derive the label from a frozen old plan and its execution, not its expected label."""
    old_actions = plan_actions(task, '1.16.5')
    current_actions = plan_actions(task, '1.21.11')
    if old_actions is None and task['feasible']['1.21.11']:
        return True, 'old_policy_reports_infeasible_for_current_feasible_task'
    if result['execution_failure'] is not None:
        return True, 'old_policy_action_failed_in_current_world'
    if old_actions != current_actions:
        return True, 'old_policy_action_sequence_differs_from_current_oracle'
    return False, 'old_policy_sequence_matches_current_oracle'


def validate_current(result: dict[str, Any]) -> list[str]:
    errors = []
    if not result['success']:
        errors.append('oracle_success_false')
    if result['reported_feasible'] != result['expected_feasible']:
        errors.append('feasibility_report_mismatch')
    if result['expected_feasible'] and not result['produced_goal']:
        errors.append('goal_not_produced')
    if result['expected_feasible'] and result['primary_cost'] != result['oracle_cost_current_world']:
        errors.append('primary_cost_mismatch')
    if result['execution_failure'] is not None:
        errors.append('execution_failure')
    return errors


def run_oracle_twice(tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for task_id in sorted(tasks):
        task = tasks[task_id]
        for world in WORLDS:
            runs = []
            for run_index in (1, 2):
                path = LOGS / f'run{run_index}' / world / f'{task_id}.jsonl'
                result = run_oracle(task, world, log_path=path)
                runs.append({'result': result, 'log_path': str(path.relative_to(ROOT)),
                             'sha256': sha256(path)})
            errors = validate_current(runs[0]['result'])
            if stable_result(runs[0]['result']) != stable_result(runs[1]['result']):
                errors.append('result_not_deterministic')
            if runs[0]['sha256'] != runs[1]['sha256']:
                errors.append('log_not_byte_identical')
            rows.append({'task_id': task_id, 'type': task['type'], 'split': task['split'],
                         'world': world, 'run1': runs[0], 'run2': runs[1], 'errors': errors})
    return {
        'stage': 'S2.1', 'timestamp': datetime.now(timezone.utc).isoformat(),
        'task_count': len(tasks), 'task_world_pairs': len(rows), 'rows': rows,
        'development_pairs': sum(row['split'] == 'dev' for row in rows),
        'evaluation_pairs': sum(row['split'] == 'eval' for row in rows),
        'passed': not any(row['errors'] for row in rows),
    }


def run_stale(tasks: dict[str, dict[str, Any]], expectations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for task_id in sorted(tasks):
        task, expected = tasks[task_id], expectations[task_id]
        path = LOGS / 'stale_1.16.5_in_1.21.11' / f'{task_id}.jsonl'
        result = run_oracle(task, '1.21.11', plan_version='1.16.5', log_path=path)
        stale, rationale = observed_stale_action(task, result)
        errors = []
        if result['success'] != expected['expected_success']:
            errors.append('success_mismatch')
        if stale != expected['expected_stale_action']:
            errors.append('stale_action_mismatch')
        rows.append({'task_id': task_id, 'type': task['type'], 'split': task['split'],
                     'expected_stale_action': expected['expected_stale_action'],
                     'observed_stale_action': stale, 'stale_rationale': rationale,
                     'expected_success': expected['expected_success'], 'result': result,
                     'log_path': str(path.relative_to(ROOT)), 'sha256': sha256(path),
                     'errors': errors})
    return {'stage': 'S2.2', 'timestamp': datetime.now(timezone.utc).isoformat(),
            'task_count': len(rows), 'rows': rows, 'passed': not any(row['errors'] for row in rows)}


def metric_row(path: Path, result: dict[str, Any], task: dict[str, Any], stale: bool) -> dict[str, Any]:
    trace = events(path)
    action_indices = [index for index, event in enumerate(trace)
                      if event['method'] not in ('inventory', 'submit')]
    vectors = {}
    if stale and action_indices:
        vectors[action_indices[0]] = {'1.16.5': True, '1.19': None, '1.21.11': False}
    return episode_metrics(
        trace, goal=task['target'], world='1.21.11', true_feasible=task['feasible']['1.21.11'],
        success=result['success'], reported_feasible=result['reported_feasible'],
        version_hashes={}, mechanism_vectors=vectors,
        oracle_cost=task['oracle_cost']['1.21.11'], task_type=task['type'],
        claim_answers=[], claim_truth=[],
    )


def run_metrics(tasks: dict[str, dict[str, Any]], current: dict[str, Any], stale: dict[str, Any]) -> dict[str, Any]:
    rows, mismatches = [], []
    for source, records in (('oracle', current['rows']), ('stale_1.16.5', stale['rows'])):
        for record in records:
            if source == 'oracle' and record['world'] != '1.21.11':
                continue
            task = tasks[record['task_id']]
            if source == 'oracle':
                path = ROOT / record['run1']['log_path']
                result, stale_label = record['run1']['result'], False
            else:
                path = ROOT / record['log_path']
                result, stale_label = record['result'], record['observed_stale_action']
            online = metric_row(path, result, task, stale_label)
            offline = metric_row(path, result, task, stale_label)
            equal = online == offline
            rows.append({'source': source, 'task_id': task['id'], 'online': online,
                         'offline': offline, 'match': equal})
            if not equal:
                mismatches.append({'source': source, 'task_id': task['id']})
    return {'stage': 'S2.3', 'timestamp': datetime.now(timezone.utc).isoformat(),
            'episodes': len(rows), 'rows': rows,
            'aggregate_online': aggregate([row['online'] for row in rows]),
            'aggregate_offline': aggregate([row['offline'] for row in rows]),
            'mismatches': mismatches, 'passed': not mismatches}


def expect(name: str, condition: bool, checks: list[dict[str, Any]]) -> None:
    checks.append({'name': name, 'passed': bool(condition)})


def run_leakage_probe(version: str) -> list[dict[str, Any]]:
    """Execute the live probe's public-contract assertions against simulator output."""
    checks: list[dict[str, Any]] = []
    tempdir = tempfile.TemporaryDirectory()
    log_path = Path(tempdir.name) / 'public.jsonl'
    handle = create_simulated_agent(version=version, log_path=log_path,
                                    answer_claims=lambda claims: [True, False, 'unknown'])
    api, evaluator = handle['api'], handle['evaluator']
    try:
        expect('exact_8_method_allowlist', sorted(name for name in dir(api) if not name.startswith('_')) == sorted(PUBLIC_METHODS), checks)
        for key in ('bot', 'version', 'registry', 'recipesFor', 'recipesAll', 'chat', 'command', 'rcon', 'answer_claims'):
            expect(f'blocked_{key}', not hasattr(api, key), checks)
        expect('frozen_facade_slots', _ApiFacade.__slots__ == ('_token',), checks)
        expect('no_truth_or_evaluator_attribute', not any(hasattr(api, key) for key in ('truth', 'evaluator')), checks)
        for key in ('x', 'y', 'z', 'position', 'coordinates', 'height', 'world_boundary', 'dimension_height', 'time_ticks'):
            expect(f'redacts_{key}', _public_value({key: -60}) == {}, checks)
        expect('errors_are_allowlisted', _safe_error(Exception('position (1, -60, 4) entity_id=7')) == 'action could not be completed', checks)
        invalid = api.check_name('height=-60 1.19')
        expect('invalid_input_safe_error', invalid.get('error') == 'invalid item identifier', checks)
        expect('failed_name_cost_one', invalid['cost']['primary'] == 1, checks)
        try:
            evaluator.answer_claims([1])
            before_finish = False
        except Exception:
            before_finish = True
        expect('evaluation_rejected_mid_episode', before_finish, checks)
        api.report_version('1.19')
        api.inventory()
        evaluator.finish_episode()
        expect('evaluation_returns_agent_answers', evaluator.answer_claims([1, 2, 3]) == [True, False, 'unknown'], checks)
        try:
            api.inventory()
            after_finish = False
        except Exception:
            after_finish = True
        expect('actions_rejected_after_end', after_finish, checks)
        # The semantic replacement for the live absolute-spawn assertion: no location exists publicly.
        expect('no_public_location_field', all(key not in str(invalid) for key in ('position', 'coordinates', 'entity_id')), checks)
        public_log = log_path.read_text()
        expect('public_log_no_world_or_coordinate_leaks', re.search(r'\b1\.(?:16\.5|19|21\.11)\b|"(?:x|y|z|position|entity_id|time_ticks)"\s*:', public_log) is None, checks)
        expect('version_guess_is_hashed_in_log', 'guess_sha256' in public_log and '"1.19"' not in public_log, checks)
    finally:
        handle['shutdown']()
        tempdir.cleanup()
    if len(checks) != 30:
        raise RuntimeError(f'expected 30 leakage assertions, got {len(checks)}')
    return checks


def run_leakage() -> dict[str, Any]:
    versions = {}
    for version in WORLDS:
        checks = run_leakage_probe(version)
        versions[version] = {'checks': checks, 'passed': all(check['passed'] for check in checks)}
    total = sum(len(row['checks']) for row in versions.values())
    return {'stage': 'S2.4', 'timestamp': datetime.now(timezone.utc).isoformat(),
            'versions': versions, 'total_leakage_checks': total,
            'passed': total == 90 and all(row['passed'] for row in versions.values())}


def main() -> None:
    tasks = load_tasks()
    expectations = {row['task_id']: row for row in
                    (json.loads(line) for line in (ROOT / 'data/stale_agent_expectations.jsonl').read_text().splitlines() if line)}
    if set(tasks) != set(expectations) or len(tasks) != 42:
        raise SystemExit('STOP: frozen task/expectation manifest mismatch')
    current = run_oracle_twice(tasks)
    stale = run_stale(tasks, expectations)
    metrics = run_metrics(tasks, current, stale)
    leakage = run_leakage()
    reports = {
        'sim_oracle_validation.json': current,
        'sim_stale_validation.json': stale,
        'sim_metrics_validation.json': metrics,
        'sim_leakage_validation.json': leakage,
    }
    for name, payload in reports.items():
        (OUTPUT / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    passed = all(payload['passed'] for payload in reports.values())
    summary = {'timestamp': datetime.now(timezone.utc).isoformat(), 'status': 'PASS' if passed else 'FAILED',
               'stages': {payload['stage']: payload['passed'] for payload in reports.values()},
               'reports': sorted(f'reports/{name}' for name in reports)}
    (OUTPUT / 'sim_stage2_summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
