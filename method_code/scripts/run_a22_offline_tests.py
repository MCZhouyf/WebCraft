"""Run four disjoint offline pytest shards without xdist or simulator access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from arbiter.checkpoint import effective_available_bytes

ROOT = Path(__file__).resolve().parents[1]
SHARDS = (
    ('tests/test_a22_pf_policy.py', 'tests/test_a22_preflight.py'),
    ('tests/test_aggregation.py', 'tests/test_planning.py'),
    ('tests/test_final_execution.py', 'tests/test_contracts.py'),
    ('tests/test_fairness.py',),
)


def main():
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise SystemExit('WAITING_RESOURCES: retry after 600 seconds')
    out = ROOT / 'reports/main/post_review/a22_pf_rag/offline_tests'
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': 'src:.:scripts', 'OPENBLAS_NUM_THREADS': '1',
           'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    jobs = []
    started = time.monotonic()
    try:
        for index, files in enumerate(SHARDS):
            log = (out / f'shard_{index}.log').open('w')
            cmd = ['nice', '-n', '15', sys.executable, '-m', 'pytest', '-q',
                   '-p', 'no:cacheprovider', *files]
            process = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            jobs.append((process, log, files))
        codes = [p.wait() for p, _, _ in jobs]
    finally:
        for p, log, _ in jobs:
            if p.poll() is None:
                p.terminate()
                p.wait()
            log.close()
    result = {'workers': len(jobs), 'elapsed_seconds': time.monotonic() - started,
              'exit_codes': codes, 'status': 'PASS' if not any(codes) else 'FAIL',
              'shards': [list(x) for x in SHARDS], 'llm_calls': 0, 'environment_episodes': 0}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    raise SystemExit(int(any(codes)))


if __name__ == '__main__':
    main()
