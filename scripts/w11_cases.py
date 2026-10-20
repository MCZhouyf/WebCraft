#!/usr/bin/env python3
"""Record hypotheses and capture the nine divergence states before source tests."""
from __future__ import annotations
import json
import sys
from w11_common import *


def main():
    OUT.mkdir(parents=True,exist_ok=True);resources('cases')
    ledger=[
        {'hypothesis':'different planner source','pass1':'possible; compare pinned/runtime code','pass2':'pending'},
        {'hypothesis':'different compiled evidence/operator insertion order','pass1':'possible; compare same frozen bundles','pass2':'pending'},
        {'hypothesis':'set-ordered MILP balance rows / alternative optima','pass1':'explicit items=set(...) and for item in items; no objective tie term','pass2':'pending'},
        {'hypothesis':'filesystem ordering','pass1':'check callers; no planner file enumeration','pass2':'pending'},
        {'hypothesis':'floating-point/objective differences','pass1':'compare exact probabilities, coefficients and invariant matrix','pass2':'pending'},
        {'hypothesis':'solver runtime source outside PYTHONHASHSEED','pass1':'inspect binary/version/default settings and archived provenance','pass2':'pending'}]
    write('hypothesis_ledger_pass1.csv',ledger)
    quotes=[]
    sections={'agent/planning.py':[(88,121),(139,200),(202,247)],
              'agent/core.py':[(148,190),(196,236)],'agent/decision.py':[(90,133)],
              'evaluation/p6_execution.py':[(361,402)]}
    for rel,ranges in sections.items():
        path=ARB/'src/arbiter'/rel;lines=path.read_text().splitlines()
        for lo,hi in ranges:
            quotes.append({'file':str(path.relative_to(ARB)),'sha256':sha(path),'start_line':lo,'end_line':min(hi,len(lines)),
                           'source':'\n'.join(f'{i}: {lines[i-1]}' for i in range(lo,min(hi,len(lines))+1))})
    (OUT/'source_quotes.json').write_text(json.dumps(quotes,indent=2)+'\n')
    reconstruction=Reconstruction()
    pairs=json.loads((ROOT/'reports/paperA/w10_release_final/impact/nine_episode_pairs.json').read_text())
    cases=[]
    for number,pair in enumerate(pairs):
        a,b=pair['frozen'],pair['replayed']
        def sig(r):
            return [(e['method'],e.get('params'),e.get('intent'),mid)
                    for e,mid in zip([e for e in r['events'] if e['method'] in ACTION_METHODS],r['used_mechanism_ids'])]
        sa,sb=sig(a),sig(b)
        divergence=next((i for i,(x,y) in enumerate(zip(sa,sb)) if x!=y),min(len(sa),len(sb)))
        snapshots=[]
        for label,rec in (('frozen',a),('replayed',b)):
            state=next(s for s in reconstruction.states(rec) if s['semantic_index']==divergence)
            value=fixture(state)
            (OUT/'fixtures').mkdir(exist_ok=True)
            path=OUT/f'fixtures/case_{number:02d}_{label}.json'
            path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
            snapshots.append({'fixture':str(path.relative_to(ROOT)),'fixture_sha256':sha(path),
                              'bundle_sha256':state['bundle_sha256'],'selected':state['trace']})
        cases.append({'case':number,'key':key(a),'divergence_semantic_index_0based':divergence,
                      'frozen':snapshots[0],'replayed':snapshots[1],
                      'exact_fixture_equal':snapshots[0]['fixture_sha256']==snapshots[1]['fixture_sha256']})
    (OUT/'captured_cases.json').write_text(json.dumps(cases,indent=2)+'\n')
    print(json.dumps({'captured_cases':len(cases),'equal_fixtures':sum(x['exact_fixture_equal'] for x in cases)}))


if __name__=='__main__':main()
