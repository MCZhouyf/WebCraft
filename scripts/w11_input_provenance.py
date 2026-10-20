#!/usr/bin/env python3
"""Check the actual old/post-run and W9 bundle origins, not just shared objects."""
from w11_common import *


def main():
    resources('input_provenance')
    pairs=json.loads((ROOT/'reports/paperA/w10_release_final/impact/nine_episode_pairs.json').read_text())
    cases=json.loads((OUT/'captured_cases.json').read_text())
    wanted={(p['key'][0],p['key'][2],p['key'][4]) for p in pairs}
    bundles={}
    for name in ('assets/evidence/main.jsonl.gz','assets/restricted/evidence/main.jsonl.gz'):
        path=ROOT/'release/package'/name
        if not path.exists():continue
        for r in rows(path):
            k=(r['mode'],r['condition'],r['task_id'])
            if k in wanted:bundles[k]=r['bundle']
    assert set(bundles)==wanted,'STOP: missing actual packaged bundle'
    old=Reconstruction();new=Reconstruction(bundles)
    out=[]
    for case,pair in zip(cases,pairs):
        a=next(s for s in old.states(pair['frozen']) if s['semantic_index']==case['divergence_semantic_index_0based'])
        b=next(s for s in new.states(pair['replayed']) if s['semantic_index']==case['divergence_semantic_index_0based'])
        fa,fb=fixture(a),fixture(b)
        midlist=lambda f:[op['mechanism_id'] for op in f['operators']]
        newpath=OUT/f'fixtures/case_{case["case"]:02d}_actual_W9_input.json'
        newpath.write_text(json.dumps(fb,indent=2,sort_keys=True)+'\n')
        out.append({'case':case['case'],'key':case['key'],
                    'original_bundle_sha256':a['bundle_sha256'],'W9_bundle_sha256':b['bundle_sha256'],
                    'original_operators':len(fa['operators']),'W9_operators':len(fb['operators']),
                    'operator_order_equal':midlist(fa)==midlist(fb),'operator_set_equal':set(midlist(fa))==set(midlist(fb)),
                    'actual_fixture_equal':fa==fb,'actual_W9_fixture':str(newpath.relative_to(ROOT)),
                    'actual_W9_fixture_sha256':sha(newpath),
                    'added_operator_ids':sorted(set(midlist(fb))-set(midlist(fa))),
                    'removed_operator_ids':sorted(set(midlist(fa))-set(midlist(fb)))})
    (OUT/'actual_input_provenance.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({'actual_cases':len(out),'same_actual_fixture':sum(r['actual_fixture_equal'] for r in out),
                      'same_operator_order':sum(r['operator_order_equal'] for r in out)}))


if __name__=='__main__':main()
