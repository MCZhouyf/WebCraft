"""Build a local replay package, excluding WebCraft source and all credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.destination.exists():
        raise RuntimeError('refusing to overwrite an existing package')
    manifest = json.loads((ROOT / 'artifacts/post_review/a22_pf_rag/evaluation/manifest.json').read_text())
    files = {Path(p) for p in manifest['source_hashes'] if Path(p).is_relative_to(ROOT)}
    files.update((ROOT / 'src').rglob('*.py'))
    files.update(ROOT / 'scripts' / name for name in [
        'run_a22_evaluation.py', 'run_a22_development.py', 'preflight_a22_pf_rag.py',
        'run_main.py', 'analyze_a22_pf_rag.py', 'run_measurement_v3_20261014.py'])
    files.update(ROOT / 'config' / name for name in [
        'a22_pf_rag_frozen.json', 'arbiter_hparams.json', 'p6_development_tasks.json'])
    files.add(ROOT / 'artifacts/post_review/a22_pf_rag/development/grid_fixedhash_20261020/manifest.json')
    files.update((ROOT / 'prereg').glob('a22*.md'))
    files.update(ROOT / name for name in [
        'artifacts/main/all_public/episodes.jsonl', 'artifacts/main/llm_only/episodes.jsonl',
        'artifacts/post_review/oracle_reading/episodes.jsonl', 'pyproject.toml'])
    files.update((ROOT / 'tests').glob('test_a22*.py'))
    forbidden = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}')
    hashes = {}
    for source in sorted(files):
        data = source.read_bytes()
        if forbidden.search(data):
            raise RuntimeError('credential-like content blocked from package; no content printed')
        relative = source.relative_to(ROOT)
        target = args.destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    (args.destination / 'PACKAGE_MANIFEST.json').write_text(json.dumps({
        'files': hashes, 'external_dependency': '/root/autodl-tmp/webcraft',
        'webcraft_commit': manifest['webcraft_commit'],
        'webcraft_is_read_only_and_not_copied': True,
        'policy_freeze_commit': manifest['freeze_commit'],
        'secrets_scan': 'PASS', 'llm_calls': 0}, indent=2, sort_keys=True) + '\n')
    (args.destination / 'README.md').write_text(
        '# A22 local replay package\n\n'
        'Requires the pinned read-only WebCraft checkout at /root/autodl-tmp/webcraft '
        'and the existing Python dependencies. WebCraft is intentionally not copied.\n'
        'From this extracted package directory run:\n\n'
        '```bash\n'
        'env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.:scripts OPENBLAS_NUM_THREADS=1 '
        'OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_a22_evaluation.py --workers 4\n'
        'env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.:scripts OPENBLAS_NUM_THREADS=1 '
        'OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 nice -n 15 python scripts/analyze_a22_pf_rag.py\n'
        '```\n\n'
        'The runner verifies frozen policy and input hashes, locks server.lock, and '
        'executes both registered seeds. Outputs are new files inside this package. '
        'The seeds are reproducibility replicas, not additional statistical samples. '
        'Initial retrieval is replayed from the frozen shared-reader evidence. '
        'No network inference, new extraction, GPU, or model credentials are required.\n')
    archive = args.destination.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz', compresslevel=6) as handle:
        handle.add(args.destination, arcname='a22_replay', recursive=True)
    print(json.dumps({'archive': str(archive), 'files': len(files),
                      'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                      'bytes': archive.stat().st_size}))


if __name__ == '__main__':
    main()
