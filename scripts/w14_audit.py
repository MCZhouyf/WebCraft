#!/usr/bin/env python3
"""Read-only W14 stage-zero audit; no policy or simulator is executed."""
from __future__ import annotations
import copy
import csv
import gzip
import hashlib
import json
import os
import sys
from dataclasses import asdict, replace
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT.parent/'arbiter/src'),
                str(ROOT.parent/'arbiter/scripts')]
import numpy as np
import w13_run as old
import run_ext_v2_cds_20261016 as cds
from conflict_core import recipe_assertion, accepts_layout
from arbiter.evaluation.p6_execution import _compiler, _claims, _action_role_metrics

OUT = ROOT/'reports/paperA/w14'
SEED = 20261017
INPUTS = {}


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576), b''): h.update(b)
    return h.hexdigest()


def read(p):
    INPUTS[str(p.relative_to(ROOT))] = sha(p)
    with (gzip.open(p,'rt') if str(p).endswith('.gz') else p.open()) as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def write(name, rows):
    rows=list(rows); p=OUT/name; p.parent.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for r in rows for k in r)) or ['status']
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fields,lineterminator='\n');w.writeheader();w.writerows(rows)


def encoded(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def digest(x): return hashlib.sha256(encoded(x).encode()).hexdigest()
def key(r): return (r['reading_mode'],r['condition'],r['world'],r['task_id'])


def load():
    tasks={r['id']:r for p in ('data/tasks.jsonl','data/ext_v2/ext_v2_tasks_20261016.jsonl')
           for r in read(ROOT/p)}
    peers=[]
    for scope in ('main','ext_v2','cds'):
        for r in read(ROOT/f'release/package/assets/frozen/{scope}.jsonl.gz'):
            if r['method']=='naive_rag': peers.append({**r,'scope':scope})
    for r in read(ROOT/'release/package/assets/frozen/cp_rag.jsonl.gz'):
        peers.append({**r,'scope':old.scope(r)})
    evidence={s:{(r['mode'],r['condition'],r['task_id']):r['bundle'] for r in
                 read(ROOT/f'release/package/assets/evidence/{s}.jsonl.gz')}
              for s in ('main','ext_v2','cds')}
    restricted=ROOT/'release/package/assets/restricted/evidence/main.jsonl.gz'
    if restricted.is_file():
        evidence['main'].update({(r['mode'],r['condition'],r['task_id']):r['bundle'] for r in read(restricted)})
    return tasks,peers,evidence


def effect(rows, field, cluster_equal=False):
    pairs=defaultdict(dict)
    for r in rows:pairs[(r['reading_mode'],r['condition'],r['world'],r['task_id'])][r['method']]=r
    groups=defaultdict(list)
    for pair in pairs.values():
        if set(pair)!={'naive_rag','cp_rag'}:continue
        a,b=pair['cp_rag'],pair['naive_rag']
        if a[field]=='' or b[field]=='':continue
        groups[a['unit']].append(float(a[field])-float(b[field]))
    units=sorted(groups)
    if not units:return {'n':0,'delta':'','ci95_low':'','ci95_high':''}
    nums=np.array([sum(groups[u]) for u in units]);dens=np.array([len(groups[u]) for u in units])
    if cluster_equal:nums=nums/dens;dens=np.ones(len(units))
    draws=np.random.default_rng(SEED).integers(0,len(units),(10000,len(units)))
    bs=nums[draws].sum(1)/dens[draws].sum(1)
    lo,hi=np.quantile(bs,[.025,.975])
    return dict(n=sum(len(v) for v in groups.values()),clusters=len(units),
                delta=float(nums.sum()/dens.sum()),ci95_low=float(lo),ci95_high=float(hi),
                bootstrap_draws=10000,seed=SEED,point_weighting='equal_cluster' if cluster_equal else 'episode')


class Grounded:
    """Static truth lookup for compiled concrete operators, evaluator only."""
    def __init__(self):
        self.truth={w:json.loads((ROOT/f'truth/{w}.json').read_text())
                    for w in ('1.16.5','1.19','1.21.11')}
        self.recipes={w:[a for r in t['recipes'] if (a:=recipe_assertion(r))]
                      for w,t in self.truth.items()}

    def valid(self,op,w):
        if op.method=='craft_grid':
            matches=[a for a in self.recipes[w] if a['station']=='crafting_grid'
                     and accepts_layout(a,op.params['layout'])]
            return any(a['output']==op.output and a['count']==op.output_count for a in matches)
        if op.method=='smelt':
            item=op.params.get('input',op.params.get('input_item'))
            return any(a['station']=='furnace' and a['output']==op.output and
                       item in a.get('ingredients',[[]])[0] and
                       int(a['count'])==op.output_count
                       for a in self.recipes[w])
        if op.method=='mine':
            matches=[r for r in self.truth[w]['drops']
                     if r['block'].removeprefix('minecraft:')==op.params['node_type']]
            if not matches:return None
            return op.output in {v.removeprefix('minecraft:') for r in matches for v in r.get('no_silk_touch_items',[])}
        if op.method=='check_name':
            return op.params['identifier'] in {v.removeprefix('minecraft:') for v in self.truth[w]['names']['items']}
        return None


def main():
    old.OUT=OUT; old.resource_gate('stage0')
    tasks,records,evidence=load()
    normalizer,_=_compiler(ROOT)
    catalog={r['id']:r for r in read(ROOT/'data/conflicts.jsonl')}
    vectors={k:r['truth_vector'] for k,r in catalog.items()}
    ground=Grounded()
    compiled={}; details=[]; k_actions=[]; s_success=[]; invariance=[]
    private={}
    for p in sorted((ROOT/'reports/paperA/ext_v2/cds_evaluator').glob('*.json')):
        private[p.stem]=json.loads(p.read_text());INPUTS[str(p.relative_to(ROOT))]=sha(p)
    for s,ee in evidence.items():
        if s!='cds':continue
        by=defaultdict(list)
        for (m,c,t),b in ee.items():
            derived,ops=_claims(b,normalizer=normalizer)
            compiled[(m,c,t)]={op.mechanism_id:op for op in ops}
            by[(m,c.rsplit('__',1)[0],t)].append((c.rsplit('__',1)[-1],b,ops))
        for (mode,instance,task),bb in sorted(by.items()):
            for condition,b,ops in bb:
                invariance.append(dict(instance_id=instance,task_id=task,condition=condition,
                    claims=len(b['claims']),claims_hash=digest(b['claims']),hits_hash=digest(b['search_rows']),
                    operator_payload_hash=digest(sorted(((op.method,op.params,op.output,op.output_count) for op in ops),key=encoded)),
                    operator_count=len(ops),mechanisms=encoded(sorted(op.mechanism_id for op in ops))))
    for r in records:
        s=r['scope'];role=_action_role_metrics(r['events'],r['used_mechanism_ids'],r['world'],vectors)
        row=dict(scope=s,reading_mode=r['reading_mode'],condition=r['condition'],world=r['world'],
                 task_id=r['task_id'],method=r['method'],unit=tasks[r['task_id']].get('independent_family_id',r['task_id']),
                 archived=int(r['metrics']['HTR_commit']),global_catalogue=role['HTR_commit'],
                 W13_recomputed='',target_grounded='',grounded_unknown_actions=0,
                 SR=int(r['metrics']['SR']),used_mechanism_ids=encoded(r['used_mechanism_ids']))
        if s=='cds':
            instance,condition=r['condition'].rsplit('__',1);meta=private[instance]
            synthetic=copy.deepcopy(vectors)
            for c in meta['boundary_truth']:
                if c['mechanism_id'] in synthetic:
                    synthetic[c['mechanism_id']]=dict.fromkeys(('1.16.5','1.19','1.21.11'),bool(c['truth']))
                    synthetic[c['mechanism_id']][meta['comparison_world']]=not bool(c['truth'])
            row['W13_recomputed']=_action_role_metrics(r['events'],r['used_mechanism_ids'],r['world'],synthetic)['HTR_commit']
            bundle=evidence[s][(r['reading_mode'],r['condition'],r['task_id'])]
            ops=compiled[(r['reading_mode'],r['condition'],r['task_id'])]
            semantic=[e for e in r['events'] if e['method'] in ('mine','craft_grid','smelt','check_name')]
            false_commit=False;unknown=False;invalid=[]
            for i,(mid,event) in enumerate(zip(r['used_mechanism_ids'],semantic)):
                op=ops.get(mid)
                # Concrete action parameters, not the operator's default option,
                # determine whether an alternative-ingredient execution is valid.
                actual=replace(op,params={**op.params,**event['params']}) if op else None
                truth=ground.valid(actual,r['world']) if actual else None
                if truth is None:row['grounded_unknown_actions']+=1
                ever_true=any(ground.valid(actual,v) is True for v in ground.truth) if actual else False
                if event.get('intent')!='probe':
                    false_commit|=truth is False and ever_true;unknown|=truth is None
                if (mid in synthetic and synthetic[mid].get(r['world']) is False) or truth is False:
                    invalid.append(mid)
                    if condition=='K':
                        k_actions.append(dict(method=r['method'],instance_id=instance,world=r['world'],
                            task_id=r['task_id'],semantic_step=i+1,mechanism_id=mid,intent=event.get('intent'),
                            op=encoded(asdict(op)) if op else '',event=encoded(event),
                            global_vector=encoded(vectors.get(mid)),W13_vector=encoded(synthetic.get(mid)),
                            target_operator_valid=truth,valid_in_some_world=ever_true,
                            comparison_operator_valid=ground.valid(actual,meta['comparison_world']) if actual else '',
                            supporting_claims=encoded([c for c in bundle['claims'] if mid in
                              {x.mechanism_id for x in _claims({**bundle,'claims':[c]},normalizer=normalizer)[1]}])))
            row['target_grounded']=int(false_commit) if false_commit or not unknown else ''
            if condition=='S' and r['metrics']['SR']:
                s_success.append(dict(method=r['method'],instance_id=instance,task_id=r['task_id'],world=r['world'],
                    used=encoded(r['used_mechanism_ids']),used_present_in_bundle=all(mid in ops for mid in r['used_mechanism_ids']),
                    true_feasible=r['true_feasible'],reported_feasible=r['reported_feasible'],
                    target_grounded=row['target_grounded'],invalid_W13_ids=encoded(invalid)))
        details.append(row)
    write('stage0/episode_acceptance.csv',details);write('stage0/K_tagged_actions.csv',k_actions)
    write('stage0/S_successes.csv',s_success);write('stage0/CDS_evidence_hashes.csv',invariance)
    effects=[];rates=[]
    for scope in ('main','ext_v2','cds'):
        for mode in sorted({r['reading_mode'] for r in details if r['scope']==scope}):
            rr=[r for r in details if r['scope']==scope and r['reading_mode']==mode]
            for field in ('archived','global_catalogue','W13_recomputed','target_grounded'):
                selected=[r for r in rr if r[field]!='']
                if not selected:continue
                for method in ('naive_rag','cp_rag'):
                    vals=[r[field] for r in selected if r['method']==method]
                    rates.append(dict(scope=scope,reading_mode=mode,definition=field,method=method,
                                      events=sum(vals),n=len(vals),rate=sum(vals)/len(vals)))
                for equal in (False,True):
                    effects.append(dict(scope=scope,reading_mode=mode,definition=field,
                                        **effect(selected,field,cluster_equal=equal)))
    write('stage0/acceptance_rates.csv',rates);write('stage0/acceptance_effects.csv',effects)
    restricted=json.loads((ROOT/'release/package/docs/RESTRICTED_FILES.json').read_text())
    asset_rows=[]
    for x in restricted:
        p=ROOT/'release/package'/x['path']
        asset_rows.append(dict(path=x['path'],present=p.is_file(),bytes=p.stat().st_size if p.is_file() else '',
                               hash_matches=p.is_file() and sha(p)==x['sha256']))
    for name in ('synthetic_pollution.tar.gz.enc','w9_replay_restricted.tar.gz.enc'):
        p=ROOT/'release/package/corpus/encrypted'/name
        asset_rows.append(dict(path=str(p.relative_to(ROOT/'release/package')),present=p.is_file(),
                               bytes=p.stat().st_size if p.is_file() else '',sha256=sha(p) if p.is_file() else ''))
    write('stage0/restricted_asset_check.csv',asset_rows)
    grouped=defaultdict(list)
    for r in invariance:grouped[r['instance_id']].append(r)
    summary=dict(LLM_calls=0,new_episodes=0,CDS_instances=len(grouped),
        CDS_distinct_claim_payload_counts=dict(Counter(len({x['operator_payload_hash'] for x in rows}) for rows in grouped.values())),
        K_tagged_episodes_by_method=dict(Counter(x['method'] for x in { (r['method'],r['instance_id']):r for r in k_actions}.values())),
        K_tagged_action_truth=dict(Counter(str(x['target_operator_valid']) for x in k_actions)),
        restricted_evidence_keys=sum(c=='polluted' for m,c,t in evidence['main']),
        private_asset_hashes_match=all(r['hash_matches'] for r in asset_rows if 'hash_matches' in r),
        ground_unknown_CDS_episodes=sum(r['target_grounded']=='' for r in details if r['scope']=='cds'))
    (OUT/'stage0/SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    write('stage0/INPUT_HASHES.csv',[dict(path=k,sha256=v) for k,v in sorted(INPUTS.items())])
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
