#!/usr/bin/env python3
"""Run oracle plans live, sequentially by world and task."""
import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from stage_preflight import snapshot

ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')
NODE = ROOT / 'env/node/bin/node'
ENV = {**os.environ, 'CUDA_VISIBLE_DEVICES': '',
       'HOME': str(ROOT / 'env/npm-home'),
       'PATH': f'{ROOT}/env/node/bin:/usr/bin:/bin'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def public_preflight(record):
    keys = ('timestamp', 'available_gib', 'free_disk_gib', 'planned_gib',
            'cuda_disabled', 'resource_pass', 'protection_policy')
    return {key: record[key] for key in keys}


def run(command, timeout=420):
    result = subprocess.run(
        command, cwd=ROOT, env=ENV, text=True, capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(json.dumps({
            'command': [str(part) for part in command],
            'exit_code': result.returncode,
            'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:],
        }))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--split', choices=('dev', 'eval'), required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    preflight = snapshot(planned_gib=2)
    if not preflight['resource_pass'] or not preflight['cuda_disabled']:
        raise SystemExit(f'STOP: B2 resource gate failed: {json.dumps(public_preflight(preflight))}')
    tasks = [json.loads(line) for line in (ROOT / 'data/tasks.jsonl').read_text().splitlines()
             if line]
    tasks = [task for task in tasks if task['split'] == args.split]
    output = ROOT / f'reports/b2_{args.split}_live.json'
    if output.exists() and not args.resume:
        raise SystemExit(f'refusing to overwrite {output}')
    if args.resume:
        if not output.exists():
            raise SystemExit(f'cannot resume missing report {output}')
        report = json.loads(output.read_text())
        if report.get('split') != args.split:
            raise SystemExit('resume report split mismatch')
        report.setdefault('resume_history', []).append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'prior_status': report.get('status'), 'prior_error': report.pop('error', None),
        })
        report['status'] = 'RUNNING'
        report['preflight_resume'] = public_preflight(preflight)
    else:
        report = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'split': args.split, 'task_count': len(tasks), 'episodes': [],
            'sequential_one_server_at_a_time': True, 'cuda_disabled': True,
            'status': 'RUNNING', 'preflight': public_preflight(preflight),
            'exclusion_candidates': [], 'infrastructure_failures': [],
        }
    report.setdefault('exclusion_candidates', [])
    report.setdefault('infrastructure_failures', [])
    if args.resume and args.split == 'dev':
        known = {(row['world'], row['task_id'])
                 for row in report['infrastructure_failures']}
        for row in report['episodes']:
            key = (row['world'], row['task_id'])
            if not row['passed'] and key not in known:
                report['infrastructure_failures'].append({
                    'task_id': row['task_id'], 'task_type': row['task_type'],
                    'world': row['world'], 'attempts': row['attempts'],
                    'classification': 'infrastructure_failure',
                })
    completed = {(row['world'], row['task_id']) for row in report['episodes']}
    try:
        for version in VERSIONS:
            pending_tasks = [task for task in tasks if (version, task['id']) not in completed]
            if not pending_tasks:
                continue
            run(['nice', '-n', '15', 'bash', 'scripts/start_one.sh', version])
            try:
                for task in pending_tasks:
                    attempts = []
                    # Amendment V2 grants one identical retry for infrastructure failures
                    # in both development and evaluation validation.
                    maximum_attempts = 2
                    for attempt in range(1, maximum_attempts + 1):
                        log_path = (ROOT / 'logs/b2' / args.split / version
                                    / f"{task['id']}.attempt{attempt}.jsonl")
                        try:
                            run(['nice', '-n', '15', str(NODE),
                                 'harness/src/reset_arena.mjs', version, '0'])
                            episode = run([
                                'nice', '-n', '15', str(NODE),
                                'harness/src/run_plan_episode.mjs', version, task['id'],
                                version, str(log_path),
                            ])
                            record = json.loads(episode.stdout.strip().splitlines()[-1])
                            valid = (record['success'] and not record['execution_failure']
                                     and (not record['expected_feasible']
                                          or record['primary_cost']
                                          == record['oracle_cost_current_world']))
                            attempts.append({
                                'attempt': attempt, 'record': record, 'valid': valid,
                                'log_path': str(log_path.relative_to(ROOT)),
                                'log_sha256': sha256(log_path),
                            })
                        except Exception as error:
                            attempts.append({
                                'attempt': attempt, 'record': None, 'valid': False,
                                'log_path': str(log_path.relative_to(ROOT)),
                                'log_sha256': sha256(log_path) if log_path.exists() else None,
                                'public_failure': str(error),
                            })
                        if attempts[-1]['valid']:
                            break
                    episode_summary = {
                        'task_id': task['id'], 'task_type': task['type'],
                        'world': version, 'attempts': attempts,
                        'passed': attempts[-1]['valid'],
                    }
                    report['episodes'].append(episode_summary)
                    if not episode_summary['passed']:
                        failure = {
                            'task_id': task['id'], 'task_type': task['type'],
                            'world': version, 'attempts': attempts,
                        }
                        if args.split == 'eval':
                            report['exclusion_candidates'].append(failure)
                        else:
                            failure['classification'] = 'infrastructure_failure'
                            report['infrastructure_failures'].append(failure)
                    output.write_text(json.dumps(report, indent=2) + '\n')
            finally:
                run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', version])
        report['excluded_task_ids'] = sorted({
            row['task_id'] for row in report['exclusion_candidates']
        })
        report['infrastructure_failure_task_ids'] = sorted({
            row['task_id'] for row in report['infrastructure_failures']
        })
        if report['infrastructure_failure_task_ids']:
            report['status'] = 'COMPLETE_WITH_INFRASTRUCTURE_FAILURES'
        elif report['excluded_task_ids']:
            report['status'] = 'PASS_WITH_EXCLUSIONS'
        else:
            report['status'] = 'PASS'
        report['validation_complete'] = True
        report['passed'] = not report['infrastructure_failure_task_ids']
    except Exception as error:
        report['status'] = 'FAILED'
        report['passed'] = False
        report['error'] = str(error)
        raise
    finally:
        for version in VERSIONS:
            subprocess.run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', version],
                           cwd=ROOT, env=ENV, text=True, capture_output=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({
        'status': report['status'], 'split': args.split,
        'episodes': len(report['episodes']),
        'elapsed_seconds': round(sum(
            attempt['record']['elapsed_seconds']
            for row in report['episodes'] for attempt in row['attempts']
            if attempt.get('record')
        ), 3),
    }))


if __name__ == '__main__':
    main()
