#!/usr/bin/env python3
"""W14 postprocessing only. Policies and frozen inputs are never modified."""
from __future__ import annotations
import csv
import json
from collections import Counter,defaultdict
from dataclasses import replace
import numpy as np
import w14_audit as a
import w13_run as w
from arbiter.evaluation.post_review_measurement import terminal

ROOT,OUT=a.ROOT,a.OUT
METRICS=w.METRICS


def csvread(p):
    with p.open(newline='') as f:return list(csv.DictReader(f))


def row(record,tasks,catalog,nodes,ops,ground):
    r=record;scope=r.get('scope',w.scope(r));semantic=[e for e in r['events'] if e['method'] in
        ('mine','craft_grid','smelt','check_name')]
    if len(semantic)!=len(r['used_mechanism_ids']):raise RuntimeError('action/mechanism misalignment')
    role=w.p6._action_role_metrics(r['events'],r['used_mechanism_ids'],r['world'],
                                  {mid:c['truth_vector'] for mid,c in catalog.items()})
    r={**r,'metrics':{**r['metrics'],**role}}
    label=terminal(r);feasible=bool(r['true_feasible'])
    target=tasks[r['task_id']]['target']['item']
    try:
        _,query,action,prop=w.w8.v3d(r,catalog,nodes,target)
    except (ValueError,KeyError,IndexError):query=action=prop=''
    acceptance=role['HTR_commit'];grounded=''
    if scope=='cds':
        lookup=ops[(r['reading_mode'],r['condition'],r['task_id'])]
        stale=False;unknown=False
        for mid,event in zip(r['used_mechanism_ids'],semantic):
            op=lookup.get(mid)
            actual=replace(op,params={**op.params,**event['params']}) if op else None
            valid=ground.valid(actual,r['world']) if actual else None
            if event.get('intent')!='probe':
                stale|=valid is False and any(ground.valid(actual,v) is True for v in ground.truth)
                unknown|=valid is None
        grounded=int(stale) if stale or not unknown else ''
        acceptance=grounded
    calls=int(r.get('reader',{}).get('search_count',0))
    if r['method']=='cp_rag' and scope!='cds' and r['reading_mode'] in ('all_public','llm_only'):
        calls+=len({t['denied_subject'] for t in r.get('policy_trace',[]) if t.get('research_triggered') and 'denied_subject' in t})
    group='matched' if r['condition'].startswith('matched_') else r['condition'].rsplit('__',1)[-1] if scope=='cds' else r['condition']
    return dict(scope=scope,reading_mode=r['reading_mode'],condition=r['condition'],condition_group=group,
        world=r['world'],task_id=r['task_id'],family_id=tasks[r['task_id']].get('independent_family_id',r['task_id']),
        method=r['method'],true_feasible=int(feasible),terminal=label,SR=int(r['metrics']['SR']),
        feasible_SR=int(r['metrics']['SR']) if feasible else '',
        overconservative=int(label in ('plan_not_found','incorrectly_infeasible')) if feasible else '',
        execution_failure=int(label=='execution_failed') if feasible else '',
        plan_acceptance=acceptance,catalogue_plan_acceptance=role['HTR_commit'],
        target_grounded_plan_acceptance=grounded,archived_plan_acceptance=record['metrics'].get('HTR_commit',''),
        specific_query=query,specific_action=action,specific_contact=int(bool(query or action)) if query!='' else '',
        specific_provenance='frozen_catalogue_classifier_excludes_unregistered_operators',
        v3d_propagation=prop,semantic_actions=len(semantic),semantic_cost=role['primary_cost'],search_calls=calls,
        registered_operator_actions=sum(mid in catalog for mid in r['used_mechanism_ids']),
        formed_year=r.get('temporal_belief',{}).get('formed_year'),
        formed_ever=r.get('temporal_belief',{}).get('formed_ever',False),
        source_year_count=r.get('temporal_belief',{}).get('source_year_count',''))


def aggregate(rows):
    groups=defaultdict(list)
    for r in rows:
        for g,c in [('overall','all'),('condition_group',r['condition_group']),('condition_world',r['condition'])]:
            groups[(r['scope'],r['reading_mode'],r['method'],g,c,r['world'] if g=='condition_world' else 'all')].append(r)
    out=[]
    for k,rr in sorted(groups.items()):
        result=dict(zip(('scope','reading_mode','method','grouping','condition','world'),k));result['n']=len(rr)
        for field in METRICS+('catalogue_plan_acceptance',):
            values=[float(r[field]) for r in rr if r[field]!='']
            result[field]=sum(values)/len(values) if values else ''
            result[field+'_numerator']=sum(values);result[field+'_denominator']=len(values)
        out.append(result)
    return out


def effects(rows):
    out=[]
    groups=defaultdict(list)
    for r in rows:
        groups[(r['scope'],r['reading_mode'],'all')].append(r)
        groups[(r['scope'],r['reading_mode'],r['condition_group'])].append(r)
    for (scope,mode,condition),rr in sorted(groups.items()):
        by={(r['method'],r['condition'],r['world'],r['task_id']):r for r in rr}
        for method in ('cp_rag_recall_upper','vb_rag_cal'):
            for peer in ('naive_rag','cp_rag','vb_rag'):
                paired=[]
                for r in rr:
                    if r['method']!=method:continue
                    other=by.get((peer,r['condition'],r['world'],r['task_id']))
                    if other:paired.append((r,other))
                if not paired:continue
                for field in METRICS:
                    unit=defaultdict(lambda:[0.,0.,0.,0.])
                    for x,y in paired:
                        if x[field]=='' or y[field]=='':continue
                        u=x['task_id'] if scope=='main' else x['family_id']
                        unit[u][0]+=float(x[field]);unit[u][1]+=1
                        unit[u][2]+=float(y[field]);unit[u][3]+=1
                    if not unit:continue
                    values=np.array([unit[k] for k in sorted(unit)])
                    draws=np.random.default_rng(a.SEED).integers(0,len(values),(10000,len(values)))
                    bb=values[draws].sum(1)
                    d=bb[:,0]/bb[:,1]-bb[:,2]/bb[:,3]
                    sums=values.sum(0);lo,hi=np.quantile(d,[.025,.975])
                    out.append(dict(scope=scope,reading_mode=mode,condition=condition,method=method,peer=peer,
                        metric=field,delta=float(sums[0]/sums[1]-sums[2]/sums[3]),ci95_low=float(lo),ci95_high=float(hi),
                        paired_episodes=int(sums[1]),clusters=len(values),bootstrap_draws=10000,seed=a.SEED,
                        unit='task' if scope=='main' else 'independent_family'))
    return out


def main():
    w.OUT=OUT;w.resource_gate('finalize')
    integrity=[]
    for name in ('stage0/INPUT_HASHES.csv',):
        for item in csvread(OUT/name):
            p=ROOT/item['path'];match=p.is_file() and a.sha(p)==item['sha256']
            integrity.append(dict(path=item['path'],unchanged=match,reference=name))
            if not match:raise RuntimeError('SAFETY_STOP: stage-zero frozen input changed')
    for item in csvread(ROOT/'reports/paperA/w13_vbrag/diagnostics/INPUT_MANIFEST.csv'):
        if item['path'].startswith(('truth/','corpus/')):
            p=ROOT/item['path'];match=p.is_file() and a.sha(p)==item['sha256']
            integrity.append(dict(path=item['path'],unchanged=match,reference='W13 diagnostics input freeze'))
            if not match:raise RuntimeError('SAFETY_STOP: inherited truth/corpus input changed')
    a.write('frozen_input_integrity.csv',integrity)
    tasks,peers,evidence=a.load()
    catalog={r['id']:r for r in a.read(ROOT/'data/conflicts.jsonl')}
    nodes=w.load_resource_nodes(ROOT/'harness/config/arena.json')
    normalizer,_=w.p6._compiler(ROOT);ground=a.Grounded()
    ops={k:{op.mechanism_id:op for op in w.p6._claims(b,normalizer=normalizer)[1]} for k,b in evidence['cds'].items()}
    rows=[];new_records=[]
    for kind in ('cp_rag_recall_upper','vb_rag_cal'):
        p=OUT/f'{kind}/episodes.jsonl'
        if p.is_file():new_records.extend(a.read(p))
    records=peers+list(a.read(ROOT/'reports/paperA/w13_vbrag/episodes.jsonl'))+new_records
    for rec in records:rows.append(row(rec,tasks,catalog,nodes,ops,ground))
    a.write('episode_metrics.csv',rows)
    agg=aggregate(rows);a.write('results_by_condition.csv',agg)
    ee=effects(rows);a.write('paired_effects.csv',ee)
    a.write('PRIMARY_mixed_paired_effects.csv',[r for r in ee if r['scope']=='main' and r['condition']=='mixed'])
    temporal=defaultdict(list);identities=[];callbacks=[]
    peers_map={(r['method'],*w.key(r)):r for r in records}
    for r in new_records:
        scope=r['scope'];group='matched' if r['condition'].startswith('matched_') else r['condition'].rsplit('__',1)[-1] if scope=='cds' else r['condition']
        if r['method']=='vb_rag_cal':temporal[(scope,r['reading_mode'],group)].append(r)
        for peer in ('naive_rag','cp_rag','vb_rag'):
            other=peers_map.get((peer,*w.key(r)))
            if other:identities.append(dict(scope=scope,reading_mode=r['reading_mode'],method=r['method'],peer=peer,
                condition=r['condition'],world=r['world'],task_id=r['task_id'],
                identical=int(w.actions(r)==w.actions(other)),intent_inclusive_identical=int(w.actions(r,True)==w.actions(other,True))))
        if r['method']=='cp_rag_recall_upper':
            traces=[t for t in r['policy_trace'] if t.get('research_triggered')]
            for n,q in enumerate(r.get('research_details',[])):
                t=traces[n] if n<len(traces) else {}
                callbacks.append(dict(scope=scope,reading_mode=r['reading_mode'],condition=r['condition'],world=r['world'],
                    task_id=r['task_id'],query=q['query'],subject=q['subject'],returned_chunks=q['returned_chunks'],
                    cached_claims=q['claims'],new_candidates=t.get('new_candidates',''),
                    chunk_ids=json.dumps(q['chunk_ids']),diagnostic_upper_bound=True))
    a.write('action_identity.csv',identities);a.write('research_callbacks.csv',callbacks)
    belief=[];years={'1.16.5':2021,'1.19':2022,'1.21.11':2025}
    for k,rr in sorted(temporal.items()):
        final=[r for r in rr if r['temporal_belief']['formed_year'] is not None]
        correct=sum(r['temporal_belief']['formed_year']==years[r['world']] for r in final)
        belief.append(dict(scope=k[0],reading_mode=k[1],condition=k[2],n=len(rr),
            formed_ever=sum(r['temporal_belief']['formed_ever'] for r in rr),
            formation_rate=sum(r['temporal_belief']['formed_ever'] for r in rr)/len(rr),
            formed_final=len(final),correct_final=correct,accuracy_formed=correct/len(final) if final else '',
            accuracy_all=correct/len(rr)))
    a.write('temporal_belief.csv',belief)
    cal_barriers=[]
    for p in sorted((OUT/'dev').glob('calibration_*.jsonl')):
        for r in a.read(p):
            b=r['temporal_belief']
            if b['source_year_count']<2:barrier='not_multiyear_eligible'
            elif b['formed_ever']:barrier='formed'
            elif not any(len(t.get('posterior',{}))>=2 and
                         max(t['posterior'].values())-min(t['posterior'].values())>1e-12
                         for t in r['policy_trace']):barrier='no_nonuniform_observation_posterior'
            else:barrier='posterior_threshold_or_tied_maximum'
            cal_barriers.append(dict(candidate=p.stem,task_id=r['task_id'],condition=r['condition'],world=r['world'],
                                     eligible=int(b['source_year_count']>=2),formed=int(b['formed_ever']),barrier=barrier))
    a.write('dev/calibration_barriers.csv',cal_barriers)
    original=csvread(ROOT/'reports/paperA/w13_vbrag/diagnostics/retrieval_203_interpreted.csv')
    overlap=[]
    for q in original:
        # The 118 are the missed-at-top10 subset. Three already-retrieved
        # compilable/zero-novel cases are a different W13 diagnostic category.
        if (q['category']!='exists_bm25_missed' or
                q['compiler_after_full_recall'] not in ('True','1','true')):continue
        hits=[x for x in callbacks if all(x[f]==q[f] for f in ('reading_mode','condition','world','task_id','query'))]
        overlap.append({**{f:q[f] for f in ('reading_mode','condition','world','task_id','query','category')},
            'W13_compilable_after_recall':True,'upper_callback_count':len(hits),
            'upper_new_candidates':sum(int(x['new_candidates'] or 0) for x in hits),
            'explanation':'same opportunity' if hits else 'no same callback reached under upper policy trajectory'})
    a.write('W13_118_overlap.csv',overlap)
    reference=[]
    # Historical version-informed policies, no new execution or task tuning.
    for r in csvread(ROOT/'reports/paperA/version_scoped/paired_instance_comparisons.csv'):
        if r['reference_method']!='probe_then_rag':continue
        for method,prefix in (('probe_then_version_scoped','version_scoped'),('probe_then_rag','reference')):
            reference.append(dict(method=method,reading_mode=r['reading_mode'],condition=r['condition'],world=r['world'],
                task_id=r['task_id'],SR=r[prefix+'_SR'],plan_acceptance=r[prefix+'_HTR_commit'],
                provenance='historical_version_informed_reference_not_W14_runtime'))
    a.write('version_informed_reference.csv',reference)
    ref_groups=defaultdict(list)
    for r in reference:
        g='matched' if r['condition'].startswith('matched_') else r['condition']
        ref_groups[(r['method'],r['reading_mode'],g)].append(r)
    a.write('version_informed_reference_summary.csv',[
        dict(method=k[0],reading_mode=k[1],condition_group=k[2],n=len(rr),
             SR=sum(int(r['SR']) for r in rr)/len(rr),
             plan_acceptance=sum(float(r['plan_acceptance']) for r in rr)/len(rr),
             provenance='historical_version_informed_reference')
        for k,rr in sorted(ref_groups.items())])
    summary=dict(LLM_calls=0,episodes_by_method_scope=dict(Counter(r['method']+'/'+r['scope'] for r in new_records)),
        worker_hidden_reads=sum(r['runtime_guard']['evaluation_asset_reads'] for r in new_records),
        worker_network_calls=sum(r['runtime_guard']['network_calls'] for r in new_records),
        research_callbacks=len(callbacks),research_with_new_candidates=sum(int(r['new_candidates'] or 0)>0 for r in callbacks),
        research_new_candidates_total=sum(int(r['new_candidates'] or 0) for r in callbacks),
        W13_recallable_compilable=len(overlap),overlap_callbacks=sum(r['upper_callback_count']>0 for r in overlap),
        W13_118_with_new_candidates=sum(r['upper_new_candidates']>0 for r in overlap),
        code_unchanged=all(a.sha(ROOT/p)==h for p,h in json.loads((OUT/'CODE_FREEZE.json').read_text())['source_hashes'].items()))
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    a.write('INPUT_HASHES.csv',[dict(path=p,sha256=h) for p,h in sorted(a.INPUTS.items())])
    lines=['# W14 final results','',
        'Preregistration 798a007; stage-zero audit 799bd3a. No prior number or frozen asset was changed.',
        'Stage-zero findings and CDS scoring limitations are in STAGE0_REPORT.md.',
        'CP-RAG-recall-upper is a nondeployable recall diagnostic, not a ranked method.',
        'VB-RAG-cal changes formation thresholds only; original VB results remain separate.','',
        '## Coverage and calibration','', '```json',json.dumps(summary,indent=2),'```','',
        '```json',(OUT/'CODE_FREEZE.json').read_text().strip(),'```','',
        '## Primary mixed comparisons','',
        '|Mode|Method|Reference|SR difference (pp)|95% CI (pp)|Paired episodes|',
        '|---|---|---|---:|---|---:|']
    for r in ee:
        if r['scope']=='main' and r['condition']=='mixed' and r['metric']=='SR':
            lines.append(f"|{r['reading_mode']}|{r['method']}|{r['peer']}|{100*r['delta']:.3f}|[{100*r['ci95_low']:.3f}, {100*r['ci95_high']:.3f}]|{r['paired_episodes']}|")
    lines+=['','## Mixed temporal formation','',
        '|Mode|Formed ever / n|Final formed|Final correct / formed|','|---|---:|---:|---:|']
    for r in belief:
        if r['scope']=='main' and r['condition']=='mixed':
            lines.append(f"|{r['reading_mode']}|{r['formed_ever']}/{r['n']}|{r['formed_final']}|{r['correct_final']}/{r['formed_final']}|")
    lines+=['','## Interpretation and scoring provenance','',
        'Tables never pool reading modes. Main paired intervals resample tasks; EXT/CDS intervals resample families.',
        'Feasible SR, false abstention and execution failure have feasible-only denominators.',
        'CDS plan_acceptance is the parallel target-grounded concrete-operator score registered in W14,',
        'with historical archived and global-catalogue values in separate columns. No corrected CDS experiment was run.',
        'Specific contact and v3-d use the unchanged catalogue scorer; unregistered compiled operators are not silently',
        'added to its coverage. registered_operator_actions and semantic_actions disclose that limitation.',
        'The CDS conditions differ in literal evidence/polarity but K/K+S/S have identical compiled payload sets;',
        'do not interpret their equal SR as a valid hard knowledge-deletion intervention.',
        'Original VB has no polluted episodes; paired comparisons against it explicitly have smaller coverage.',
        'Source-year accuracy uses release calendar year, not a designer version mapping in the policy.',
        'Version-informed references are exported by exact keys in version_informed_reference.csv, not rerun.','',
        '## Files','',
        '- episode_metrics.csv: events and denominators, one row per episode/method.',
        '- results_by_condition.csv and paired_effects.csv: all metrics and condition groups.',
        '- PRIMARY_mixed_paired_effects.csv: preregistered primary comparisons.',
        '- temporal_belief.csv, action_identity.csv, research_callbacks.csv, W13_118_overlap.csv.',
        '- dev/calibration.csv and dev/cp_equivalence.csv; CODE_FREEZE.json / CODE_FREEZE_COMMIT.txt.',
        '- not_reported.csv records unreportable entries; SUMMARY.json / INPUT_HASHES.csv / OUTPUTS.sha256.csv.',
        '- Public action logs stay in the method directories; no raw evidence bundle or pollution page text is exported.']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    a.write('OUTPUTS.sha256.csv',[dict(path=str(p.relative_to(OUT)),sha256=a.sha(p),bytes=p.stat().st_size)
        for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='OUTPUTS.sha256.csv' and 'solver_tmp' not in p.parts])
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
