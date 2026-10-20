"""Read-only W11 inputs and captured-state reconstruction; no agent execution."""
from __future__ import annotations
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import os
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARB=ROOT.parent/'arbiter'
OUT=ROOT/'reports/paperA/w11_attribution'
sys.path[:0]=[str(ARB/'src'),str(ARB/'scripts'),str(ROOT)]
from arbiter.agent.planning import BeliefPlanner, MechanismOperator, action_signature, resolved_params
from arbiter.evaluation.p6_execution import _claims, _compiler, _execution_policy
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_resource_nodes
from arbiter.evaluation.measurement_v3 import classify_actions_v3, intrinsic_discriminative
from arbiter.evaluation.oracle_baseline import oracle_aggregation
from arbiter.agent.aggregation import operator_from_assertion, parametric
import run_post_review_measurement_v2 as v2


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'))


def rows(path):
    with (gzip.open(path,'rt') if str(path).endswith('.gz') else path.open()) as f:
        for line in f:
            if line.strip():yield json.loads(line)


def read(path):
    with path.open(newline='') as f:return list(csv.DictReader(f))


def write(name,values,fields=None):
    path=OUT/name;path.parent.mkdir(parents=True,exist_ok=True)
    fields=fields or list(dict.fromkeys(k for r in values for k in r))
    if not fields:return
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(values)


def key(r):return tuple(r[k] for k in ('reading_mode','method','condition','world','task_id'))


def resources(stage):
    available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
    limit=Path('/sys/fs/cgroup/memory.max')
    if limit.exists() and limit.read_text().strip()!='max':
        available=min(available,int(limit.read_text())-int(limit.with_name('memory.current').read_text()))
    assert available>=10*1024**3,'STOP: less than plan 2 GiB plus 8 GiB'
    assert os.environ.get('CUDA_VISIBLE_DEVICES')=='','STOP: CUDA must be disabled'
    value={'stage':stage,'available_bytes':available,'planned_bytes':2*1024**3,'headroom_bytes':8*1024**3,'GPU_used':False}
    (OUT/'resources').mkdir(parents=True,exist_ok=True)
    (OUT/f'resources/{stage}.json').write_text(json.dumps(value,indent=2)+'\n')


class Reconstruction:
    def __init__(self,package_bundles=None):
        self.normalizer,_=_compiler(ROOT)
        self.tasks={r['id']:r for r in rows(ROOT/'data/tasks.jsonl')}
        self.catalog={r['id']:r for r in rows(ROOT/'data/conflicts.jsonl')}
        self.nodes=load_resource_nodes(ROOT/'harness/config/arena.json')
        self.policy=_execution_policy()
        prior=json.loads((ROOT/'release/package/assets/prior/frozen_prior_bundle.json').read_text())
        self.priorops=[operator_from_assertion(a) for a in prior['assertions']]
        self.priorsamples=prior['samples']
        self.poolcache={}
        self.package_bundles=package_bundles

    def inputs(self,rec):
        task=self.tasks[rec['task_id']]
        initial=Counter({r['name']:int(r['count']) for r in task['standard_items']+task.get('task_supplements',[])})
        method=rec['method']
        if method=='oracle':return (*oracle_aggregation(rec['world'],initial,ROOT),None)
        if method in {'parametric','probe_then_parametric'}:
            return self.priorops,parametric(self.priorsamples),None
        if self.package_bundles is None:
            bundle=v2.evidence(rec['reading_mode'],rec)
        else:
            bundle=self.package_bundles[(rec['reading_mode'],rec['condition'],rec['task_id'])]
            if method=='probe_then_rag' and rec.get('temporal_filter',{}).get('identified_version'):
                from arbiter.evaluation.p6_execution import _probe_then_rag_filter_bundle
                bundle=_probe_then_rag_filter_bundle(bundle,rec['temporal_filter']['identified_version'])
        cache=(rec['reading_mode'],rec['condition'],rec['task_id'],
               rec.get('temporal_filter',{}).get('identified_version'),method=='probe_then_rag')
        if cache not in self.poolcache:
            self.poolcache[cache]=_claims(bundle,normalizer=self.normalizer)[1]
        agg=v2.aggregate(method,bundle,self.normalizer)
        return self.poolcache[cache],agg,sha_for_bundle(bundle)

    def states(self,rec):
        operators,agg,bundle_hash=self.inputs(rec)
        traces=[t for t in rec['policy_trace'] if 'action_signature' in t]
        semantics=[i for i,e in enumerate(rec['events']) if e.get('method') in ACTION_METHODS]
        prefix=len(semantics)-len(traces)
        assert prefix in ({3,4} if rec['method']=='probe_then_rag' else {0}),('trace alignment',key(rec),prefix)
        if agg is None:
            assert not traces
            return
        belief=agg.belief
        trace_index=0;inventory=Counter();blocked=set();spent=0
        for semantic_index,event_index in enumerate(semantics):
            start=semantics[semantic_index-1]+1 if semantic_index else 0
            for e in rec['events'][start:event_index]:
                if e.get('method')=='inventory' and isinstance(e.get('result'),list):
                    inventory=Counter({r['name']:int(r['count']) for r in e['result']})
            if semantic_index<prefix:continue
            t=traces[trace_index];trace_index+=1
            mid=rec['used_mechanism_ids'][semantic_index]
            assert t['mechanism_id']==mid,('trace mid mismatch',key(rec),semantic_index)
            probability=belief.mechanism_probability(mid)
            assert abs(probability-t['probability_before'])<=1e-12,('probability reconstruction failed',key(rec),semantic_index,probability,t['probability_before'])
            weighted=[replace(op,probability=belief.mechanism_probability(op.mechanism_id)) for op in operators]
            planner=BeliefPlanner(weighted,inventory,uncertainty_weight=self.policy.uncertainty_weight,
                action_limit=self.policy.max_actions-spent-(prefix if prefix else 0),
                inventory_aware=self.policy.inventory_aware,blocked_actions=frozenset(blocked))
            yield {'semantic_index':semantic_index,'event_index':event_index,'trace':t,
                   'inventory':Counter(inventory),'planner':planner,'bundle_sha256':bundle_hash,
                   'belief_state':{'q':belief.q.tolist(),'phi':{k:v.tolist() for k,v in belief.phi.items()},
                                   'observations':dict(belief.observations or {})},
                   'explicit_probes_before':sum(x.get('intent')=='probe' for x in traces[:trace_index-1]),
                   'goal':self.tasks[rec['task_id']]['target']}
            if t['execution_strategy'] in {'arbiter','verify_all'} and not t['resource_failure']:
                belief.update(mid,t['observed'])
            if not t['observed']:blocked.add(t['action_signature'])
            spent+=int(rec['events'][event_index]['cost']['primary'])


def sha_for_bundle(bundle):return hashlib.sha256(canonical(bundle).encode()).hexdigest()


def fixture(state):
    p=state['planner']
    return {'operators':[op.__dict__ for op in p.operators], 'inventory':dict(p.initial),
            'uncertainty_weight':p.uncertainty_weight,'action_limit':p.action_limit,
            'inventory_aware':p.inventory_aware,'blocked_actions':sorted(p.blocked_actions),
            'goal':state['goal'],'n_plans':5,'semantic_index':state['semantic_index']}


def planner_from_fixture(value):
    operators=[]
    for r in value['operators']:
        r=dict(r);r['input_options']=tuple(tuple(x) for x in r['input_options'])
        r['requires']=tuple(r['requires']);r['layout_slots']=tuple(r['layout_slots'])
        operators.append(MechanismOperator(**r))
    return BeliefPlanner(operators,Counter(value['inventory']),
        uncertainty_weight=value['uncertainty_weight'],action_limit=value['action_limit'],
        inventory_aware=value['inventory_aware'],blocked_actions=frozenset(value['blocked_actions']))
