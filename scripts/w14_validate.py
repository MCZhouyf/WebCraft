#!/usr/bin/env python3
"""Persist test and public-library provenance without touching frozen assets."""
import json
import os
import subprocess
import sys
import w14_audit as a


def main():
    files=('agent/core.py','agent/planning.py','agent/aggregation.py','agent/decision.py',
           'evaluation/claim_compiler.py','normalization.py')
    provenance=[]
    for name in files:
        working=a.ROOT.parent/'arbiter/src/arbiter'/name
        release=a.ROOT/'release/package/method_code/src/arbiter'/name
        h1,h2=a.sha(working),a.sha(release)
        provenance.append(dict(source=name,workspace_sha256=h1,release_sha256=h2,identical=h1==h2))
        if h1!=h2:raise RuntimeError('shared public library differs from frozen release')
    a.write('shared_library_provenance.csv',provenance)
    proc=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-p','test_w1[34]*.py','-v'],
                        cwd=a.ROOT,env={**os.environ,'CUDA_VISIBLE_DEVICES':'','PYTHONDONTWRITEBYTECODE':'1'},
                        text=True,capture_output=True)
    (a.OUT/'TEST_RESULTS.txt').write_text(proc.stdout+proc.stderr)
    summary=json.loads((a.OUT/'SUMMARY.json').read_text())
    summary['tests_passed']=proc.returncode==0
    summary['shared_library_matches_release']=all(r['identical'] for r in provenance)
    (a.OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    a.write('OUTPUTS.sha256.csv',[dict(path=str(p.relative_to(a.OUT)),sha256=a.sha(p),bytes=p.stat().st_size)
        for p in sorted(a.OUT.rglob('*')) if p.is_file() and p.name!='OUTPUTS.sha256.csv' and 'solver_tmp' not in p.parts])
    print(proc.stdout+proc.stderr)
    raise SystemExit(proc.returncode)


if __name__=='__main__':main()
