#!/usr/bin/env python3
"""Fixed-behavior attribution bounds and paired full-matrix contact tables."""
from __future__ import annotations
from collections import defaultdict
from itertools import combinations
import numpy as np
from w11_common import *
from arbiter.evaluation.measurement_v3 import event_inputs,predicted_products


def selector_key(op,params):
    return (op.method,op.output,op.output_count,op.action_cost,canonical(params),
            canonical(sorted(op.requires)),canonical(op.input_options),op.mechanism_id)


def main():
    resources('contact_audit');r=Reconstruction()
    cases=json.loads((OUT/'source_cases_second_pass.json').read_text())
    assert all(c['both_historical_selections_reproduced'] for c in cases),'STOP: root-cause source tests incomplete'
    reference={key(x):x for x in read(ROOT/'reports/paperA/w9_release_fix/replay/main_comparison.csv')}
    assert len(reference)==11220
    details=[];episodes=[];baseline_checks=0;states_checked=0;seen=set()
    intrinsic_cache={}
    def stale(mid,world):
        return mid in r.catalog and r.catalog[mid]['truth_vector'].get(world) is False and any(v is True for v in r.catalog[mid]['truth_vector'].values())
    for n,rec in enumerate(rows(ROOT/'release/package/assets/frozen/main.jsonl.gz'),1):
        k=key(rec);assert k in reference and k not in seen,('STOP: population mismatch',k)
        seen.add(k);sem=[i for i,e in enumerate(rec['events']) if e.get('method') in ACTION_METHODS]
        mids=rec['used_mechanism_ids'];assert len(sem)==len(mids)
        world=rec['world'];events=rec['events']
        stale_map={i:[mid] for i,mid in zip(sem,mids) if stale(mid,world)}
        annotations=classify_actions_v3(events,stale_by_event=stale_map,conflicts=r.catalog,resource_nodes=r.nodes,
            target=r.tasks[rec['task_id']]['target']['item'],episode_success=bool(rec['metrics']['SR']))
        baseline={a['event_index']:int(intrinsic_discriminative(events[a['event_index']],a['stale_mechanism_ids'],
            world=world,conflicts=r.catalog,resource_nodes=r.nodes) or bool(a['referenced_at'])) for a in annotations}
        actual_action=int(any(baseline.get(i,0) for i in sem if events[i]['method']!='check_name'))
        actual_query=int(any(baseline.get(i,0) for i in sem if events[i]['method']=='check_name'))
        assert actual_action==int(reference[k]['frozen_specific_action']) and actual_query==int(reference[k]['frozen_specific_query']),('STOP: original annotation mismatch',k)
        baseline_checks+=1
        lower=dict(baseline);upper=dict(baseline);repaired=dict(baseline)
        event_ties=0;label_sensitive=0
        for state in r.states(rec):
            states_checked+=1
            si,ei=state['semantic_index'],state['event_index'];trace=state['trace'];p=state['planner']
            method,params,expected=json.loads(trace['action_signature'])
            resolved=p._resolved()
            chosen=next(((op,choices) for op,choices in resolved if op.mechanism_id==mids[si]),None)
            assert chosen is not None,('STOP: selected operator not reconstructed',k,si,mids[si])
            selected,choices=chosen
            batch=int(params.get('n',1)) if method=='smelt' else 1
            assert action_signature(selected.method,resolved_params(selected,choices,batch),selected.output)==trace['action_signature'],('STOP: selected resolution mismatch',k,si)
            selected_coefficient=selected.action_cost-p.uncertainty_weight*math.log(max(selected.probability,1e-9))
            aliases=[];ties=[]
            for op,choices in resolved:
                if op.method!=method or op.output!=expected or op.output_count!=selected.output_count or op.action_cost!=selected.action_cost:continue
                if tuple(sorted(op.requires))!=tuple(sorted(selected.requires)):continue
                rp=resolved_params(op,choices,batch)
                if canonical(rp)!=canonical(params):continue
                required=Counter(choices)
                required=Counter({item:count*batch for item,count in required.items()})
                if method=='smelt':required[op.params.get('fuel','coal')]+=math.ceil(batch/8)
                if any(state['inventory'][item]<count for item,count in required.items()):continue
                if any(state['inventory'][item]<1 for item in op.requires):continue
                coefficient=op.action_cost-p.uncertainty_weight*math.log(max(op.probability,1e-9))
                aliases.append((op,rp,coefficient))
                if abs(coefficient-selected_coefficient)<=1e-12:ties.append((op,rp,coefficient))
            assert any(op.mechanism_id==selected.mechanism_id for op,_,_ in ties),('STOP: selected action not currently feasible',k,si)
            if len(aliases)<2:continue
            def specific(mid):
                if not stale(mid,world):return 0
                cache=(mid,world,canonical(events[ei].get('params',{})),method)
                if cache not in intrinsic_cache:
                    intrinsic_cache[cache]=intrinsic_discriminative(events[ei],[mid],world=world,conflicts=r.catalog,resource_nodes=r.nodes)
                predicted=predicted_products(r.catalog[mid]['assertion'])
                referenced=any(event_inputs(events[j]) & predicted for j in sem if j>ei)
                return int(intrinsic_cache[cache] or referenced)
            labels=[specific(op.mechanism_id) for op,_,_ in ties]
            assert specific(selected.mechanism_id)==baseline.get(ei,0),('STOP: alternative predicate differs',k,si)
            canonical_op=min(ties,key=lambda x:selector_key(x[0],x[1]))[0]
            if len(ties)>1:
                event_ties+=1;label_sensitive+=int(min(labels)!=max(labels))
                lower[ei]=min(labels);upper[ei]=max(labels);repaired[ei]=specific(canonical_op.mechanism_id)
            for op,rp,coeff in aliases:
                details.append({**dict(zip(('reading_mode','method','condition','world','task_id'),k)),
                    'semantic_index_0based':si,'event_index':ei,'selected_id':selected.mechanism_id,
                    'candidate_id':op.mechanism_id,'candidate_selected':int(op.mechanism_id==selected.mechanism_id),
                    'objective_coefficient':coeff,'selected_objective_coefficient':selected_coefficient,
                    'equal_objective':int(abs(coeff-selected_coefficient)<=1e-12),
                    'specific_action_predicate':specific(op.mechanism_id),'canonical_id':canonical_op.mechanism_id,
                    'catalogue_membership':int(op.mechanism_id in r.catalog),'params':canonical(rp),
                    'input_options':canonical(op.input_options)})
        def reduce(values,query=False):
            return int(any(values.get(i,0) for i in sem if (events[i]['method']=='check_name')==query))
        la,ua,fa=reduce(lower),reduce(upper),reduce(repaired)
        lq,uq,fq=reduce(lower,True),reduce(upper,True),reduce(repaired,True)
        assert la<=actual_action<=ua and lq<=actual_query<=uq
        episodes.append({**dict(zip(('reading_mode','method','condition','world','task_id'),k)),
            'specific_action':actual_action,'specific_query':actual_query,'specific_contact':int(actual_action or actual_query),
            'action_lower':la,'action_upper':ua,'contact_lower':int(la or lq),'contact_upper':int(ua or uq),
            'action_repaired':fa,'query_repaired':fq,'contact_repaired':int(fa or fq),
            'equivalent_tied_actions':event_ties,'label_sensitive_tied_actions':label_sensitive,
            'action_attribution_sensitive':int(la!=ua),'contact_attribution_sensitive':int((la or lq)!=(ua or uq))})
        if n%500==0:print(f'Validated and audited {n}/11220 frozen records',flush=True)
    assert seen==set(reference) and baseline_checks==11220,'STOP: incomplete population'
    write('candidate_attributions.csv',details)
    write('episode_attribution_sensitivity.csv',episodes)
    grouped=defaultdict(list)
    for e in episodes:
        for scope,condition,world in (('overall','all','all'),('condition_world',e['condition'],e['world'])):
            grouped[(e['reading_mode'],scope,condition,world)].append(e)
    tables=[];bounds=[];intervals=[]
    methods=sorted({e['method'] for e in episodes})
    assert len(methods)==10
    for scope,vs in sorted(grouped.items()):
        mode,kind,cond,world=scope;by=defaultdict(dict)
        for e in vs:by[e['method']][(e['task_id'],e['condition'],e['world'])]=e
        keys=sorted(by[methods[0]])
        assert all(sorted(by[m])==keys for m in methods),'STOP: unpaired full-matrix cells'
        assert len(keys)==(374 if kind=='overall' else 34)
        for method in methods:
            es=list(by[method].values());den=len(es)
            common={'reading_mode':mode,'scope':kind,'condition':cond,'world':world,'method':method,'denominator':den}
            for metric,col in (('specific_action','action'),('specific_contact','contact')):
                numerator=sum(e[metric] for e in es)
                tables.append({**common,'metric':metric,'events':numerator,'rate':numerator/den,
                               'event_flag':'no events' if numerator==0 else 'sparse (1-4)' if numerator<=4 else '5+ events'})
                bounds.append({**common,'metric':metric,'frozen_events':numerator,'frozen_rate':numerator/den,
                    'lower_events':sum(e[col+'_lower'] for e in es),'upper_events':sum(e[col+'_upper'] for e in es),
                    'lower_rate':sum(e[col+'_lower'] for e in es)/den,'upper_rate':sum(e[col+'_upper'] for e in es)/den,
                    'affected_episodes':sum(e[col+'_attribution_sensitive'] for e in es),
                    'repaired_events':sum(e[col+'_repaired'] for e in es),'repaired_rate':sum(e[col+'_repaired'] for e in es)/den,
                    'delta_repaired_pp':100*sum(e[col+'_repaired']-e[metric] for e in es)/den,
                    'analysis_label':'fixed-behavior attribution sensitivity; not new experimental performance'})
        tasks=sorted({k[0] for k in keys});assert len(tasks)==34
        means=np.array([[[np.mean([by[m][k][metric] for k in keys if k[0]==t])
                          for metric in ('specific_action','specific_contact')] for t in tasks] for m in methods])
        draws=np.random.default_rng(20261017).integers(0,34,size=(10000,34))
        boot=means[:,draws,:].mean(axis=2)
        for ai,bi in combinations(range(10),2):
            a,b=methods[ai],methods[bi]
            for mi,metric in enumerate(('specific_action','specific_contact')):
                delta=means[bi,:,mi].mean()-means[ai,:,mi].mean()
                lo,hi=np.quantile(boot[bi,:,mi]-boot[ai,:,mi],[.025,.975])
                pairs=[(by[a][k][metric],by[b][k][metric]) for k in keys]
                intervals.append({'reading_mode':mode,'scope':kind,'condition':cond,'world':world,
                    'method_A':a,'method_B':b,'metric':metric,'paired_instances':len(keys),'paired_tasks':34,
                    'A_events':sum(x for x,y in pairs),'B_events':sum(y for x,y in pairs),
                    'B_minus_A_pp':100*delta,'ci95_low_pp':100*lo,'ci95_high_pp':100*hi,
                    'A_only_events':sum(x and not y for x,y in pairs),'B_only_events':sum(y and not x for x,y in pairs),
                    'bootstrap_draws':10000,'bootstrap_seed':20261017,'unit':'paired task; configs averaged within task'})
    write('full_matrix_contact_counts.csv',tables)
    write('attribution_sensitivity_bounds.csv',bounds)
    write('full_matrix_paired_intervals.csv',intervals)
    # The exact old common-110 keys, not a newly selected subset.
    subset={(x['condition'],x['world'],x['task_id']) for x in read(ROOT/'reports/paperA/inputs/external_rescore_v2_episode_metrics.csv')}
    common=[]
    for m in methods:
        es=[e for e in episodes if e['reading_mode']=='all_public' and e['method']==m
            and (e['condition'],e['world'],e['task_id']) in subset]
        assert len(es)==110
        common.append({'method':m,'denominator':110,'frozen_events':sum(e['specific_contact'] for e in es),
                       'repaired_events':sum(e['contact_repaired'] for e in es),
                       'frozen_rate':sum(e['specific_contact'] for e in es)/110,
                       'repaired_rate':sum(e['contact_repaired'] for e in es)/110})
    write('common110_repaired_contact.csv',common)
    summary={'frozen_records':len(episodes),'original_annotations_validated':baseline_checks,'captured_states_checked':states_checked,
             'action_sensitive_episodes':sum(e['action_attribution_sensitive'] for e in episodes),
             'contact_sensitive_episodes':sum(e['contact_attribution_sensitive'] for e in episodes),
             'episodes_with_equivalent_tied_actions':sum(e['equivalent_tied_actions']>0 for e in episodes),
             'annotation_repaired_episodes':sum(e['action_repaired']!=e['specific_action'] or e['query_repaired']!=e['specific_query'] for e in episodes),
             'counts_rows':len(tables),'interval_rows':len(intervals),'new_episodes':0,'LLM_calls':0}
    (OUT/'CONTACT_SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
