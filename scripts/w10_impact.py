#!/usr/bin/env python3
"""Counterfactual reaggregation of W9's nine differences, without running agents."""
from __future__ import annotations
import copy
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / 'reports/paperA'
OUT = PAPER / 'w10_release_final/impact'
W9 = PAPER / 'w9_release_fix/replay'
ARB = ROOT.parent / 'arbiter'
sys.path[:0] = [str(ARB/'src'), str(ARB/'scripts'), str(ROOT)]
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_resource_nodes
from arbiter.evaluation.measurement_v3 import classify_actions_v3
from arbiter.evaluation.post_review_measurement import stable_identification_cost
from arbiter.evaluation import p6_matrix as matrixmod


def read(path):
    with path.open(newline='') as f: return list(csv.DictReader(f))


def write(name, rows):
    if not rows: return
    path = OUT/name
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        w.writeheader(); w.writerows(rows)


def records(path):
    with (gzip.open(path, 'rt') if path.suffix == '.gz' else path.open()) as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def key(r):
    return tuple(r[k] for k in ('reading_mode','method','condition','world','task_id'))


def extras(r):
    status, ic = stable_identification_cost(r['events'], hashlib.sha256(r['world'].encode()).hexdigest())
    return {'probe_count': float(r['probe_count']), 'probe_cost': float(r['probe_cost']),
            'IC_identified': int(status == 'identified'), 'IC_cost': ic,
            'reported_feasible': r['reported_feasible'], 'true_feasible': r['true_feasible']}


def signatures(r):
    return [json.dumps({'method':e['method'],'params':e.get('params',{}),'intent':e.get('intent')},sort_keys=True)
            for e in r['events'] if e.get('method') in ACTION_METHODS]


def observations(r):
    result = {}
    for t in r.get('policy_trace',[]):
        mid = t.get('mechanism_id', t.get('separator'))
        if isinstance(mid,str) and isinstance(t.get('observed'),bool) and not t.get('resource_failure',False):
            result[mid] = t['observed']
    return result


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    comparisons = read(W9/'main_comparison.csv')
    changed = {key(r):r for r in comparisons if r['status']=='metric_difference'}
    assert len(changed)==9 and len(comparisons)==11220
    base, new = {}, {}
    for r in records(ROOT/'release/package/assets/frozen/main.jsonl.gz'):
        k = key(r)
        if k in changed: base[k]=r
    for r in records(W9/'main_replayed.jsonl'):
        k = key(r)
        if k in changed: new[k]=r
    assert set(base)==set(new)==set(changed)
    before_extra = {}
    for r in records(ROOT/'release/package/assets/frozen/main.jsonl.gz'):
        before_extra[key(r)] = extras(r)
    after_extra = {**before_extra, **{k:extras(r) for k,r in new.items()}}
    tasks = {r['id']:r for r in records(ROOT/'data/tasks.jsonl')}
    catalog = {r['id']:r for r in records(ROOT/'data/conflicts.jsonl')}
    nodes = load_resource_nodes(ROOT/'harness/config/arena.json')
    details, trace_evidence, label_impacts = [], [], []
    for k,row in changed.items():
        a,b = base[k],new[k]
        left,right = signatures(a),signatures(b)
        first = next((i for i in range(min(len(left),len(right))) if left[i]!=right[i]),min(len(left),len(right)))
        same_multi = Counter(a['used_mechanism_ids'])==Counter(b['used_mechanism_ids'])
        reason = ('Different selected probe/action path and semantic cost; source-level tie-breaking cause is not established'
                  if 'semantic_cost' in row['changed_metrics'] else
                  'Different dependency/resource action order changes the scorer\'s later reference witness for a stale predicted product; terminal and cost unchanged')
        if left==right:
            reason=('Identical public actions: the stone_pickaxe operator attribution at semantic step 3 changes '
                    'from catalogued cf_0cf08e1cf6803d3f to uncatalogued cf_b55d59dd1aaa0ede. '
                    'The stale-map classifier drops this annotation; no behavioral change. '
                    'The source-level selector tie cause is not established.')
        details.append({**{n:row[n] for n in ('reading_mode','method','condition','world','task_id')},
                        'metrics':row['changed_metrics'],
                        'frozen_values':json.dumps({m:row['frozen_'+m] for m in row['changed_metrics'].split(';')},sort_keys=True),
                        'replay_values':json.dumps({m:row['replayed_'+m] for m in row['changed_metrics'].split(';')},sort_keys=True),
                        'first_different_semantic_action':first,'mechanism_multiset_equal':int(same_multi),
                        'public_actions_identical':int(left==right),
                        'reason':reason})
        def annotate(record):
            indexes=[i for i,e in enumerate(record['events']) if e.get('method') in ACTION_METHODS]
            stale={i:[m] for i,m in zip(indexes,record['used_mechanism_ids']) if m in catalog
                   and catalog[m]['truth_vector'].get(record['world']) is False
                   and any(v is True for v in catalog[m]['truth_vector'].values())}
            return classify_actions_v3(record['events'],stale_by_event=stale,conflicts=catalog,
                                       resource_nodes=nodes,target=tasks[record['task_id']]['target'],
                                       episode_success=bool(record['metrics']['SR']))
        aa,bb=annotate(a),annotate(b)
        def count_labels(ann,rec):
            return Counter((r['v3_label'],int(rec['events'][r['event_index']].get('intent')!='probe')) for r in ann)
        ca,cb=count_labels(aa,a),count_labels(bb,b)
        label_impacts.append({**{n:row[n] for n in ('reading_mode','method','condition','world','task_id')},
                              'frozen_label_plan_counts':json.dumps({f'{x[0]}/plan{x[1]}':v for x,v in ca.items()},sort_keys=True),
                              'replay_label_plan_counts':json.dumps({f'{x[0]}/plan{x[1]}':v for x,v in cb.items()},sort_keys=True),
                              'confusion_counts_changed':int(ca!=cb),
                              'v3_lower_changed':int(any(x['v3_label']=='commit' for x in aa)!=any(x['v3_label']=='commit' for x in bb)),
                              'v3_upper_changed':int(any(x['v3_label'] in {'commit','undetermined'} for x in aa)!=any(x['v3_label'] in {'commit','undetermined'} for x in bb)),
                              'IAA_observation_inputs_changed':int(observations(a)!=observations(b)),
                              'IAA_query_claims_changed':int(a['conflict_mechanism_ids']!=b['conflict_mechanism_ids']),
                              'IAA_temporal_filter_changed':int(a.get('temporal_filter')!=b.get('temporal_filter'))})
        trace_evidence.append({'key':k,'first_divergence':first,'frozen_actions':left,'replay_actions':right,
                               'frozen_mechanisms':a['used_mechanism_ids'],'replay_mechanisms':b['used_mechanism_ids'],
                               'frozen_annotations':aa,'replay_annotations':bb,
                               'frozen_extra':before_extra[k],'replay_extra':after_extra[k]})
    write('difference_instances.csv',details)
    write('v3_IAA_invariants.csv',label_impacts)
    (OUT/'trace_evidence.json').write_text(json.dumps(trace_evidence,indent=2,sort_keys=True)+'\n')
    (OUT/'nine_episode_pairs.json').write_text(json.dumps([{'key':k,'frozen':base[k],'replayed':new[k]} for k in changed],sort_keys=True)+'\n')
    skinny_before, skinny_after = {},{}
    for r in comparisons:
        k=key(r)
        values=[]
        for prefix,ex in (('frozen_',before_extra),('replayed_' if k in changed else 'frozen_',after_extra)):
            v={n:float(r[prefix+n]) for n in ('SR','overconservative','execution_failure','plan_acceptance',
                   'stale_contact','specific_query','specific_action','semantic_actions','semantic_cost','search_calls')}
            v.update(ex[k]); v['specific_contact']=int(v['specific_query'] or v['specific_action'])
            v['feasible_SR']=v['SR'] if v['true_feasible'] else None
            v['overconservative']=v['overconservative'] if v['true_feasible'] else None
            v['execution_failure']=v['execution_failure'] if v['true_feasible'] else None
            values.append(v)
        skinny_before[k],skinny_after[k]=values
    metrics=('SR','plan_acceptance','stale_contact','specific_query','specific_action','specific_contact',
             'overconservative','execution_failure','feasible_SR','semantic_actions','semantic_cost','search_calls',
             'probe_count','probe_cost','IC_identified','IC_cost')
    grouped=defaultdict(list)
    coverage=matrixmod.coverage_conflict_ids(ROOT/'reports/corpus_stats/coverage_p1.csv','one_sided')
    subset_keys={(r['condition'],r['world'],r['task_id']) for r in read(PAPER/'inputs/external_rescore_v2_episode_metrics.csv')}
    assert len(subset_keys)==110
    for k in skinny_before:
        mode,method,condition,world,tid=k
        broad='matched' if condition.startswith('matched_') else condition
        selections=[('overall','all'),('condition',condition),('condition_group',broad),
                    ('world',world),('condition_world',condition+'|'+world),('task_type',tasks[tid]['type'])]
        if not tasks[tid]['conflict_ids']:selections.append(('subset','control'))
        else:
            mids=set(tasks[tid]['conflict_ids'])
            selections.append(('subset','one_sided' if mids & coverage else 'two_sided'))
        if mode=='all_public' and (condition,world,tid) in subset_keys: selections.append(('common110','all'))
        for scope,group in selections: grouped[(mode,method,scope,group)].append(k)
    aggregates=[]
    for (mode,method,scope,group),keys in sorted(grouped.items()):
        for m in metrics:
            av=[skinny_before[k][m] for k in keys if skinny_before[k][m] is not None]
            bv=[skinny_after[k][m] for k in keys if skinny_after[k][m] is not None]
            a=sum(av)/len(av) if av else None; b=sum(bv)/len(bv) if bv else None
            aggregates.append({'reading_mode':mode,'method':method,'scope':scope,'group':group,'metric':m,
                               'denominator':len(av),'counterfactual_denominator':len(bv),
                               'frozen':a,'counterfactual':b,
                               'frozen_3dp':'' if a is None else f'{a:.3f}',
                               'counterfactual_3dp':'' if b is None else f'{b:.3f}',
                               'delta_pp_1dp':'' if a is None or b is None else f'{100*(b-a):.1f}',
                               'rounded_changed':int((None if a is None else f'{a:.3f}')!=(None if b is None else f'{b:.3f}'))})
    write('all_main_aggregates.csv',aggregates)
    write('changed_rounded_aggregates.csv',[r for r in aggregates if r['rounded_changed']])
    index={(r['reading_mode'],r['method'],r['scope'],r['group'],r['metric']):r for r in aggregates}
    alias={'SR':'SR','HTR_commit':'plan_acceptance','HTR_all':'stale_contact',
           'probe_count':'probe_count','probe_cost':'probe_cost','mean_semantic_actions':'semantic_actions',
           'mean_semantic_cost':'semantic_cost','primary_cost':'semantic_cost',
           'mean_search_calls':'search_calls','mean_searches':'search_calls','mean_probe_labels':'probe_count',
           'overconservatism':'overconservative','over_conservatism_rate':'overconservative',
           'execution_failure':'execution_failure','execution_failure_rate':'execution_failure',
           'identification_rate':'IC_identified','IC_mean_identified':'IC_cost','IC':'IC_cost',
           'HTR_all_discriminative':'specific_contact','discriminative_stale_action':'specific_action',
           'discriminative_stale_query':'specific_query'}
    cells=[]
    files=[*PAPER.glob('S7*.csv'),PAPER/'w5/table2_real_costs.csv',PAPER/'inputs/a3/unified_110.csv']
    for path in files:
        for rownum,r in enumerate(read(path),2):
            method=r.get('method'); mode=r.get('reading_mode','llm_only' if 'llm_only' in path.name else 'all_public')
            mode={'gold_path_reading':'oracle_reading'}.get(mode,mode)
            if not method or method=='always_abstain': continue
            scope,group='overall','all'
            if path.name=='unified_110.csv': scope,group='common110','all'
            elif r.get('subset') and r['subset']!='all':scope,group='subset',r['subset']
            elif r.get('condition') and r.get('world'): scope,group='condition_world',r['condition']+'|'+r['world']
            elif r.get('condition'): scope,group='condition_group',r['condition']
            elif r.get('world'): scope,group='world',r['world']
            elif r.get('task_type'): scope,group='task_type',r['task_type']
            for col,m in alias.items():
                if col not in r or r[col] in ('','NA','nan'):continue
                a=index.get((mode,method,scope,group,m))
                if not a:continue
                try: published=float(r[col])
                except ValueError:continue
                matches=abs(published-float(a['frozen']))<1e-5 if a['frozen'] is not None else False
                cells.append({'source':str(path.relative_to(ROOT)),'row':rownum,'column':col,
                              'reading_mode':mode,'method':method,'scope':scope,'group':group,
                              'reported_original':r[col],'computed_frozen':a['frozen'],'counterfactual':a['counterfactual'],
                              'original_3dp':f'{published:.3f}','counterfactual_3dp':a['counterfactual_3dp'],
                              'baseline_matches':int(matches),
                              'rounded_changed':int(f'{published:.3f}'!=a['counterfactual_3dp']) if matches else 'unmapped_baseline'})
            for col,m in (('total_semantic_actions','semantic_actions'),('total_searches','search_calls')):
                if col not in r or not r.get('episodes'):continue
                a=index.get((mode,method,scope,group,m))
                if not a:continue
                av=float(a['frozen'])*int(r['episodes']);bv=float(a['counterfactual'])*int(r['episodes'])
                cells.append({'source':str(path.relative_to(ROOT)),'row':rownum,'column':col,
                              'reading_mode':mode,'method':method,'scope':scope,'group':group,
                              'reported_original':r[col],'computed_frozen':av,'counterfactual':bv,
                              'original_3dp':f'{float(r[col]):.3f}','counterfactual_3dp':f'{bv:.3f}',
                              'baseline_matches':int(abs(float(r[col])-av)<1e-5),
                              'rounded_changed':int(f'{av:.3f}'!=f'{bv:.3f}')})
    write('reported_cells.csv',cells)
    write('changed_reported_cells.csv',[r for r in cells if r['rounded_changed']==1])
    replacements={(r['source'],r['row'],r['column']):r for r in cells if r['baseline_matches']}
    for path in files:
        values=read(path)
        for line,r in enumerate(values,2):
            for col in r:
                cell=replacements.get((str(path.relative_to(ROOT)),line,col))
                if cell:r[col]=cell['counterfactual']
        write('counterfactual_'+path.name,values)
    from metrics.episode import episode_metrics
    optional=[]
    for k,oldrec in base.items():
        task=tasks[k[4]];scores=[]
        for rec in (oldrec,new[k]):
            semantic=[i for i,e in enumerate(rec['events']) if e.get('method') in ACTION_METHODS]
            vectors={i:catalog[mid]['truth_vector'] for i,mid in zip(semantic,rec['used_mechanism_ids']) if mid in catalog}
            initial=Counter({r['name']:int(r['count']) for r in task['standard_items']+task.get('task_supplements',[])})
            scores.append(episode_metrics(rec['events'],goal=task['target'],world=rec['world'],
                true_feasible=rec['true_feasible'],success=bool(rec['metrics']['SR']),
                reported_feasible=rec['reported_feasible'],version_hashes={},mechanism_vectors=vectors,
                oracle_cost=task['oracle_cost'][rec['world']],task_type=task['type'],initial_inventory=initial))
        optional.append({'reading_mode':k[0],'method':k[1],'condition':k[2],'world':k[3],'task_id':k[4],
                         **{f'{p}_{m}':s[m] for p,s in zip(('frozen','counterfactual'),scores)
                            for m in ('OPR','OVR','OVR_numerator','OVR_denominator')},
                         'OPR_or_OVR_changed':int(any(scores[0][m]!=scores[1][m] for m in ('OPR','OVR','OVR_numerator','OVR_denominator'))),
                         'reported_feasibility_changed':int(oldrec['reported_feasible']!=new[k]['reported_feasible'])})
    write('OPR_OVR_feasibility_checks.csv',optional)
    # Reuse the registered implementation, including its shared RNG and cluster weights.
    discr=load_module('w10_discrimination',PAPER/'discrimination_audit/build_discrimination_audit_v2.py')
    original=discr.load_records(); replaced=copy.deepcopy(original)
    for k in changed:
        mode,method,cond,world,tid=k
        row=replaced[(mode,method)][(cond,world,tid)]
        for m in ('probe_count','probe_cost'):row[m]=after_extra[k][m]
    clusters,_=discr.load_clusters()
    old_matrix,old_summary=discr.build_v2_matrix(original,clusters)
    new_matrix,new_summary=discr.build_v2_matrix(replaced,clusters)
    write('discrimination_counterfactual.csv',new_matrix)
    interval_changes=[]
    for a,b in zip(old_matrix,new_matrix):
        for col in a:
            if isinstance(a[col],(float,int)) and isinstance(b[col],(float,int)) and f'{a[col]:.3f}'!=f'{b[col]:.3f}':
                interval_changes.append({'reading_mode':a['reading_mode'],'method_a':a['method_a'],'method_b':a['method_b'],
                                         'column':col,'frozen':a[col],'counterfactual':b[col],
                                         'frozen_3dp':f'{a[col]:.3f}','counterfactual_3dp':f'{b[col]:.3f}'})
    write('changed_discrimination_cells.csv',interval_changes)
    # Re-score the same nine records under A3's frozen classifier and arguments.
    a3=load_module('w10_a3',ARB/'scripts/run_measurement_v3_20261014.py')
    a3root=ARB/'reports/main/post_review/measurement_v3'
    eps=read(a3root/'pipeline_episodes_v3.csv')
    acts=read(a3root/'pipeline_actions_v3.csv')
    external=read(a3root/'external_episodes_v3.csv')
    for r in eps+external:
        for name in ('seed','HTR_commit_c_v3_lower','HTR_commit_c_v3_upper',
                     'HTR_commit_c_v2_rule_iii_includes_infeasible','HTR_commit_plan_source',
                     'HTR_all','HTR_all_discriminative','stale_actions','discriminative_stale_actions','semantic_actions'):
            if r.get(name,'')!='':r[name]=int(float(r[name]))
    for r in acts:
        r['plan_source_commit']=int(r['plan_source_commit'])
        r['seed']=int(r['seed'])
    patched_eps=copy.deepcopy(eps)
    patched_acts=[r for r in acts if key(r) not in changed]
    for r in patched_eps:
        k=key(r)
        if k not in changed:continue
        rec=new[k]; ann=annotate(rec)
        a3.annotate_discriminative(ann,rec['events'],rec['world'],catalog,nodes)
        r.update(HTR_commit_c_v3_lower=int(any(x['v3_label']=='commit' for x in ann)),
                 HTR_commit_c_v3_upper=int(any(x['v3_label'] in {'commit','undetermined'} for x in ann)),
                 HTR_all_discriminative=int(any(x['discriminative'] for x in ann)),
                 stale_actions=len(ann),discriminative_stale_actions=sum(x['discriminative'] for x in ann),
                 semantic_actions=int(skinny_after[k]['semantic_actions']))
        for x in ann:
            annotation={**x,'action_method':x['method']}
            del annotation['method']
            patched_acts.append({**annotation,**{n:r[n] for n in ('setting','reading_mode','method','condition','world','task_id','seed')},
                                 'plan_source_commit':int(rec['events'][x['event_index']].get('intent')!='probe')})
    old_unified,old_effects=a3.unified_110(eps,external)
    new_unified,new_effects=a3.unified_110(patched_eps,external)
    accepted={r['method']:r for r in read(PAPER/'inputs/a3/unified_110.csv')}
    for row in old_unified:
        for name,value in row.items():
            if name=='method' or value=='':continue
            assert abs(float(value)-float(accepted[row['method']][name]))<1e-12,('A3 baseline mismatch',row['method'],name)
    write('unified110_counterfactual.csv',new_unified)
    write('unified110_effects_counterfactual.csv',new_effects)
    old_conf,old_conf_summary,_=a3.confusion_outputs(acts,eps)
    new_conf,new_conf_summary,_=a3.confusion_outputs(patched_acts,patched_eps)
    write('confusion_counterfactual.csv',new_conf)
    write('confusion_summary_counterfactual.csv',new_conf_summary)
    old_full,new_full=a3.pipeline_full(eps),a3.pipeline_full(patched_eps)
    write('pipeline_full_v3_counterfactual.csv',new_full)
    opportunities=[]
    for episodes,actions in ((eps,acts),(patched_eps,patched_acts)):
        values=a3.opportunity_rows(episodes,'pipeline')
        a3.fill_first_stale(values,actions)
        opportunities.append(a3.opportunity_summary(values))
    write('opportunity_counterfactual.csv',opportunities[1])
    a3_changes=[]
    for table,left,right,ids in (('unified110',old_unified,new_unified,('method',)),
                               ('unified110_effects',old_effects,new_effects,('method','metric')),
                               ('confusion',old_conf,new_conf,('level','reading_mode','method','v3_label','plan_source_commit')),
                               ('confusion_summary',old_conf_summary,new_conf_summary,('level','reading_mode','method')),
                               ('pipeline_full',old_full,new_full,('reading_mode','method')),
                               ('opportunity',opportunities[0],opportunities[1],('agent_family','setting','reading_mode','method'))):
        for a,b in zip(left,right):
            for col in a:
                if col in ids:continue
                if a[col]==b[col]:continue
                try:av,bv=float(a[col]),float(b[col])
                except (ValueError,TypeError):
                    av,bv=a[col],b[col]
                if isinstance(av,float) and isinstance(bv,float) and f'{av:.3f}'==f'{bv:.3f}':continue
                a3_changes.append({'table':table,**{n:a[n] for n in ids},'column':col,
                                   'frozen':av,'counterfactual':bv,
                                   'frozen_reported':f'{av:.1f}' if col.endswith('_pp') and isinstance(av,float) else f'{av:.3f}' if isinstance(av,float) else av,
                                   'counterfactual_reported':f'{bv:.1f}' if col.endswith('_pp') and isinstance(bv,float) else f'{bv:.3f}' if isinstance(bv,float) else bv})
    write('changed_a3_reported_cells.csv',a3_changes)
    # IAA uses an unchanged evidence aggregation plus the last public observation.
    v2=load_module('w10_measurement_v2',ARB/'scripts/run_post_review_measurement_v2.py')
    normalizer,_=v2._compiler(ROOT)
    iaa_changes=[]; iaa_checks=[]
    for k,rec in base.items():
        bundle=v2.evidence(k[0],rec)
        result=v2.aggregate(k[1],bundle,normalizer)
        oldobs,newobs=observations(rec),observations(new[k])
        for claim in rec['conflict_mechanism_ids']:
            def answer(obs):
                if claim in obs:return obs[claim]
                return result.belief.mechanism_probability(claim)>=0.5 if result is not None and claim in result.belief.phi else 'unknown'
            a,b=answer(oldobs),answer(newobs)
            item={'reading_mode':k[0],'method':k[1],'condition':k[2],'world':k[3],'task_id':k[4],
                  'claim_id':claim,'frozen_answer':a,'counterfactual_answer':b,'answer_changed':int(a!=b)}
            iaa_checks.append(item)
            if a!=b:iaa_changes.append(item)
    write('IAA_query_checks.csv',iaa_checks)
    write('IAA_answer_changes.csv',iaa_changes)
    draft=(ROOT/'paper/PAPER_A_DRAFT.md').read_text()
    inventory=[]; heading=''
    for n,line in enumerate(draft.splitlines(),1):
        if line.startswith('#'):heading=line
        if any(c.isdigit() for c in line) and (line.startswith('|') or line.startswith('**RQ') or heading.startswith('## Appendix')):
            inventory.append({'line':n,'section':heading,'text':line,
                              'disposition':'main result/appendix sensitivity in reported_cells and discrimination outputs' if ('7 ' in heading or 'Appendix D' in heading) else
                                            'unchanged structural/independent evidence, or mapped in source table inventory'})
    write('draft_numeric_inventory.csv',inventory)
    common=[r for r in aggregates if r['scope']=='common110' and r['metric']=='specific_contact']
    write('common110_discriminative_contact.csv',common)
    summary={'difference_instances':9,'metric_equal_action_only_cases':75,'reported_cells_checked':len(cells),
             'reported_rounded_changes':sum(r['rounded_changed']==1 for r in cells),
             'reported_baseline_unmapped':sum(r['rounded_changed']=='unmapped_baseline' for r in cells),
             'discrimination_changed_rounded_cells':len(interval_changes),
             'a3_changed_reported_cells':len(a3_changes),'IAA_answers_changed':len(iaa_changes),
             'discrimination_old_summary':old_summary,'discrimination_counterfactual_summary':new_summary,
             'v3_IAA_checks':label_impacts,
             'limitations':['Only paper/PAPER_A_DRAFT.md is available; the requested Table 2/3/4 numbering is not the same as this English draft. Table2 real costs and v13 primary Table4 are audited by source name. No Chinese manuscript or external Word Table3 was silently assumed.'],
             'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              (ROOT/'paper/PAPER_A_DRAFT.md',W9/'main_comparison.csv',ROOT/'data/tasks.jsonl')}}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'v3_IAA_checks','source_hashes','discrimination_old_summary','discrimination_counterfactual_summary'}},sort_keys=True))


if __name__=='__main__':main()
