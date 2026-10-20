#!/usr/bin/env python3
"""Run the author-authorized frozen-parameter D1 reproduction once."""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from stage_preflight import snapshot

ROOT = Path('/root/autodl-tmp/webcraft')
NODE = ROOT / 'env/node/bin/node'
ENV = {**os.environ, 'CUDA_VISIBLE_DEVICES': '', 'HOME': str(ROOT / 'env/npm-home'),
       'PATH': f'{ROOT}/env/node/bin:/usr/bin:/bin'}


def run(command, timeout=480):
    result = subprocess.run(command, cwd=ROOT, env=ENV, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(json.dumps({'command': list(map(str, command)),
                                       'stdout': result.stdout[-4000:],
                                       'stderr': result.stderr[-4000:]}))
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    gate = snapshot(planned_gib=2)
    if not gate['resource_pass'] or not gate['cuda_disabled']:
        raise SystemExit('STOP: D1 resource gate failed')
    output = ROOT / 'reports/d1_reproduction.json'
    action_log = ROOT / 'logs/d1/fea_009_1.16.5.jsonl'
    diagnostic_log = ROOT / 'logs/d1/fea_009_1.16.5_diagnostic.jsonl'
    if output.exists() or action_log.exists() or diagnostic_log.exists():
        raise SystemExit('refusing to overwrite D1 evidence')
    action_log.parent.mkdir(parents=True, exist_ok=True)
    report = {'timestamp': datetime.now(timezone.utc).isoformat(),
              'task_id': 'fea_009', 'world': '1.16.5',
              'frozen_navigation_timeout_seconds': 15, 'status': 'RUNNING'}
    try:
        run(['nice', '-n', '15', 'bash', 'scripts/start_one.sh', '1.16.5'])
        run(['nice', '-n', '15', str(NODE), 'harness/src/reset_arena.mjs', '1.16.5', '0'])
        result = run(['nice', '-n', '15', str(NODE),
                      'harness/src/run_plan_episode.mjs', '1.16.5', 'fea_009',
                      '1.16.5', str(action_log), str(diagnostic_log)])
        report['episode'] = json.loads(result.stdout.strip().splitlines()[-1])
        report['status'] = 'REPRODUCED' if not report['episode']['success'] else 'NOT_REPRODUCED'
    finally:
        subprocess.run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', '1.16.5'],
                       cwd=ROOT, env=ENV, text=True, capture_output=True)
        if action_log.exists(): report['action_log_sha256'] = digest(action_log)
        if diagnostic_log.exists(): report['diagnostic_log_sha256'] = digest(diagnostic_log)
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
