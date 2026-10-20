#!/usr/bin/env python3
"""Run sequential B1 reset determinism and the 90 leakage checks."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')
NODE = ROOT / 'env/node/bin/node'
ENV = {**os.environ, 'CUDA_VISIBLE_DEVICES': '',
       'HOME': str(ROOT / 'env/npm-home'),
       'PATH': f'{ROOT}/env/node/bin:/usr/bin:/bin'}


def run(command, timeout=300):
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
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'versions': {}, 'sequential_one_server_at_a_time': True,
        'cuda_disabled': True,
    }
    try:
        for version in VERSIONS:
            run(['nice', '-n', '15', 'bash', 'scripts/start_one.sh', version])
            reset_records = []
            for repetition in range(1, 4):
                started = time.monotonic()
                result = run([
                    'nice', '-n', '15', str(NODE),
                    'harness/src/reset_arena.mjs', version, '0',
                ])
                elapsed = time.monotonic() - started
                record = json.loads(result.stdout.strip().splitlines()[-1])
                reset_records.append({
                    'repetition': repetition, 'hash': record['hash'],
                    'elapsed_seconds': round(elapsed, 3),
                    'inventory': record['inventory'],
                })
            leakage = run([
                'nice', '-n', '15', str(NODE),
                'harness/src/stage1_leakage_probe.mjs', version,
            ])
            leakage_result = json.loads(leakage.stdout.strip().splitlines()[-1])
            run(['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', version])
            hashes = {record['hash'] for record in reset_records}
            report['versions'][version] = {
                'resets': reset_records,
                'within_version_hash_consistent': len(hashes) == 1,
                'leakage': leakage_result,
            }
        all_hashes = {
            record['hash']
            for version in report['versions'].values()
            for record in version['resets']
        }
        report['cross_version_hash_consistent'] = len(all_hashes) == 1
        report['total_leakage_checks'] = sum(
            version['leakage']['checks'] for version in report['versions'].values()
        )
        report['passed'] = (
            report['cross_version_hash_consistent']
            and report['total_leakage_checks'] == 90
            and all(version['within_version_hash_consistent']
                    and version['leakage']['passed']
                    for version in report['versions'].values())
        )
    finally:
        for version in VERSIONS:
            subprocess.run(
                ['nice', '-n', '15', 'bash', 'scripts/stop_one.sh', version],
                cwd=ROOT, env=ENV, text=True, capture_output=True,
            )
    (ROOT / 'reports/b1_live_validation.json').write_text(
        json.dumps(report, indent=2) + '\n'
    )
    print(json.dumps(report))
    raise SystemExit(0 if report.get('passed') else 1)


if __name__ == '__main__':
    main()
