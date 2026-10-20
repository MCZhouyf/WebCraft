#!/usr/bin/env python3
"""Public-only W14 worker. Frozen CP policy is loaded by AST, not reimplemented."""
from __future__ import annotations
import ast
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARB=ROOT/'method_code'
sys.path[:0]=[str(ROOT/'scripts'),str(ARB/'src')]


def guard():
    tmp=Path(os.environ['TMPDIR']).resolve()
    allowed={Path(__file__).resolve(),ROOT/'scripts/w13_runtime.py',
             ROOT/'scripts/run_cp_rag_20261016.py',ROOT/'corpus/gold_claims.py',
             ROOT/'corpus/normalize_claims_cache.py',ROOT/'corpus/CLAIM_PAYLOAD_SCHEMA.md'}
    def audit(event,args):
        if event in ('socket.connect','socket.bind','socket.getaddrinfo'):
            raise PermissionError('SAFETY_STOP: network forbidden')
        if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            p=Path(os.fsdecode(args[0])).resolve();s=str(p)
            if p.is_relative_to(tmp):return
            if any(x in s for x in ('/truth/','/data/','/oracle/','/reports/','/artifacts/',
                                    '/evaluator/','/assets/','/.ssh/','/env/','auth.json','llm.env')):
                raise PermissionError('SAFETY_STOP: hidden asset forbidden')
            public_method_source = p.is_relative_to(ARB/'src') and p.suffix in ('.py', '.pyc')
            public_method_config = p == ARB/'config/arbiter_hparams.json'
            if p.is_relative_to(ROOT) and p not in allowed and not public_method_source and not public_method_config:
                raise PermissionError('SAFETY_STOP: non-public project asset forbidden')
    sys.addaudithook(audit)


def cp_policy():
    from collections import Counter,defaultdict
    from dataclasses import replace
    from typing import Any
    import numpy as np
    from arbiter.agent.core import ArbiterAgent,ExecutionPolicy
    from arbiter.agent.planning import BeliefPlanner,action_signature
    source=(ROOT/'scripts/run_cp_rag_20261016.py').read_text()
    tree=ast.parse(source)
    defs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('relation_key','cp_solve')]
    if len(defs)!=2:raise RuntimeError('frozen CP functions absent')
    namespace=dict(Counter=Counter,defaultdict=defaultdict,replace=replace,Any=Any,np=np,json=json,
                   ArbiterAgent=ArbiterAgent,ExecutionPolicy=ExecutionPolicy,
                   BeliefPlanner=BeliefPlanner,action_signature=action_signature)
    exec(compile(ast.Module(body=defs,type_ignores=[]),'<frozen_cp_policy>','exec'),namespace)
    return namespace['cp_solve'],hashlib.sha256(source.encode()).hexdigest()


def main():
    guard()
    import w13_runtime as vb
    from arbiter.agent.core import ArbiterAgent,ExecutionPolicy
    from arbiter.contracts import Goal,ItemStack
    from arbiter.agent.aggregation import naive_rag
    config=json.loads((ARB/'config/arbiter_hparams.json').read_text())
    normalizer=vb.CorpusItemNormalizer.from_config(ROOT,config['compiler']['normalizer_sources'])
    solve,cp_hash=cp_policy()
    kind=sys.argv[1]
    if kind=='vb_rag_cal':
        vb.POLICY['threshold']=float(sys.argv[2]);vb.POLICY['odds']=float(sys.argv[3])
    def rpc(method,params,intent=None):
        print(vb.canonical(dict(type='call',method=method,params=params,intent=intent)),flush=True)
        return json.loads(sys.stdin.readline())['result']
    class API:
        def inventory(self):return rpc('inventory',{})
        def mine(self,node_type,*,intent=None):return rpc('mine',{'node_type':node_type},intent)
        def craft_grid(self,layout,*,intent=None):return rpc('craft_grid',{'layout':layout},intent)
        def smelt(self,item,fuel,n,*,intent=None):return rpc('smelt',{'input':item,'fuel':fuel,'n':n},intent)
        def check_name(self,identifier,*,intent=None):return rpc('check_name',{'identifier':identifier},intent)
        def submit(self,result):return rpc('submit',{'result':result})
    for line in sys.stdin:
        request=json.loads(line)
        if request.get('shutdown'):return
        try:
            if set(request)!={'goal','kit','bundle'}:raise PermissionError('SAFETY_STOP: hidden envelope')
            if kind=='vb_rag_cal':
                result=vb.VBRAG(request,rpc,normalizer).solve()
            else:
                derived,ops=vb.compile_bundle(request['bundle'],normalizer)
                agent=ArbiterAgent(Goal(request['goal']['item'],request['goal']['count']),
                                  [ItemStack(r['name'],r['count']) for r in request['kit']],API())
                def research(subject,goal):
                    bundle=rpc('research',dict(subject=subject,goal=goal))
                    dd,oo=vb.compile_bundle(bundle,normalizer)
                    probs={c.mechanism_id:(.99 if c.value else .01) for c in dd}
                    return [(op,probs.get(op.mechanism_id,.5)) for op in oo]
                agent._cp_research=research
                if derived:
                    agent.configure(ops,naive_rag(derived).belief)
                    result=solve(agent,policy=ExecutionPolicy())
                else:
                    agent.api.submit({'feasible':False})
                    result=dict(success=False,feasible=False,used_mechanism_ids=[],policy_trace=[])
                result['temporal_belief']=dict(formed_year=None,source_year_count=0,posterior={},updates=0)
                result['frozen_cp_source_sha256']=cp_hash
            result['runtime_guard']=dict(evaluation_asset_reads=0,network_calls=0,guard_installed_before_policy_imports=True)
            print(vb.canonical(dict(type='done',result=result)),flush=True)
        except Exception as e:
            print(vb.canonical(dict(type='error',error_type=type(e).__name__,message=str(e))),flush=True)


if __name__=='__main__':main()
