#!/usr/bin/env python3
"""Run current-oracle and frozen 1.16.5-policy checks in Minecraft 1.21.11."""
import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from replay_b4 import compare_online, replay_episode
from stage_preflight import snapshot

ROOT = Path('/root/autodl-tmp/webcraft')
VERSION = '1.21.11'
NODE = ROOT / 'env/node/bin/node'
ENV = {**os.environ, 'CUDA_VISIBLE_DEVICES': '', 'HOME': str(ROOT / 'env/npm-home'),
       'PATH': f'{ROOT}/env/node/bin:/usr/bin:/bin'}


def public_preflight(record):
    keys = ('timestamp', 'available_gib', 'free_disk_gib', 'planned_gib',
            'cuda_disabled', 'resource_pass', 'protection_policy')
    return {key: record[key] for key in keys}


def run(command, timeout=420):
    result = subprocess.run(command, cwd=ROOT, env=ENV, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(json.dumps({
            'command': [str(part) for part in command], 'exit_code': result.returncode,
            'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:],
        }))
    return result


def execute(task, role, plan_version, attempt):
    log_path = ROOT / 'logs/b4' / role / f"{task['id']}.attempt{attempt}.jsonl"
    run(['nice', '-n', '15', str(NODE), 'harness/src/reset_arena.mjs', VERSION, '0'])
    result = run(['nice', '-n', '15', str(NODE), 'harness/src/run_plan_episode.mjs',
                  VERSION, task['id'], plan_version, str(log_path)])
    online = json.loads(result.stdout.strip().splitlines()[-1])
    replayed = replay_episode(log_path, task, plan_version)
    return {
        'attempt': attempt,
        'role': role, 'log_path': str(log_path.relative_to(ROOT)),
        'online': online, 'offline_replay': replayed,
        'offline_online_comparison': compare_online(replayed, online),
    }


def infrastructure_failure(record):
    failure = record['online'].get('execution_failure')
    return bool(failure and failure.get('error') == 'action could not be completed')


def execute_with_retry(task, role, plan_version, expected_success):
    attempts = []
    for attempt in (1, 2):
        record = execute(task, role, plan_version, attempt)
        attempts.append(record)
        replay_match = record['offline_online_comparison']['match']
        needs_retry = infrastructure_failure(record) or not replay_match
        if not needs_retry:
            break
    return {
        'attempts': attempts, 'final': attempts[-1],
        'infrastructure_failure': infrastructure_failure(attempts[-1]),
        'outcome_matches_expectation': attempts[-1]['online']['success'] == expected_success,
        'replay_matches_online': attempts[-1]['offline_online_comparison']['match'],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    preflight = snapshot(planned_gib=2)
    if not preflight['resource_pass'] or not preflight['cuda_disabled']:
        raise SystemExit(f'STOP: B4 resource gate failed: {json.dumps(public_preflight(preflight))}')
    output = ROOT / 'reports/b4_live_stale_validation.json'
    if output.exists() and not args.resume:
        raise SystemExit(f'refusing to overwrite {output}')
    tasks = [json.loads(line) for line in (ROOT / 'data/tasks.jsonl').read_text().splitlines()]
    selected = sorted((task for task in tasks
                       if task['split'] == 'dev' or task['type'] == 'efficiency'),
                      key=lambda task: task['id'])
    b2 = json.loads((ROOT / 'reports/b2_dev_live.json').read_text())
    excluded_ids = set(b2.get('infrastructure_failure_task_ids', []))
    tasks = [task for task in selected if task['id'] not in excluded_ids]
    expectations = {
        row['task_id']: row for row in (
            json.loads(line) for line in
            (ROOT / 'data/stale_agent_expectations.jsonl').read_text().splitlines()
        )
    }
    if args.resume:
        if not output.exists():
            raise SystemExit(f'cannot resume missing report {output}')
        report = json.loads(output.read_text())
        prior_error = report.pop('error', None)
        report.setdefault('resume_history', []).append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'prior_status': report.get('status'), 'prior_error': prior_error,
        })
        report['status'] = 'RUNNING'
        report['preflight_resume'] = public_preflight(preflight)
        report.setdefault('infrastructure_failures', [])
        if prior_error and not report['episodes']:
            payload = json.loads(prior_error)
            oracle = payload.get('b4_oracle_infrastructure_failure')
            if oracle:
                task = next(task for task in tasks if task['id'] == 'eff_001')
                expectation = expectations[task['id']]
                row = {
                    'task_id': task['id'], 'task_type': task['type'],
                    'split': task['split'],
                    'expected_stale_action': expectation['expected_stale_action'],
                    'expected_stale_success': expectation['expected_success'],
                    'oracle': oracle, 'stale': None, 'oracle_pass': False,
                    'stale_success_matches_expectation': None,
                    'replay_matches_online': oracle['replay_matches_online'],
                    'classification': 'infrastructure_failure',
                }
                report['episodes'].append(row)
                report['infrastructure_failures'].append({
                    'task_id': task['id'], 'role': 'oracle', 'attempts': oracle['attempts']
                })
    else:
        report = {
            'timestamp': datetime.now(timezone.utc).isoformat(), 'world': VERSION,
            'task_count_before_infrastructure_exclusion': len(selected),
            'task_count': len(tasks), 'episodes': [], 'status': 'RUNNING',
            'excluded_infrastructure_task_ids': sorted(excluded_ids),
            'exclusion_source': 'reports/b2_dev_live.json',
            'preflight': public_preflight(preflight),
            'stale_label_source': 'data/stale_agent_expectations.jsonl',
            'infrastructure_failures': [],
        }
    completed = {row['task_id'] for row in report['episodes']}
    try:
        run(['nice', '-n', '15', 'bash', 'scripts/start_one.sh', VERSION])
        for task in tasks:
            if task['id'] in completed:
                continue
            expectation = expectations[task['id']]
            oracle = execute_with_retry(task, 'oracle', VERSION, True)
            if oracle['infrastructure_failure']:
                row = {
                    'task_id': task['id'], 'task_type': task['type'],
                    'split': task['split'],
                    'expected_stale_action': expectation['expected_stale_action'],
                    'expected_stale_success': expectation['expected_success'],
                    'oracle': oracle, 'stale': None, 'oracle_pass': False,
                    'stale_success_matches_expectation': None,
                    'replay_matches_online': oracle['replay_matches_online'],
                    'classification': 'infrastructure_failure',
                }
                report['episodes'].append(row)
                report['infrastructure_failures'].append({
                    'task_id': task['id'], 'role': 'oracle', 'attempts': oracle['attempts']
                })
                output.write_text(json.dumps(report, indent=2) + '\n')
                continue
            stale = execute_with_retry(task, 'stale_1.16.5', '1.16.5',
                                       expectation['expected_success'])
            if stale['infrastructure_failure']:
                row = {
                    'task_id': task['id'], 'task_type': task['type'],
                    'split': task['split'],
                    'expected_stale_action': expectation['expected_stale_action'],
                    'expected_stale_success': expectation['expected_success'],
                    'oracle': oracle, 'stale': stale,
                    'oracle_pass': oracle['outcome_matches_expectation']
                                   and oracle['replay_matches_online'],
                    'stale_success_matches_expectation': None,
                    'replay_matches_online': stale['replay_matches_online'],
                    'classification': 'infrastructure_failure',
                }
                report['episodes'].append(row)
                report['infrastructure_failures'].append({
                    'task_id': task['id'], 'role': 'stale_1.16.5',
                    'attempts': stale['attempts']
                })
                output.write_text(json.dumps(report, indent=2) + '\n')
                continue
            row = {
                'task_id': task['id'], 'task_type': task['type'], 'split': task['split'],
                'expected_stale_action': expectation['expected_stale_action'],
                'expected_stale_success': expectation['expected_success'],
                'oracle': oracle, 'stale': stale,
                'oracle_pass': oracle['outcome_matches_expectation']
                               and oracle['replay_matches_online'],
                'stale_success_matches_expectation': stale['outcome_matches_expectation'],
                'replay_matches_online': stale['replay_matches_online'],
            }
            report['episodes'].append(row)
            output.write_text(json.dumps(report, indent=2) + '\n')
            if not row['oracle_pass']:
                raise RuntimeError(json.dumps({'b4_oracle_failure': row}))
        comparable = [row for row in report['episodes']
                      if row.get('classification') != 'infrastructure_failure']
        base_status = 'PASS' if all(
            row['stale_success_matches_expectation'] and row['replay_matches_online']
            for row in comparable
        ) else 'EXPECTATION_MISMATCH'
        report['comparable_task_count'] = len(comparable)
        report['infrastructure_failure_task_ids'] = sorted({
            row['task_id'] for row in report['infrastructure_failures']
        })
        report['status'] = ('COMPLETE_WITH_INFRASTRUCTURE_FAILURES'
                            if base_status == 'PASS'
                            and (excluded_ids or report['infrastructure_failures'])
                            else base_status)
    except Exception as error:
        report['status'] = 'FAILED'
        report['error'] = str(error)
        raise
    finally:
        subprocess.run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', VERSION],
                       cwd=ROOT, env=ENV, text=True, capture_output=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({
        'status': report['status'], 'tasks': len(report['episodes']),
        'oracle_pass': sum(row['oracle_pass'] is True for row in report['episodes']),
        'stale_expectation_match': sum(row['stale_success_matches_expectation'] is True
                                       for row in report['episodes']),
        'replay_match': sum(row['replay_matches_online'] is True
                            for row in report['episodes']),
    }))


if __name__ == '__main__':
    main()
