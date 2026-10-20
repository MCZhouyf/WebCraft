#!/usr/bin/env python3
"""Align existing W13 logs to the established six-terminal measurement."""
import csv
from collections import defaultdict
import numpy as np
from w13_run import ROOT,OUT,MODES,METRICS,SEED,csvout,jsonl,key,sha,resource_gate
from arbiter.evaluation.post_review_measurement import terminal


def main():
    resource_gate('metric_alignment')
    records={('vb_rag',*key(r)):r for r in jsonl(OUT/'episodes.jsonl')}
    for scope in ('main','ext_v2','cds'):
        for r in jsonl(ROOT/f'release/package/assets/frozen/{scope}.jsonl.gz'):
            if r['method']=='naive_rag':records[('naive_rag',*key(r))]=r
    for r in jsonl(ROOT/'release/package/assets/frozen/cp_rag.jsonl.gz'):
        records[('cp_rag',*key(r))]=r
    with (OUT/'episode_metrics.csv').open(newline='') as f:rows=list(csv.DictReader(f))
    changes=[]
    for row in rows:
        rec=records[(row['method'],row['reading_mode'],row['condition'],row['world'],row['task_id'])]
        rec['metrics']['primary_cost']=sum(float(e['cost']['primary']) for e in rec['events'])
        label=terminal(rec);feasible=bool(rec['true_feasible'])
        abstain=int(label in ('plan_not_found','incorrectly_infeasible')) if feasible else ''
        failure=int(label=='execution_failed') if feasible else ''
        for col,value in [('terminal',label),('overconservative',abstain),('execution_failure',failure)]:
            old=row[col];row[col+'_legacy_w8_helper']=old;row[col]=value
            if str(old)!=str(value):
                changes.append({**{k:row[k] for k in ('scope','reading_mode','method','condition','world','task_id')},
                                'metric':col,'legacy_helper':old,'canonical_existing_definition':value})
        old_search=int(row['search_calls'])
        if row['method']=='cp_rag' and row['reading_mode'] in ('all_public','llm_only') and row['scope']!='cds':
            additional=len({t['denied_subject'] for t in rec.get('policy_trace',[])
                            if t.get('research_triggered') and 'denied_subject' in t})
            row['search_calls_initial']=old_search
            row['search_calls_additional']=additional
            row['search_calls_provenance']='initial_reader_plus_trace_inferred_cached_research'
            row['search_calls']=old_search+additional
        elif row['method']=='vb_rag':
            additional=sum(bool(t['executed']) for t in rec['reader'].get('vb_research',[]))
            row['search_calls_initial']=old_search-additional
            row['search_calls_additional']=additional
            row['search_calls_provenance']='initial_reader_plus_actual_research_rpc'
        else:
            row['search_calls_initial']=old_search
            row['search_calls_additional']=0
            row['search_calls_provenance']='reader_no_public_research'
        row['search_calls_legacy_reader_only']=old_search
        if old_search!=int(row['search_calls']):
            changes.append({**{k:row[k] for k in ('scope','reading_mode','method','condition','world','task_id')},
                            'metric':'search_calls','legacy_helper':old_search,'canonical_existing_definition':row['search_calls']})
    csvout('episode_metrics_canonical.csv',rows);csvout('metric_alignment_changes.csv',changes)
    groups=defaultdict(list)
    for row in rows:
        for grouping in ('overall','condition_group','condition_world'):
            cond=row['condition_group'] if grouping=='condition_group' else row['condition'] if grouping=='condition_world' else 'all'
            world=row['world'] if grouping=='condition_world' else 'all'
            groups[(row['scope'],row['reading_mode'],row['method'],grouping,cond,world)].append(row)
    aggregates=[]
    for k,rr in sorted(groups.items()):
        result=dict(zip(('scope','reading_mode','method','grouping','condition','world'),k))
        result.update(n=len(rr),feasible_n=sum(int(r['true_feasible']) for r in rr))
        for metric in METRICS:
            values=[float(r[metric]) for r in rr if r[metric]!='']
            result[metric+'_denominator']=len(values)
            result[metric]=sum(values)/len(values) if values else ''
        aggregates.append(result)
    csvout('results_by_condition_canonical.csv',aggregates)
    effects=[]
    for mode in MODES:
        selected=[r for r in rows if r['scope']=='main' and r['reading_mode']==mode and r['condition']=='mixed']
        tids=sorted({r['task_id'] for r in selected})
        draws=np.random.default_rng(SEED).integers(0,len(tids),(10000,len(tids)))
        for peer in ('naive_rag','cp_rag'):
            for metric in METRICS:
                arrays=[]
                for method in ('vb_rag',peer):
                    nums=[];dens=[]
                    for task in tids:
                        vs=[float(r[metric]) for r in selected if r['method']==method and r['task_id']==task and r[metric]!='']
                        nums.append(sum(vs));dens.append(len(vs))
                    arrays.append((np.array(nums),np.array(dens)))
                a,b=arrays
                if min(a[1].sum(),b[1].sum())==0:continue
                with np.errstate(divide='ignore',invalid='ignore'):
                    boot=a[0][draws].sum(1)/a[1][draws].sum(1)-b[0][draws].sum(1)/b[1][draws].sum(1)
                lo,hi=np.nanquantile(boot,[.025,.975])
                effects.append(dict(scope='main',condition='mixed',reading_mode=mode,peer=peer,metric=metric,
                                    VB_minus_peer=a[0].sum()/a[1].sum()-b[0].sum()/b[1].sum(),
                                    ci95_low=float(lo),ci95_high=float(hi),paired_tasks=len(tids),bootstrap_draws=10000,seed=SEED))
    csvout('PRIMARY_mixed_paired_effects_canonical.csv',effects)
    source=ROOT.parent/'arbiter/src/arbiter/evaluation/post_review_measurement.py'
    (OUT/'METRIC_ALIGNMENT.md').write_text(
        '# Official six-terminal metric alignment\n\n'
        'Policy and episodes are unchanged. The evaluation adapter initially used the old\n'
        'W8 helper, which labels an unsuccessful feasible episode with infeasible submit\n'
        'overconservative even after an action failed. The established six-terminal\n'
        'definition prioritizes execution_failed after budget_exhausted unless SR is true.\n\n'
        '*_canonical.csv uses existing post_review_measurement.terminal (113-123), not a\n'
        'new fitted rule. false abstention = (plan_not_found + incorrectly_infeasible) /\n'
        'feasible; execution failure = execution_failed / feasible. Original exports\n'
        'remain unchanged as legacy evidence. SR, actions, cost, attribution and belief\n'
        'are unchanged. No agent was rerun for this alignment.\n\n'
        'Retrieval cost includes extra re-search. CP reader counts omit these calls;\n'
        'the new table adds distinct denied-subject callbacks only in public-index modes,\n'
        'matching the frozen callback cache key. CP extras are trace-inferred (W10 protocol),\n'
        'VB extras are actual RPC counts; both provenance columns are retained.\n\n'
        f'Classifier SHA-256: {sha(source)}. Changed row/field entries: {len(changes)}.\n')
    print(f'Canonical metric alignment complete: {len(changes)} row/field changes; no new episodes',flush=True)


if __name__=='__main__':main()
