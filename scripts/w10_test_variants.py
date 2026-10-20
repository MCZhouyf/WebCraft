#!/usr/bin/env python3
"""Install and test each anonymous form without running the experimental matrix."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from w10_release import ROOT, OUT, ONLINE, OFFLINE, PASSWORD, replace_text


def command(args,*,cwd,env,log):
    result=subprocess.run(args,cwd=cwd,env=env,text=True,capture_output=True)
    log.write_text(replace_text(result.stdout+result.stderr))
    return result.returncode,result.stdout+result.stderr


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,CUDA_VISIBLE_DEVICES='',PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',
             PYTHONNOUSERSITE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    env.pop('PYTHONPATH',None)
    summaries=[]
    for label,source in (('online',ONLINE),('offline',OFFLINE)):
        started=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='w10_'+label+'_',dir=ROOT.parent) as tmp:
            tmp=Path(tmp);target=tmp/'anonymous package';venv=tmp/'env'
            shutil.copytree(source,target)
            rc,_=command([sys.executable,'-m','venv',str(venv)],cwd=tmp,env=env,log=OUT/f'{label}_venv.log')
            if rc:raise RuntimeError(label+' venv creation failed')
            python=venv/'bin/python'
            pip=[str(python),'-m','pip','--isolated','install','--no-cache-dir']
            if label=='offline':pip+=['--no-index','--find-links',str(target/'wheelhouse')]
            else:pip+=['--index-url','https://pypi.org/simple']
            pip+=['-r',str(target/'requirements.lock')]
            rc,_=command(pip,cwd=target,env=env,log=OUT/f'{label}_dependency_install.log')
            install='public PyPI using exact lock' if label=='online' else 'bundled wheelhouse, no index'
            if rc:
                # No method/data adjustment: report failed network resolution separately.
                if label!='online':raise RuntimeError('offline dependency installation failed')
                rc,_=command([str(python),'-m','pip','--isolated','install','--no-index','--find-links',
                              str(OFFLINE/'wheelhouse'),'-r',str(target/'requirements.lock')],
                             cwd=target,env=env,log=OUT/'online_dependency_fallback.log')
                install='public PyPI attempt failed; test dependencies installed from separate offline form'
                if rc:raise RuntimeError('online test dependency fallback also failed')
            testenv=dict(env,WC_REVIEW_PASSWORD_FILE=str(PASSWORD),WC_REPLAY_VENV=str(venv),
                         WC_REPLAY_TEST_ONLY='1')
            rc,console=command(['bash','./reproduce_main.sh'],cwd=target,env=testenv,log=OUT/f'{label}_tests.log')
            if rc:
                (OUT/f'{label}_TEST_FAILED.json').write_text(json.dumps({'returncode':rc,'install':install})+'\n')
                raise RuntimeError(label+' package tests failed; see saved log')
            code='from pathlib import Path; import sys; sys.path.insert(0,str(Path.cwd()/"repro")); import runtime; runtime.setup(); runtime.check_imports(); print("MANIFEST_RESTRICTED_IMPORTS_OK")'
            rc,verification=command([str(python),'-I','-c',code],cwd=target,env=env,log=OUT/f'{label}_integrity.log')
            if rc or 'MANIFEST_RESTRICTED_IMPORTS_OK' not in verification:
                raise RuntimeError(label+' integrity or isolated imports failed')
            match=re.search(r'(\d+) passed, (\d+) subtests passed',console)
            assert match
            row={'variant':label,'tests_passed':int(match[1]),'subtests_passed':int(match[2]),
                 'dependency_install':install,'integrity_restricted_and_imports':True,
                 'no_main_ext_or_cds_matrix_run':True,'zero_llm_calls':True,
                 'elapsed_s':round(time.monotonic()-started,3),'path_with_space':True}
            summaries.append(row)
            (OUT/'variant_tests.json').write_text(json.dumps(summaries,indent=2)+'\n')
            print(json.dumps(row),flush=True)


if __name__=='__main__':main()
