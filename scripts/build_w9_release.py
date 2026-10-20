#!/usr/bin/env python3
"""Complete the review release with portable sources and frozen replay inputs."""
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARB = ROOT.parent / 'arbiter'
PACKAGE = ROOT / 'release/package'
REPORT = ROOT / 'reports/paperA/w9_release_fix'
SECRET = Path('/root/.local/share/webcraft-review/w9_password')
SKIP = {'__pycache__', '.pytest_cache', '.git'}


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def tree(source, target, suffixes=None):
    for path in sorted(source.rglob('*')):
        if not path.is_file() or SKIP.intersection(path.parts) or path.suffix == '.pyc':
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        copy(path, target / path.relative_to(source))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def gz_records(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as zipped:
            for record in records:
                zipped.write((json.dumps(record, sort_keys=True, separators=(',', ':'))+'\n').encode())


def manifest():
    entries = []
    for path in sorted(PACKAGE.rglob('*')):
        if (path.is_file() and not SKIP.intersection(path.parts) and path.suffix != '.pyc'
                and path.name != 'MANIFEST.sha256' and not path.is_relative_to(PACKAGE/'runs')
                and not path.is_relative_to(PACKAGE/'assets/restricted')):
            entries.append(f'{sha(path)}  {path.relative_to(PACKAGE)}')
    (PACKAGE/'MANIFEST.sha256').write_text('\n'.join(entries)+'\n')


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    before = {str(p.relative_to(PACKAGE)): (sha(p), p.stat().st_size)
              for p in PACKAGE.rglob('*') if p.is_file() and not SKIP.intersection(p.parts)}
    for directory in SKIP - {'.git'}:
        for p in PACKAGE.rglob(directory):
            if p.is_dir():
                shutil.rmtree(p)
    tree(ARB/'src', PACKAGE/'method_code/src', {'.py'})
    tree(ARB/'config', PACKAGE/'method_code/config', {'.json'})
    copy(ARB/'pyproject.toml', PACKAGE/'method_code/pyproject.toml')
    tree(ROOT/'sim', PACKAGE/'sim', {'.py', '.md'})
    tree(ROOT/'oracle', PACKAGE/'oracle', {'.py'})
    tree(ROOT/'metrics', PACKAGE/'metrics', {'.py'})
    tree(ROOT/'tests/sim', PACKAGE/'tests/sim', {'.py'})
    for p in (PACKAGE/'sim').glob('*.py'):
        body = p.read_text().replace("ROOT = Path('/root/autodl-tmp/webcraft')", 'ROOT = Path(__file__).resolve().parents[1]')
        p.write_text(body)
    for p in (PACKAGE/'oracle').glob('*.py'):
        p.write_text(p.read_text().replace("ROOT = Path('/root/autodl-tmp/webcraft')", 'ROOT = Path(__file__).resolve().parents[1]'))
    for p in (PACKAGE/'tests/sim').glob('*.py'):
        p.write_text(p.read_text().replace("ROOT = Path('/root/autodl-tmp/webcraft')", 'ROOT = Path(__file__).resolve().parents[2]'))
    for name in ('gold_claims.py', 'normalize_claims_cache.py', 'cache_extractor_counts.py',
                 'CLAIM_PAYLOAD_SCHEMA.md', 'claims_cache.py', 'structured_claims_cache.py', 'search_backend.py'):
        copy(ROOT/'corpus'/name, PACKAGE/'corpus'/name)
    for name in ('tasks.jsonl', 'conflicts.jsonl', 'stale_agent_expectations.jsonl'):
        copy(ROOT/'data'/name, PACKAGE/'data'/name)
    tree(ROOT/'data/ext_v2', PACKAGE/'data/ext_v2')
    tree(ROOT/'config/ext_v2', PACKAGE/'config/ext_v2')
    for p in (ROOT/'config').glob('*.json'):
        copy(p, PACKAGE/'config'/p.name)
    copy(ROOT/'harness/config/arena.json', PACKAGE/'harness/config/arena.json')
    tree(ROOT/'truth', PACKAGE/'truth', {'.json'})
    tree(ROOT/'cds_materials', PACKAGE/'cds_materials', {'.json'})
    tree(ROOT/'reports/paperA/ext_v2/cds_evaluator', PACKAGE/'evaluator/cds', {'.json'})
    copy(ARB/'artifacts/prior/frozen_prior_bundle.json', PACKAGE/'assets/prior/frozen_prior_bundle.json')
    tree(ROOT/'prereg', PACKAGE/'prereg', {'.md', '.json', '.csv'})

    # Keep appendix implementations and their exact source snapshots reviewable.
    scripts = ['run_cp_rag_20261016.py', 'run_cp_rag_evaluation_adapter_20261016.py',
               'run_ext_v2_cds_20261016.py', 'run_review_v14_zero_call_20261016.py',
               'run_w8_closeout_20261016.py']
    for name in scripts:
        copy(ROOT/'scripts'/name, PACKAGE/'provenance/scripts'/name)
    for name in ('run_main.py', 'run_measurement_v3_20261014.py', 'run_measurement_v3d_20261015.py'):
        copy(ARB/'scripts'/name, PACKAGE/'provenance/arbiter_scripts'/name)
    table_root = PACKAGE/'reports/paperA'
    # CSVs are sufficient inputs for the single paper-table generator.
    for p in (ROOT/'reports/paperA').rglob('*'):
        if p.is_file() and not SKIP.intersection(p.parts) and p.suffix == '.csv' and 'w9_release_fix' not in p.parts:
            copy(p, table_root/p.relative_to(ROOT/'reports/paperA'))
    for p in (ROOT/'reports/paperA').glob('build*.py'):
        copy(p, table_root/p.name)
    # Include original HTML/Markdown table exports alongside their generating CSVs.
    tree(ROOT/'reports/paperA/paper_tables_v13', table_root/'paper_tables_v13', {'.html', '.md'})

    for name in ('test_measurement_v3.py', 'test_measurement_v3d.py', 'test_consequence_htr.py',
                 'test_post_review_measurement.py', 'test_aggregation.py', 'test_planning.py',
                 'test_final_execution.py', 'test_context_mixture.py'):
        copy(ARB/'tests'/name, PACKAGE/'tests/methods'/name)
    copy(ROOT/'scripts/w9_runtime.py', PACKAGE/'repro/runtime.py')

    with tempfile.TemporaryDirectory(prefix='w9_restricted_') as temporary:
        restricted = Path(temporary)
        for condition in sorted((ROOT/'corpus/indexes/formal_p1').iterdir()):
            if not condition.is_dir():
                continue
            target = restricted if condition.name == 'polluted' else PACKAGE
            tree(condition, target/'corpus/indexes/formal_p1'/condition.name)
            tree(ROOT/'corpus/claims_cache/formal_p1'/condition.name,
                 target/'corpus/claims_cache/formal_p1'/condition.name)
        datasets = {}
        evidence = {'main': [], 'ext_v2': [], 'cds': []}
        for mode in ('llm_only', 'all_public', 'oracle_reading'):
            if mode == 'oracle_reading':
                records = jsonl(ARB/'artifacts/post_review/oracle_reading/episodes.jsonl')
                evidir = ARB/'artifacts/post_review/oracle_reading/evidence'
            else:
                records = jsonl(ARB/f'artifacts/main/{mode}/episodes.jsonl') + jsonl(ARB/f'artifacts/post_run_probe_then_rag/{mode}/episodes.jsonl')
                records = [r for r in records if int(r['seed']) == 20261006]
                evidir = ARB/f'artifacts/main/{mode}/evidence'
            unique = {(r['condition'], r['world'], r['task_id'], r['method']): r for r in records}
            if len(unique) != 3740:
                raise RuntimeError(f'main count {mode}: {len(unique)}')
            datasets.setdefault('main', []).extend({**r, 'reading_mode': mode} for _, r in sorted(unique.items()))
            keys = sorted({(r['condition'], r['task_id']) for r in records})
            for condition, task in keys:
                path = evidir/(f'{condition}__{task}.json' if mode == 'oracle_reading'
                                else f'{condition}__{task}__20261006.json')
                bundle = json.loads(path.read_text())
                evidence['main'].append({'mode': mode, 'condition': condition, 'task_id': task, 'bundle': bundle})
        for scope, source in (('ext_v2', 'web'), ('cds', 'cds')):
            records = jsonl(ROOT/f'reports/paperA/ext_v2/{source}_run/episodes.jsonl')
            datasets[scope] = records
            if scope == 'ext_v2':
                keys = sorted({(r['condition'], r['task_id']) for r in records})
                for condition, task in keys:
                    path = ROOT/f'reports/paperA/ext_v2/web_run/evidence/{condition}__{task}__20261016.json'
                    evidence[scope].append({'mode': 'all_public', 'condition': condition, 'task_id': task,
                                            'bundle': json.loads(path.read_text())})
            else:
                import sys
                sys.path[:0] = [str(ROOT/'scripts'), str(ROOT), str(ARB/'src')]
                import run_ext_v2_cds_20261016 as w7a
                materials = json.loads((ROOT/'cds_materials/cds_manifest.json').read_text())
                for item in materials['rows']:
                    private = json.loads((ROOT/item['evaluator_path']).read_text())
                    visible = json.loads((ROOT/item['visible_path']).read_text())
                    for condition in ('K', 'K+S', 'S', 'empty', 'K+N'):
                        evidence[scope].append({'mode': 'cds', 'condition': private['instance_id']+'__'+condition,
                                                'task_id': private['task_id'],
                                                'bundle': w7a.cds_bundle(private, visible, condition)})
        for scope, records in datasets.items():
            gz_records(PACKAGE/f'assets/frozen/{scope}.jsonl.gz', records)
            public = [r for r in evidence[scope] if r['condition'] != 'polluted']
            private = [r for r in evidence[scope] if r['condition'] == 'polluted']
            gz_records(PACKAGE/f'assets/evidence/{scope}.jsonl.gz', public)
            if private:
                gz_records(restricted/f'assets/restricted/evidence/{scope}.jsonl.gz', private)
        SECRET.parent.mkdir(parents=True, exist_ok=True)
        if not SECRET.exists():
            descriptor = os.open(SECRET, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                handle.write(secrets.token_urlsafe(40)+'\n')
        archive = restricted/'restricted.tar.gz'
        files = [p for p in restricted.rglob('*') if p.is_file() and p != archive]
        with tarfile.open(archive, 'w:gz') as tar:
            for path in files:
                tar.add(path, arcname=str(path.relative_to(restricted)))
        encrypted = PACKAGE/'corpus/encrypted/w9_replay_restricted.tar.gz.enc'
        subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-in', str(archive),
                        '-out', str(encrypted), '-pass', f'file:{SECRET}'], check=True)
        (PACKAGE/'docs/RESTRICTED_FILES.json').write_text(json.dumps([
            {'path':str(p.relative_to(restricted)), 'bytes':p.stat().st_size, 'sha256':sha(p)}
            for p in sorted(files)], indent=2)+'\n')

    packages = ['numpy','scipy','scikit-learn','pulp','pytest','pytest-subtests','joblib','cloudpickle',
                'threadpoolctl','iniconfig','packaging','pluggy','pygments','exceptiongroup',
                'tomli','typing_extensions','matplotlib','pandas','contourpy','cycler','fonttools',
                'kiwisolver','pillow','pyparsing','python-dateutil','six','pytz','tzdata']
    locked = []
    for name in packages:
        try:
            locked.append(f'{name}=={importlib.metadata.version(name)}')
        except importlib.metadata.PackageNotFoundError:
            pass
    (PACKAGE/'requirements.lock').write_text('\n'.join(locked)+'\n')
    (PACKAGE/'ENVIRONMENT.lock').write_text('python==3.10.8\nPYTHONHASHSEED=0\nCBC_threads=1\nbackend=deterministic-cpu\n'+ '\n'.join(locked)+'\n')
    copy(ROOT/'scripts/w9_reproduce_main.sh', PACKAGE/'reproduce_main.sh')
    (PACKAGE/'reproduce_main.sh').chmod(0o755)
    copy(ROOT/'scripts/w9_package_readme.md', PACKAGE/'README.md')
    # Preserve the locked method/protocol source as a separate, auditable artifact.
    archive = subprocess.run(['git', 'archive', 'ff3bf4a', 'src', 'config', 'pyproject.toml'],
                             cwd=ARB, capture_output=True, check=True).stdout
    (PACKAGE/'provenance/ff3bf4a.tar').write_bytes(archive)
    (PACKAGE/'provenance/SOURCE_COMMITS.json').write_text(json.dumps({
        'method_protocol': subprocess.check_output(['git','rev-parse','ff3bf4a'],cwd=ARB,text=True).strip(),
        'packaged_method_snapshot': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ARB,text=True).strip(),
        'webcraft': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'prior_sha256': sha(PACKAGE/'assets/prior/frozen_prior_bundle.json'),
    },indent=2)+'\n')
    manifest()
    inventory = []
    for path in sorted(PACKAGE.rglob('*')):
        if not path.is_file() or SKIP.intersection(path.parts):
            continue
        name = str(path.relative_to(PACKAGE))
        old = before.get(name)
        inventory.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path),
                          'change': 'added' if old is None else ('updated' if old[0] != sha(path) else 'retained')})
    with (REPORT/'package_inventory.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(inventory[0]));writer.writeheader();writer.writerows(inventory)
    print(json.dumps({'files': len(inventory), 'bytes': sum(r['bytes'] for r in inventory),
                      'added': sum(r['change']=='added' for r in inventory)}))


if __name__ == '__main__':
    main()
