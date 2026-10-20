#!/usr/bin/env python3
"""Registered interval and CP-reference sensitivity; no episode execution."""
from __future__ import annotations
import copy
import json
from dataclasses import replace
from collections import defaultdict
import numpy as np
from w10_impact import ROOT, PAPER, OUT, ARB, read, write, records, key, load_module
from arbiter.evaluation.reporting import EpisodeRecord


def main():
    pairs=json.loads((OUT/'nine_episode_pairs.json').read_text())
    updates={tuple(x['key']):x['replayed'] for x in pairs}
    helper=load_module('w10_registered_export',ARB/'scripts/export_post_run_baseline.py')
    old=[]; new=[]
    for path in (ARB/'artifacts/main/all_public/episodes.jsonl',
                 ARB/'artifacts/post_run_probe_then_rag/all_public/episodes.jsonl'):
        for r in records(path):
            a=EpisodeRecord.from_dict(r)
            old.append(a)
            k=('all_public',a.method,a.condition,a.world,a.task_id)
            b=updates.get(k)
            new.append(a if b is None else replace(a,probes=float(b['probe_count']),probe_cost=float(b['probe_cost'])))
    assert len(old)==len(new)==7480
    ci_changes=[]; baseline_errors=[]; checked=0
    def check(path, left, right, groupfields):
        nonlocal checked
        lookup={tuple(str(r.get(f,'')) for f in groupfields):(a,b)
                for a,b in zip(left,right) for r in (a,)}
        counter=read(OUT/('counterfactual_'+path.name))
        for line,(r,out) in enumerate(zip(read(path),counter),2):
            if r.get('reading_mode')!='all_public':continue
            ident=tuple(r.get(f,'') for f in groupfields)
            if ident not in lookup:continue
            a,b=lookup[ident]
            for field in r:
                if not (field.startswith(('probe_count','probe_cost')) and '_ci_' in field):continue
                if r[field]=='' and a[field]=='':continue
                checked+=1
                if abs(float(r[field])-float(a[field]))>1e-9:
                    baseline_errors.append({'source':str(path.relative_to(ROOT)),'row':line,'column':field,
                                            'published':r[field],'recomputed_frozen':a[field]})
                    continue
                out[field]=b[field]
                if f'{a[field]:.3f}'!=f'{b[field]:.3f}':
                    ci_changes.append({'source':str(path.relative_to(ROOT)),'row':line,'method':r['method'],
                                       'group':'|'.join(ident),'column':field,'frozen':a[field],
                                       'counterfactual':b[field],'frozen_3dp':f'{a[field]:.3f}',
                                       'counterfactual_3dp':f'{b[field]:.3f}'})
        write('counterfactual_'+path.name,counter)
    # The accepted subsets were regenerated together, using a fresh fixed RNG per subset.
    left=helper._corrected_subsets('all_public',old)
    right=helper._corrected_subsets('all_public',new)
    for name in ('S7_three_subsets.csv','S7_control_subset.csv'):
        check(PAPER/name,left,right,('subset','method'))
    # The original nine methods and the post-run baseline had separate RNG streams.
    def cells(values):
        output=[]
        for ispost in (False,True):
            group=[replace(r,condition=f'{r.condition}@@{r.world}') for r in values
                   if (r.method=='probe_then_rag')==ispost]
            for row in helper._aggregate(group):
                row=dict(row);row['condition'],row['world']=row['condition'].split('@@')
                output.append(row)
        return output
    check(PAPER/'S7_world_breakdown.csv',cells(old),cells(new),('method','condition','world'))
    write('changed_S7_interval_cells.csv',ci_changes)
    write('S7_interval_baseline_errors.csv',baseline_errors)
    # No negative-feedback event was changed in the affected Verify-All episode.
    negative=[]
    for pair in pairs:
        if pair['key'][1]!='verify_all':continue
        a,b=pair['frozen'],pair['replayed']
        for label,r in (('frozen',a),('replayed',b)):
            negative.append({'kind':label,'key':pair['key'],
                'negative_public_events':[{'method':e['method'],'params':e.get('params'),
                    'result':e.get('result'),'error':e.get('error')}
                    for e in r['events'] if e.get('error') or
                    (isinstance(e.get('result'),dict) and e['result'].get('ok') is False)]})
        assert negative[-1]['negative_public_events']==negative[-2]['negative_public_events']
    (OUT/'verify_all_negative_event_invariants.json').write_text(json.dumps(negative,indent=2)+'\n')
    w8=load_module('w10_w8',ROOT/'scripts/run_w8_closeout_20261016.py')
    cp=read(PAPER/'w8_closeout/cp_rag_episode_metrics.csv')
    changedcp=copy.deepcopy(cp)
    catalog={r['id']:r for r in records(ROOT/'data/conflicts.jsonl')}
    from arbiter.evaluation.external_scoring import load_resource_nodes
    nodes=load_resource_nodes(ROOT/'harness/config/arena.json')
    tasks={r['id']:r for r in records(ROOT/'data/tasks.jsonl')}
    for r in changedcp:
        k=(r['scope'].removeprefix('main:'),r['method'],r['condition'],r['world'],r['task_id'])
        if k not in updates:continue
        rec=updates[k]
        contact,query,action,prop=w8.v3d(rec,catalog,nodes,tasks[r['task_id']]['target'])
        r.update(semantic_actions=sum(e['method'] in w8.ACTION_METHODS for e in rec['events']),
                 semantic_cost=rec['metrics']['primary_cost'],
                 discriminative_stale_contact=contact,discriminative_stale_query=query,
                 discriminative_stale_action=action,v3d_propagation=prop)
    metric_names=('SR','semantic_actions','semantic_cost','search_calls','plan_acceptance','stale_contact',
                  'discriminative_stale_contact','discriminative_stale_query','discriminative_stale_action','v3d_propagation')
    def effects(values):
        by=defaultdict(dict)
        for r in values:by[(r['scope'],r['condition'],r['world'],r['task_id'])][r['method']]=r
        output=[]
        for scope in sorted({r['scope'] for r in values}):
            for m in metric_names:
                vs=[(p['cp_rag']['unit'],float(p['cp_rag'][m])-float(p['naive_rag'][m]))
                    for k,p in by.items() if k[0]==scope]
                effect,lo,hi=w8.bootstrap(vs)
                output.append({'scope':scope,'metric':m,'CP_minus_Naive':effect,'ci95_low':lo,'ci95_high':hi})
        return output
    ce=[]; before,after=effects(cp),effects(changedcp)
    published={(r['scope'],r['metric']):r for r in read(PAPER/'w8_closeout/cp_rag_paired_effects.csv')}
    for a,b in zip(before,after):
        for col in ('CP_minus_Naive','ci95_low','ci95_high'):
            assert abs(a[col]-float(published[(a['scope'],a['metric'])][col]))<1e-12
            if f'{a[col]:.3f}'!=f'{b[col]:.3f}':ce.append({
                'scope':a['scope'],'metric':a['metric'],'column':col,'frozen':a[col],
                'counterfactual':b[col],'frozen_3dp':f'{a[col]:.3f}','counterfactual_3dp':f'{b[col]:.3f}'})
    write('CP_paired_effects_counterfactual.csv',after)
    write('changed_CP_paired_cells.csv',ce)
    summary={'S7_interval_cells_checked':checked,'S7_interval_baseline_errors':len(baseline_errors),
             'S7_interval_rounded_changes':len(ci_changes),'CP_rounded_changes':len(ce),
             'VerifyAll_negative_event_signatures_unchanged':True}
    (OUT/'APPENDIX_SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
