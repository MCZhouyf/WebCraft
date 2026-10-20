#!/usr/bin/env python3
"""Test a manifest-only copy, excluding locally decrypted reviewer artifacts."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'release/package'
OUT = ROOT / 'reports/paperA/w9_release_fix'
PYTHON = Path('/root/autodl-tmp/w9-clean-env/bin/python')
PASSWORD = Path('/root/.local/share/webcraft-review/w9_password')


def main():
    env = dict(os.environ, PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1',
               CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='1',
               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='w9_distribution_', dir=ROOT.parent) as temporary:
        target = Path(temporary)
        names = [line.split('  ', 1)[1] for line in (PACKAGE / 'MANIFEST.sha256').read_text().splitlines()]
        for name in names + ['MANIFEST.sha256']:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PACKAGE / name, destination)
        if (target / 'assets/frozen/main.jsonl.gz').exists():
            raise RuntimeError('restricted reference unexpectedly distributed in plaintext')
        decrypt = subprocess.Popen(
            ['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2',
             '-in', str(target / 'corpus/encrypted/w9_replay_restricted.tar.gz.enc'),
             '-pass', f'file:{PASSWORD}'], stdout=subprocess.PIPE)
        extract = subprocess.run(['tar', 'xz', '-C', str(target)], stdin=decrypt.stdout)
        decrypt.stdout.close()
        if decrypt.wait() or extract.returncode:
            raise RuntimeError('reviewer archive transport failed')
        tests = subprocess.run([str(PYTHON), '-I', 'repro/test_package.py'],
                               cwd=target, env=env, text=True, capture_output=True)
        (OUT / 'clean_distribution_tests.log').write_text(tests.stdout + tests.stderr)
        if tests.returncode:
            raise RuntimeError('clean distribution tests failed; see saved log')
        smoke = subprocess.run([str(PYTHON), '-I', 'repro/runtime.py', '--scope', 'main',
                                '--limit', '1', '--output', str(target / 'runs/smoke')],
                               cwd=target, env=env, text=True, capture_output=True)
        (OUT / 'clean_distribution_smoke.log').write_text(smoke.stdout + smoke.stderr)
        if smoke.returncode:
            raise RuntimeError('clean distribution smoke failed; see saved log')
        result = json.loads((target / 'runs/smoke/main_result.json').read_text())
        if result['compared'] != 1 or result['summary'] != {'equal': 1}:
            raise RuntimeError('clean distribution first episode did not match')
        summary = {'copied_manifest_files': len(names), 'restricted_files_decrypted': True,
                   'clean_tests_returncode': tests.returncode,
                   'first_episode_equal': True, 'isolated_imports': result['isolated_imports'],
                   'scope': 'transport smoke only; full replay coverage reported separately'}
        (OUT / 'clean_distribution_smoke.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary))


if __name__ == '__main__':
    main()
