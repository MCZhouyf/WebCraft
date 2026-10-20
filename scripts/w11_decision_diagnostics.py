#!/usr/bin/env python3
"""Second reasoning pass: apply pure selectors to already solved candidate sets."""
from __future__ import annotations
from w11_common import *
import numpy as np
from arbiter.agent.planning import CandidatePlan,PlanStep
from arbiter.agent.decision import ContextBelief,DecisionController
from arbiter.agent.core import classification_information_value


def main():
    resources('decision_diagnostics');reconstruction=Reconstruction()
    cases=json.loads((OUT/'captured_cases.json').read_text())
    pairs=json.loads((ROOT/'reports/paperA/w10_release_final/impact/nine_episode_pairs.json').read_text())
    worker_results=list((OUT/'source_tests').glob('*.json'))
    results=[];states=[]
    for case,pair in zip(cases,pairs):
        state=next(s for s in reconstruction.states(pair['frozen']) if s['semantic_index']==case['divergence_semantic_index_0based'])
        bs=state['belief_state'];states.append({'case':case['case'],**bs,
                                              'explicit_probes_before':state['explicit_probes_before']})
        expected={case['frozen']['selected']['mechanism_id'],case['replayed']['selected']['mechanism_id']}
        selected=set()
        for path in sorted(worker_results):
            data=json.loads(path.read_text())
            if data['fixture_sha256']!=case['frozen']['fixture_sha256']:continue
            plans=[CandidatePlan(tuple(PlanStep(**s) for s in r['steps']),
                                 tuple(dict.fromkeys(s['mechanism_id'] for s in r['steps'])),
                                 r['primary_cost'],r['objective'],tuple(r['selected_counts'])) for r in data['plans']]
            belief=ContextBelief(np.array(bs['q']),{k:np.array(v) for k,v in bs['phi'].items()},dict(bs['observations']))
            first={p.steps[0].mechanism_id:p.steps[0] for p in plans if p.steps}
            strategy=case['frozen']['selected']['execution_strategy']
            if strategy=='arbiter' and state['explicit_probes_before']<reconstruction.policy.max_probes:
                eligible=set(first)
                decision=DecisionController().choose(plans,belief,{k:s.cost for k,s in first.items()},probe_candidates=eligible)
                scored=[(classification_information_value(belief.mechanism_probability(k),10.0,first[k].cost),k)
                        for k in sorted(eligible) if k not in belief.observations]
                risk,mid=max(scored) if scored else (0,None)
                if risk>DecisionController().kappa:selected_mid=mid
                else:selected_mid=decision.mechanism_id if decision.kind=='probe' else decision.plan.steps[0].mechanism_id
            else:selected_mid=plans[0].steps[0].mechanism_id
            selected.add(selected_mid)
            results.append({'case':case['case'],'method':case['key'][1],'seed':data['python_hash_seed'],
                            'worker_result':str(path.relative_to(OUT)),'selected':selected_mid,
                            'matches_frozen':int(selected_mid==case['frozen']['selected']['mechanism_id']),
                            'matches_W9_replay':int(selected_mid==case['replayed']['selected']['mechanism_id'])})
        case['both_historical_selections_reproduced']=expected<=selected
    write('pure_decision_results.csv',results)
    (OUT/'captured_decision_states.json').write_text(json.dumps(states,indent=2)+'\n')
    (OUT/'source_cases_second_pass.json').write_text(json.dumps(cases,indent=2)+'\n')
    print(json.dumps({'cases':len(cases),'both_selections_reproduced':sum(c['both_historical_selections_reproduced'] for c in cases),
                      'new_planner_solves':0,'new_world_actions':0}))


if __name__=='__main__':main()
