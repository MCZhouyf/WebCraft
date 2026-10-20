#!/usr/bin/env python3
"""Single preregistered semantic O attempt using the ff3bf4a method lock."""
from __future__ import annotations
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARB = ROOT.parent/'arbiter'
OUT = ROOT/'reports/paperA/w9_release_fix/o_gate'
LOCK = Path('/root/autodl-tmp/w9_locked_ff3bf4a')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'ATTEMPT_STARTED.json').exists():
        raise RuntimeError('W9 O gate already attempted; preregistration forbids a third attempt')
    LOCK.mkdir(parents=True,exist_ok=True)
    archive=subprocess.run(['git','archive','ff3bf4a','src','config','pyproject.toml'],cwd=ARB,
                           capture_output=True,check=True).stdout
    import io
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(LOCK)
    sys.path[:0]=[str(LOCK/'src'),str(ROOT)]
    from arbiter.evaluation import p6_execution as locked
    import arbiter.evaluation as evaluation
    evaluation.__path__.append(str(ARB/'src/arbiter/evaluation'))
    from arbiter.evaluation.external_scoring import ACTION_METHODS,load_resource_nodes
    from arbiter.evaluation.measurement_v3 import classify_actions_v3,intrinsic_discriminative
    from arbiter.evaluation.post_review_measurement import terminal
    from arbiter.evaluation.p6_matrix import P6Job
    from arbiter.agent.aggregation import operator_from_assertion
    import arbiter.adapters.webcraft as wa
    import arbiter.adapters.claims as ca
    import arbiter.adapters.search as sa
    spec=importlib.util.spec_from_file_location('arbiter.evaluation.w9_appendix_p6',ARB/'src/arbiter/evaluation/p6_execution.py')
    appendix=importlib.util.module_from_spec(spec);spec.loader.exec_module(appendix)
    # The nine original methods execute locked.run_episode. Only the registered
    # post-freeze Probe-then-RAG row uses its source-documented appendix adapter.
    for module in (locked,appendix,wa,ca,sa):
        module.verify_webcraft_pin=lambda *a,**kw:{'method_lock':'ff3bf4a'}
    class Forbidden:
        def __init__(self,*a,**kw):raise RuntimeError('W9 forbids LLM calls')
    locked.GatewayClient=appendix.GatewayClient=Forbidden
    read=lambda path:[json.loads(line) for line in path.read_text().splitlines() if line]
    conflicts={r['id']:r for r in read(ROOT/'data/conflicts.jsonl')}
    vectors={mid:r['truth_vector'] for mid,r in conflicts.items()}
    nodes=load_resource_nodes(ROOT/'harness/config/arena.json')
    tasks={r['id']:r for r in read(ROOT/'data/tasks.jsonl') if r['split']=='eval'}
    standard=json.loads((ROOT/'config/resource_model.json').read_text())['standard_inventory']
    prior=json.loads((ARB/'artifacts/prior/frozen_prior_bundle.json').read_text())
    prior={'operators':[operator_from_assertion(r) for r in prior['assertions']],'samples':prior['samples']}
    baseline=read(ARB/'artifacts/main/all_public/episodes.jsonl')+read(ARB/'artifacts/post_run_probe_then_rag/all_public/episodes.jsonl')
    baseline=[r for r in baseline if int(r['seed'])==20261006 and r['condition'] in {
        'mismatched_old2new','matched_1.21.11','mismatched_new2old','matched_1.16.5'}]
    baseline=sorted(baseline,key=lambda r:(r['condition'],r['world'],r['task_id'],r['method']))
    assert len(baseline)==1360
    def metrics(record):
        events=record['events'];semantic=[i for i,e in enumerate(events) if e.get('method') in ACTION_METHODS]
        mapping={i:[mid] for i,mid in zip(semantic,record['used_mechanism_ids']) if mid in vectors
                 and vectors[mid].get(record['world']) is False and any(vectors[mid].values())}
        ann=classify_actions_v3(events,stale_by_event=mapping,conflicts=conflicts,resource_nodes=nodes,
                                target=tasks[record['task_id']]['target']['item'],episode_success=bool(record['metrics']['SR']))
        query=action=0
        for row in ann:
            event=events[row['event_index']]
            specific=intrinsic_discriminative(event,row['stale_mechanism_ids'],world=record['world'],conflicts=conflicts,resource_nodes=nodes) or row['referenced_at']
            if specific:
                if event['method']=='check_name':query=1
                else:action=1
        label=terminal(record);feasible=record['true_feasible']
        return {'terminal':label,'SR':record['metrics']['SR'],
                'overconservative':int(feasible and label in {'plan_not_found','incorrectly_infeasible'}),
                'execution_failure':int(feasible and label=='execution_failed'),
                'plan_acceptance':record['metrics']['HTR_commit'],'stale_contact':record['metrics']['HTR_all'],
                'specific_query':query,'specific_action':action,'semantic_actions':len(semantic),
                'semantic_cost':record['metrics']['primary_cost'],'search_calls':record['reader'].get('search_count',0)}
    started=time.monotonic()
    (OUT/'ATTEMPT_STARTED.json').write_text(json.dumps({'method_lock':'ff3bf4a','PYTHONHASHSEED':os.environ.get('PYTHONHASHSEED'),
        'expected':1360,'appendix_probe_p6_sha256':hashlib.sha256((ARB/'src/arbiter/evaluation/p6_execution.py').read_bytes()).hexdigest()},indent=2)+'\n')
    results=[]
    for old in baseline:
        raw=tasks[old['task_id']]
        task={'id':raw['id'],'target':raw['target'],'feasible':raw['feasible'],'conflict_ids':raw['conflict_ids'],
              'standard_items':standard,'task_supplements':raw.get('task_supplements',[])}
        bundle=json.loads((ARB/f"artifacts/main/all_public/evidence/{old['condition']}__{old['task_id']}__20261006.json").read_text())
        runner=appendix if old['method']=='probe_then_rag' else locked
        record={'condition':old['condition'],'world':old['world'],'task_id':old['task_id'],'method':old['method']}
        try:
            new=runner.run_episode(P6Job(old['condition'],old['task_id'],old['world'],old['method'],old['seed']),task,bundle,
                root=ROOT,pin_path=OUT/'ATTEMPT_STARTED.json',index_dir=str(ROOT/'corpus/indexes/formal_p1'),
                output_dir=OUT/'runtime',conflict_vectors=vectors,preview=False,prior_bundle=prior)
            oldm,newm=metrics(old),metrics(new)
            changed=[key for key in oldm if oldm[key]!=newm[key]]
            if Counter(old['used_mechanism_ids'])!=Counter(new['used_mechanism_ids']):changed.append('mechanism_multiset')
            record.update({'passed':int(not changed),'changed':';'.join(changed),
                           'frozen_metrics':json.dumps(oldm,sort_keys=True),'replayed_metrics':json.dumps(newm,sort_keys=True),
                           'frozen_mechanisms':json.dumps(old['used_mechanism_ids']),
                           'replayed_mechanisms':json.dumps(new['used_mechanism_ids'])})
        except Exception as exc:
            record.update({'passed':0,'changed':type(exc).__name__+': '+str(exc)})
        results.append(record)
        if not record['passed']:break
        if len(results)%100==0:print(json.dumps({'O_gate_compared':len(results),'elapsed_s':round(time.monotonic()-started,2)}),flush=True)
    with (OUT/'comparison.csv').open('w',newline='') as handle:
        fields=list(dict.fromkeys(k for r in results for k in r))
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(results)
    passed=len(results)==1360 and all(r['passed'] for r in results)
    summary={'expected':1360,'compared':len(results),'passed':passed,'failed_instances':sum(not r['passed'] for r in results),
             'elapsed_s':round(time.monotonic()-started,3),'interventions_run':False}
    (OUT/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    if not passed:
        (OUT/'REPORT.md').write_text('# W9 semantic O gate\n\nThe one registered gate attempt failed. '
            f"Compared {len(results)}/1,360 episodes; the first failure is in comparison.csv. "
            'V/C/V+C were not run. No paired effects or allocation of the 11.7647 pp gap is available. '
            'The method/planner code came from ff3bf4a; the appendix Probe-then-RAG adapter was separately hashed. '
            'No third gate attempt or parameter change was made.\n')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
