#!/usr/bin/env python3
"""Bounded pure planning tests on captured states, with no API/world methods."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from w11_common import *


def main():
    resources('source_diagnostics')
    cases=json.loads((OUT/'captured_cases.json').read_text())
    output=OUT/'source_tests';output.mkdir(exist_ok=True)
    fixtures={row['frozen']['fixture_sha256']:row['frozen']['fixture'] for row in cases}
    results={};summary=[]
    for number,(digest,path) in enumerate(fixtures.items()):
        results[digest]=[]
        for repeat,seed in enumerate((0,1,2,3,0)):
            target=output/f'fixture_{number:02d}_run_{repeat}_seed_{seed}.json'
            env={**os.environ,'PYTHONHASHSEED':str(seed),'PYTHONDONTWRITEBYTECODE':'1',
                 'CUDA_VISIBLE_DEVICES':'','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}
            subprocess.run([sys.executable,str(ROOT/'scripts/w11_source_worker.py'),str(ROOT/path),str(target)],
                           env=env,check=True,timeout=600,capture_output=True,text=True)
            record=json.loads(target.read_text());results[digest].append(record)
            print(f'Completed captured fixture {number}, seed {seed}, repeat {repeat}',flush=True)
        first=[r['solves'][0] for r in results[digest]]
        assert len({r['matrix_without_row_order_sha256'] for r in first})==1, 'STOP: hash seed changed more than row order'
    for case in cases:
        runs=results[case['frozen']['fixture_sha256']]
        af=case['frozen']['selected'];bf=case['replayed']['selected']
        def select(r):
            plans=r['plans']
            # Naive/Verify-All take plan[0]; ARBITER's public decision is not
            # recomputed here. Export all first-step candidates for VOI cases.
            return [p['steps'][0]['mechanism_id'] for p in plans if p['steps']]
        summary.append({'case':case['case'],'key':list(case['key']),
                        'fixture_equal':case['exact_fixture_equal'],
                        'frozen_selected':af['mechanism_id'],'replay_selected':bf['mechanism_id'],
                        'seed_first_steps':[{ 'seed':r['python_hash_seed'],'first_steps':select(r),
                                             'first_objective':r['plans'][0]['objective'] if r['plans'] else None}
                                           for r in runs],
                        'row_orders':len({r['solves'][0]['row_order_sha256'] for r in runs}),
                        'same_matrix_ignoring_row_order':len({r['solves'][0]['matrix_without_row_order_sha256'] for r in runs})==1,
                        'same_seed_repeat_equal':canonical(runs[0]['plans'])==canonical(runs[-1]['plans'])})
    (OUT/'source_test_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'distinct_fixtures':len(fixtures),'cases':len(summary),'world_actions_executed':0}))


if __name__=='__main__':main()
