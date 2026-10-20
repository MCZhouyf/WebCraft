#!/usr/bin/env python3
"""Install release-only adapters/tests and refresh package manifests."""
import csv
import hashlib
import json
import shutil
import gzip
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARB=ROOT.parent/'arbiter'
PACKAGE=ROOT/'release/package'
OUT=ROOT/'reports/paperA/w9_release_fix'
PASSWORD=Path('/root/.local/share/webcraft-review/w9_password')


def copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)


def main():
    for filename in ('requirements.lock','ENVIRONMENT.lock'):
        path=PACKAGE/filename
        body=path.read_text()
        if 'cloudpickle==' not in body:path.write_text(body+'cloudpickle==3.1.2\n')
    (PACKAGE/'.gitignore').write_text('.venv/\nruns/\n__pycache__/\n.pytest_cache/\n*.pyc\nassets/restricted/\nassets/frozen/main.jsonl.gz\nassets/frozen/cp_rag.jsonl.gz\ncorpus/indexes/formal_p1/polluted/\ncorpus/claims_cache/formal_p1/polluted/\n')
    copy(ROOT/'scripts/w9_runtime.py',PACKAGE/'repro/runtime.py')
    copy(ROOT/'scripts/w9_package_readme.md',PACKAGE/'README.md')
    copy(ROOT/'scripts/w9_cp_entry.py',PACKAGE/'repro/run_cp.py')
    copy(ROOT/'reports/paperA/review_v14_zero_call/cp_rag/dev_freeze.json',
         PACKAGE/'provenance/cp_dev_freeze.json')
    if not (PACKAGE/'assets/frozen/cp_rag.jsonl.gz').exists():
        with (ROOT/'reports/paperA/review_v14_zero_call/cp_rag/episodes.jsonl').open('rb') as source:
            with (PACKAGE/'assets/frozen/cp_rag.jsonl.gz').open('wb') as target:
                with gzip.GzipFile(fileobj=target,mode='wb',filename='',mtime=0) as zipped:
                    shutil.copyfileobj(source,zipped)
    copy(ROOT/'scripts/w9_reproduce_main.sh',PACKAGE/'reproduce_main.sh')
    (PACKAGE/'reproduce_main.sh').chmod(0o755)
    (PACKAGE/'repro/test_package.py').write_text(
        'from pathlib import Path\nimport sys\n'
        'root=Path(__file__).resolve().parents[1]\n'
        'sys.path[:0]=[str(root/"method_code/src"),str(root)]\n'
        'import pytest\n'
        'raise SystemExit(pytest.main(["-q",str(root/"tests/methods"),str(root/"tests/sim"),str(root/"metrics/test_episode.py")]))\n')
    (PACKAGE/'tests/methods/test_release_oracle.py').write_text(
        'import json\nfrom pathlib import Path\nfrom collections import Counter\n'
        'from arbiter.evaluation.oracle_baseline import oracle_aggregation\n'
        'def test_oracle_normalizer_is_bundled():\n'
        '    root=Path(__file__).resolve().parents[2]\n'
        '    standard=json.loads((root/"config/resource_model.json").read_text())["standard_inventory"]\n'
        '    operators,belief=oracle_aggregation("1.16.5",Counter({r["name"]:r["count"] for r in standard}),root)\n'
        '    assert operators and belief is not None\n')
    copy(ROOT/'scripts/w9_public_observation_scoring.py',PACKAGE/'repro/public_observation_scoring.py')
    copy(ROOT/'scripts/w9_test_public_observations.py',PACKAGE/'tests/methods/test_public_observations.py')
    names=('run_measurement_v3_20261014.py','run_measurement_v3d_20261015.py',
           'run_measurement_v3_followup_20261015.py','run_measurement_reanalysis_20261013.py',
           'run_main.py')
    for name in names:
        copy(ARB/'scripts'/name,PACKAGE/'scripts'/name)
    copy(ROOT/'scripts/conflict_core.py',PACKAGE/'scripts/conflict_core.py')
    copy(ARB/'config/method_matrix.json',PACKAGE/'tests/config/method_matrix.json')
    (PACKAGE/'tests/sim/__init__.py').unlink(missing_ok=True)
    for source in (ROOT/'corpus/indexes/formal',):
        for path in source.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                copy(path,PACKAGE/path.relative_to(ROOT))
    for source in (ROOT/'corpus/claims_cache').iterdir():
        if not source.is_dir() or source.name in {'formal_p1','__pycache__'}:continue
        for path in source.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                copy(path,PACKAGE/path.relative_to(ROOT))
    for name in ('run_cp_rag_20261016.py','run_ext_v2_cds_20261016.py'):
        source=(ROOT/'scripts'/name).read_text()
        source=source.replace('Path("/root/autodl-tmp/arbiter")','ROOT / "method_code"')
        source=source.replace('PRIOR_PATH = ARBITER / "artifacts/prior/frozen_prior_bundle.json"',
                              'PRIOR_PATH = ROOT / "assets/prior/frozen_prior_bundle.json"')
        (PACKAGE/'scripts'/name).write_text(source)
    for source in (ROOT/'reports/paperA/review_supplements').glob('*.py'):
        target=PACKAGE/'reports/paperA/review_supplements'/source.name
        copy(source,target)
        body=target.read_text().replace('Path("/root/autodl-tmp/arbiter")','ROOT / "method_code"')
        target.write_text(body)
    external_target=PACKAGE/'assets/frozen/external_first_run_with_actions.jsonl.gz'
    if not external_target.exists():
        folders={'direct_llm':'direct_llm_assembled','react':'react_assembled',
                 'reflexion':'reflexion_assembled','adam':'adam','xenon':'xenon_assembled'}
        with external_target.open('wb') as target:
            with gzip.GzipFile(fileobj=target,mode='wb',filename='',mtime=0) as zipped:
                for method,folder in sorted(folders.items()):
                    source=ARB/f'artifacts/post_review/external_agents/final/{folder}/episodes.jsonl'
                    for line in source.read_text().splitlines():
                        row=json.loads(line)
                        actions=ARB/row['action_log']
                        payload={'record':row,'events':[json.loads(l) for l in actions.read_text().splitlines() if l],
                                 'action_log_sha256':hashlib.sha256(actions.read_bytes()).hexdigest()}
                        zipped.write((json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode())
    copy(ROOT/'scripts/run_cp_rag_evaluation_adapter_20261016.py',PACKAGE/'scripts/run_cp_rag_evaluation_adapter_20261016.py')
    copy(PACKAGE/'assets/prior/frozen_prior_bundle.json',PACKAGE/'method_code/artifacts/prior/frozen_prior_bundle.json')
    restricted_manifest=PACKAGE/'docs/RESTRICTED_FILES.json'
    restricted_names={entry['path'] for entry in json.loads(restricted_manifest.read_text())}
    # Full reference trajectories include polluted search text as well as actions.
    restricted_names.update({'assets/frozen/main.jsonl.gz','assets/frozen/cp_rag.jsonl.gz'})
    restricted_entries=[]
    with tempfile.TemporaryDirectory(prefix='w9_restricted_finalize_') as temporary:
        archive=Path(temporary)/'restricted.tar.gz'
        with tarfile.open(archive,'w:gz') as target:
            for name in sorted(restricted_names):
                path=PACKAGE/name
                target.add(path,arcname=name,recursive=False)
                restricted_entries.append({'path':name,'bytes':path.stat().st_size,
                                           'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        subprocess.run(['openssl','enc','-aes-256-cbc','-pbkdf2','-salt','-in',str(archive),
                        '-out',str(PACKAGE/'corpus/encrypted/w9_replay_restricted.tar.gz.enc'),
                        '-pass',f'file:{PASSWORD}'],check=True)
    restricted_manifest.write_text(json.dumps(restricted_entries,indent=2)+'\n')
    (PACKAGE/'docs/REDISTRIBUTION_NOTES.md').write_text(
        '# Redistribution and access notes\n\n'
        'No full original Web-page snapshots or original synthetic pages are included. '
        'Public retrieval documents are frozen snippets with source attribution and applicable '
        'CC BY-SA / CC BY-NC-SA provenance in corpus/source_attribution.csv. The existing '
        'release/LICENSE remains an author-supplied placeholder, not a newly granted license. '
        'Authors must resolve project-code licensing and comply with source attribution before public publication.\n\n'
        'The polluted-condition index, derived claim cache, retrieved evidence and complete '
        'main/CP reference trajectories containing polluted search text are included '
        'only in an encrypted reviewer artifact. The author supplies its password through the '
        'submission system; absent that password, polluted instances cannot be replayed. '
        'docs/RESTRICTED_FILES.json gives exact decrypted-file hashes and byte sizes.\n\n'
        'Model weights, external service endpoints, API keys, SSH credentials, full Wiki '
        'archives and original author private directories are excluded. External-model '
        'experiments are audited from scored exports; reissuing those calls is outside '
        'the zero-call replay. Raw reconstruction is documented in docs/REPRODUCIBILITY.md.\n')
    entries=[];inventory=[]
    for path in sorted(PACKAGE.rglob('*')):
        if not path.is_file() or {'__pycache__','.pytest_cache','.venv','runs'}.intersection(path.relative_to(PACKAGE).parts):continue
        if path.is_relative_to(PACKAGE/'assets/restricted'):continue
        if str(path.relative_to(PACKAGE)) in {'assets/frozen/main.jsonl.gz','assets/frozen/cp_rag.jsonl.gz'}:continue
        # Reviewer-decrypted restricted trees remain local and untracked.
        if path.is_relative_to(PACKAGE/'corpus/indexes/formal_p1/polluted') or path.is_relative_to(PACKAGE/'corpus/claims_cache/formal_p1/polluted'):continue
        if path.name=='MANIFEST.sha256':continue
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f'{digest}  {path.relative_to(PACKAGE)}')
        inventory.append({'path':str(path.relative_to(PACKAGE)),'bytes':path.stat().st_size,'sha256':digest})
    (PACKAGE/'MANIFEST.sha256').write_text('\n'.join(entries)+'\n')
    with (OUT/'final_package_inventory.csv').open('w',newline='') as handle:
        w=csv.DictWriter(handle,fieldnames=list(inventory[0]));w.writeheader();w.writerows(inventory)
    (OUT/'package_size.json').write_text(json.dumps({'files':len(inventory)+1,
        'bytes':sum(r['bytes'] for r in inventory)+(PACKAGE/'MANIFEST.sha256').stat().st_size,
        'manifest_sha256':hashlib.sha256((PACKAGE/'MANIFEST.sha256').read_bytes()).hexdigest()},indent=2)+'\n')
    outer=ROOT/'release/MANIFEST.sha256'
    names={line.split('  ',1)[1] for line in outer.read_text().splitlines()
           if line and not line.split('  ',1)[1].startswith('release/package/')}
    names.update('release/package/'+entry['path'] for entry in inventory)
    names.add('release/package/MANIFEST.sha256')
    outer.write_text(''.join(f'{hashlib.sha256((ROOT/name).read_bytes()).hexdigest()}  {name}\n'
                             for name in sorted(names)))


if __name__=='__main__':main()
