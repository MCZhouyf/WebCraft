#!/usr/bin/env python3
"""Run the preregistration-stage B3 live truth audit one server at a time."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from stage_preflight import snapshot

ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')
NODE = ROOT / 'env/node/bin/node'
ENV = {**os.environ, 'CUDA_VISIBLE_DEVICES': '', 'HOME': str(ROOT / 'env/npm-home'),
       'PATH': f'{ROOT}/env/node/bin:/usr/bin:/bin'}


def public_preflight(record):
    keys = ('timestamp', 'available_gib', 'free_disk_gib', 'planned_gib',
            'cuda_disabled', 'resource_pass', 'protection_policy')
    return {key: record[key] for key in keys}


def run(command, timeout=900):
    result = subprocess.run(command, cwd=ROOT, env=ENV, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(json.dumps({
            'command': [str(part) for part in command], 'exit_code': result.returncode,
            'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:],
        }))
    return result


def main():
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    preflight = snapshot(planned_gib=2)
    if not preflight['resource_pass'] or not preflight['cuda_disabled']:
        raise SystemExit(f'STOP: B3 resource gate failed: {json.dumps(public_preflight(preflight))}')
    output = ROOT / 'reports/b3_live_truth_audit.json'
    if output.exists():
        raise SystemExit(f'refusing to overwrite {output}')
    run(['nice', '-n', '15', 'python3', 'scripts/prepare_b3_samples.py'])
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'sample_manifest': 'reports/b3_sample_manifest.json',
        'one_server_one_bot': True, 'versions': {}, 'status': 'RUNNING',
        'preflight': public_preflight(preflight),
    }
    try:
        for version in VERSIONS:
            run(['nice', '-n', '15', 'bash', 'scripts/start_one.sh', version])
            try:
                result = run(['nice', '-n', '15', str(NODE),
                              'harness/src/run_b3_truth_audit.mjs', version])
                row = json.loads(result.stdout.strip().splitlines()[-1])
                report['versions'][version] = row
                output.write_text(json.dumps(report, indent=2) + '\n')
            finally:
                run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', version])
        report['status'] = 'PASS' if all(
            row['recipe_agreement'] == row['recipe_total']
            and row['drop_agreement'] == row['drop_total']
            for row in report['versions'].values()
        ) else 'TRUTH_MISMATCH'
    except Exception as error:
        report['status'] = 'FAILED'
        report['error'] = str(error)
        raise
    finally:
        for version in VERSIONS:
            subprocess.run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', version],
                           cwd=ROOT, env=ENV, text=True, capture_output=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'versions': {
        version: {'recipes': f"{row['recipe_agreement']}/{row['recipe_total']}",
                  'drops': f"{row['drop_agreement']}/{row['drop_total']}"}
        for version, row in report['versions'].items()
    }}))


if __name__ == '__main__':
    main()
