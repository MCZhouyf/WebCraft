#!/usr/bin/env python3
"""W14 evaluator: public-only workers, dev-only calibration, committed freeze gate."""
from __future__ import annotations
import argparse
import copy
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT)]
import w13_run as w
import w14_audit as a
OUT=a.OUT
w.OUT=OUT
CANDIDATES=((.80,3),(.70,2),(.60,1.5),(.50,1.1),(0,1.000000001))
SOURCES=('scripts/w14_run.py','scripts/w14_runtime.py','scripts/w13_runtime.py',
         'scripts/w13_run.py','scripts/run_cp_rag_20261016.py')


class Worker(w.Worker):
    def __init__(self,phase,pin,kind,threshold=.8,odds=3):
        self.pin=pin;self.kind=kind;self.backends={};self.caches={};self.research_details=[]
        self.tmp=OUT/'solver_tmp';self.tmp.mkdir(exist_ok=True)
        env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in
             ('KEY','TOKEN','SECRET','PASSWORD','ENDPOINT','BASE_URL'))}
        env.update(TMPDIR=str(self.tmp),CUDA_VISIBLE_DEVICES='',PYTHONHASHSEED='0',
                   OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        self.stderr=(OUT/f'worker_{phase}_{kind}.stderr').open('w')
        self.proc=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/w14_runtime.py'),
                                    kind,str(threshold),str(odds)],stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,stderr=self.stderr,text=True,env=env,bufsize=1)

    def research(self,subject,goal,mode,condition,bundle):
        if mode not in ('all_public','llm_only') or condition.startswith('cds_'):
            return {'search_rows':[],'claims':[]},False
        k=(mode,condition)
        if k not in self.backends:
            self.backends[k]=w.p6.load_search_backend(condition,str(ROOT/'corpus/indexes/formal_p1'/condition),
                                                     root=ROOT,pin_path=self.pin)
            self.caches[k]=w.p6.load_claims_cache(str(ROOT/'corpus/indexes/formal_p1'/condition),
                                                root=ROOT,pin_path=self.pin,reading_mode=mode)
        backend=self.backends[k]
        limit=len(backend._delegate.documents) if self.kind=='cp_rag_recall_upper' else 10
        hits=backend.search(f'{subject} {goal}',limit)
        claims=[c for h in hits for c in self.caches[k].get(str(h['chunk_id']))]
        self.research_details.append(dict(query=subject+' '+goal,subject=subject,reading_mode=mode,
            condition=condition,returned_chunks=len(hits),claims=len(claims),limit=limit,
            chunk_ids=[h['chunk_id'] for h in hits]))
        mini=w.public_bundle(dict(search_rows=hits,claims=claims,mineable_nodes=bundle.get('mineable_nodes',[])))
        return mini,True

    def run(self,task,bundle,world,mode,condition,phase,episode_id):
        start=len(self.research_details)
        rec=super().run(task,copy.deepcopy(bundle),world,mode,condition,phase,episode_id)
        rec['method']=self.kind
        rec['research_details']=self.research_details[start:]
        rec['temporal_belief']['formed_ever']=any(t.get('formed_year') is not None for t in rec['policy_trace'])
        return rec


def dev_bundles(pin):
    tasks=[r for r in w.jsonl(ROOT/'data/tasks.jsonl') if r['split']=='dev']
    if len(tasks)!=8:raise RuntimeError('SAFETY_STOP: dev8 mismatch')
    cells=[('matched_1.16.5','1.16.5'),('matched_1.19','1.19'),('matched_1.21.11','1.21.11'),
           ('mismatched_old2new','1.21.11'),('mismatched_new2old','1.16.5'),
           ('mixed','1.16.5'),('mixed','1.19'),('mixed','1.21.11')]
    out=[]
    for condition,world in cells:
        cache=w.p6.load_claims_cache(str(ROOT/'corpus/indexes/formal_p1'/condition),root=ROOT,
                                    pin_path=pin,reading_mode='all_public')
        for task in tasks:
            b=w.p6.build_evidence(task,condition,index_dir=str(ROOT/'corpus/indexes/formal_p1'),
                 root=ROOT,pin_path=pin,claims_cache=cache,fallback=None,
                 fallback_dir=OUT/'forbidden_fallback',top_k=10)
            out.append((task,b,world,'all_public',condition))
    return out


def develop(pin):
    # No held-out log/evidence is loaded in this phase.
    dd=dev_bundles(pin)
    import run_cp_rag_20261016 as cp
    normalizer,_=w.p6._compiler(ROOT)
    gate=[]
    worker=Worker('dev_gate',pin,'cp_standard')
    try:
        for n,(t,b,world,mode,c) in enumerate(dd[:40]):
            fresh=worker.run(t,b,world,mode,c,'dev_gate_v2',f'isolated_{n:03}')
            original=cp.run_cp(cp.P6Job(c,t['id'],world,'cp_rag',cp.SEED),t,b,pin=pin,prior={},
                vectors={},output=OUT/'dev_reference')
            gate.append(dict(task_id=t['id'],condition=c,world=world,
                public_actions_equal=int(w.actions(fresh)==w.actions(original)),
                mechanisms_equal=int(fresh['used_mechanism_ids']==original['used_mechanism_ids']),
                SR_equal=int(fresh['metrics']['SR']==original['metrics']['SR'])))
            if (n+1)%8==0:print(f'CP dev gate {n+1}/40',flush=True)
    finally:worker.close()
    a.write('dev/cp_equivalence.csv',gate)
    cp_eligible=all(r['public_actions_equal'] and r['mechanisms_equal'] and r['SR_equal'] for r in gate)
    calibration=[];selection=None
    for index,(threshold,odds) in enumerate(CANDIDATES):
        worker=Worker(f'dev_cal_{index}',pin,'vb_rag_cal',threshold,odds)
        records=[]
        try:
            for n,args in enumerate(dd):
                rec=worker.run(*args,f'dev_cal_{index}',f'{n:03}')
                records.append(rec)
                if (n+1)%16==0:print(f'VB calibration {index+1} {n+1}/64',flush=True)
        finally:worker.close()
        p=OUT/f'dev/calibration_{index}.jsonl'
        with p.open('w') as f:
            for r in records:f.write(w.canonical(r)+'\n')
        eligible=[r for r in records if r['temporal_belief']['source_year_count']>=2]
        formed=sum(r['temporal_belief']['formed_ever'] for r in eligible)
        rate=formed/len(eligible) if eligible else None
        calibration.append(dict(candidate=index,threshold=threshold,odds=odds,episodes=len(records),
                                eligible=len(eligible),formed=formed,formation_rate=rate,
                                qualifies=rate is not None and rate>=.5))
        if rate is not None and rate>=.5:
            selection=(threshold,odds);break
    if selection is None:selection=CANDIDATES[-1]
    a.write('dev/calibration.csv',calibration)
    manifest=dict(prereg_commit='798a007',phase='dev8_complete_before_W14_held_out',
                  threshold=selection[0],odds=selection[1],calibration_target_met=any(r['qualifies'] for r in calibration),
                  cp_isolation_gate_passed=cp_eligible,dev_task_ids=sorted({r[0]['id'] for r in dd}),
                  source_hashes={p:a.sha(ROOT/p) for p in SOURCES})
    (OUT/'CODE_FREEZE.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2),flush=True)


def evaluate(pin):
    freeze=json.loads((OUT/'CODE_FREEZE.json').read_text())
    for name,digest in freeze['source_hashes'].items():
        if a.sha(ROOT/name)!=digest:raise RuntimeError('SAFETY_STOP: code freeze changed')
    commit=(OUT/'CODE_FREEZE_COMMIT.txt').read_text().strip()
    if subprocess.run(['git','cat-file','-e',commit+'^{commit}'],cwd=ROOT).returncode:
        raise RuntimeError('missing committed freeze')
    tasks,peers,evidence=a.load()
    standard=json.loads((ROOT/'config/resource_model.json').read_text())['standard_inventory']
    for task in tasks.values():task.setdefault('standard_items',standard)
    references=[r for r in peers if r['method']=='cp_rag']
    missing=[]
    for kind in ('cp_rag_recall_upper','vb_rag_cal'):
        if kind=='cp_rag_recall_upper' and not freeze['cp_isolation_gate_passed']:
            missing.append(dict(method=kind,status='not_reported',reason='dev CP equivalence gate failed'))
            continue
        worker=Worker('eval',pin,kind,freeze['threshold'],freeze['odds'])
        p=OUT/f'{kind}/episodes.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists():raise RuntimeError('refusing to overwrite held-out output')
        try:
            with p.open('w') as f:
                for n,old in enumerate(references):
                    scope=old['scope'];mode,c,world,task=w.key(old)
                    bundle=evidence[scope].get((mode,c,task))
                    try:
                        if bundle is None:raise ValueError('exact frozen evidence unavailable')
                        rec=worker.run(tasks[task],bundle,world,mode,c,kind,f'{n:04}')
                        rec['scope']=scope
                        f.write(w.canonical(rec)+'\n');f.flush()
                    except Exception as exc:
                        if 'SAFETY_STOP' in str(exc) or 'PermissionError' in str(exc):raise
                        missing.append(dict(method=kind,scope=scope,reading_mode=mode,condition=c,world=world,
                                            task_id=task,status='not_reported',reason=str(exc)))
                    if (n+1)%40==0:print(f'{kind} {n+1}/{len(references)}; missing={len(missing)}',flush=True)
        finally:worker.close()
    a.write('not_reported.csv',missing)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=('dev','eval'))
    args=parser.parse_args();pin=w.setup('w14_'+args.phase)
    if args.phase=='dev':develop(pin)
    else:evaluate(pin)


if __name__=='__main__':main()
