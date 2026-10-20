#!/usr/bin/env python3
"""Preserve replay trajectories in an encrypted, separately hashed artifact."""
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/paperA/w9_release_fix'
PASSWORD = Path('/root/.local/share/webcraft-review/w9_password')


def main():
    for scope in ('main', 'ext_v2', 'cds'):
        result = json.loads((OUT / f'replay/{scope}_result.json').read_text())
        if result['compared'] != result['expected']:
            raise RuntimeError(f'incomplete replay: {scope}')
    files = []
    for name in ('replay', 'o_gate', 'packaging_trial_missing_oracle_dependency'):
        for path in (OUT / name).rglob('*'):
            if path.is_file() and (path.suffix == '.jsonl' or 'runtime' in path.relative_to(OUT).parts):
                files.append(path)
    inventory = [{'path': str(path.relative_to(OUT)), 'bytes': path.stat().st_size,
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in sorted(files)]
    with tempfile.TemporaryDirectory(prefix='w9_forensic_') as temporary:
        archive = Path(temporary) / 'trajectories.tar.gz'
        with tarfile.open(archive, 'w:gz') as target:
            for path in sorted(files):
                target.add(path, arcname=str(path.relative_to(OUT)), recursive=False)
        encrypted = OUT / 'replay_trajectories.tar.gz.enc'
        subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt',
                        '-in', str(archive), '-out', str(encrypted),
                        '-pass', f'file:{PASSWORD}'], check=True)
    (OUT / 'trajectory_archive.json').write_text(json.dumps({
        'encryption': 'openssl aes-256-cbc, PBKDF2; reviewer password supplied separately',
        'archive': encrypted.name, 'archive_bytes': encrypted.stat().st_size,
        'archive_sha256': hashlib.sha256(encrypted.read_bytes()).hexdigest(),
        'files': inventory,
    }, indent=2) + '\n')
    (OUT / '.gitignore').write_text(
        'replay/runtime/\nreplay/*_replayed.jsonl\no_gate/runtime/\n'
        'packaging_trial_missing_oracle_dependency/runtime/\n'
        'packaging_trial_missing_oracle_dependency/*_replayed.jsonl\n')
    print(json.dumps({'archived_files': len(files),
                      'plaintext_bytes': sum(row['bytes'] for row in inventory),
                      'encrypted_bytes': encrypted.stat().st_size}))


if __name__ == '__main__':
    main()
