#!/usr/bin/env python3
"""Check release anonymization preserves numerical and agent-visible evidence."""
import csv
import gzip
import io
import json
import tarfile
from itertools import zip_longest
from pathlib import Path
from w10_release import SOURCE, OFFLINE, OUT, decrypt_archive, digest, manifest_names, write_csv, clean_json


def objects(data,compressed=False):
    if compressed:data=gzip.decompress(data)
    for line in io.StringIO(data.decode()):
        if line.strip():yield json.loads(line)


def archive(path):
    raw=decrypt_archive(path)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as tar:
        return {m.name:tar.extractfile(m).read() for m in tar if m.isfile()}


def compare_records(name,left,right):
    n=0
    for a,b in zip_longest(objects(left,name.endswith('.gz')),objects(right,name.endswith('.gz'))):
        assert a is not None and b is not None,(name,'row cardinality')
        if 'record' in a and 'events' in a:
            assert a['events']==b['events'],(name,'external public events')
            a,b=a['record'],b['record']
        for field in ('events','metrics','used_mechanism_ids','policy_trace','probe_count','probe_cost',
                      'condition','world','method','reading_mode','task_id','true_feasible',
                      'reported_feasible','temporal_filter','conflict_mechanism_ids'):
            assert a.get(field)==b.get(field),(name,n,field)
        n+=1
    return n


def main():
    rows=[]
    critical=('data/tasks.jsonl','data/conflicts.jsonl','config/cost_model.json',
              'config/resource_model.json','config/recipe_type_policy.json',
              'assets/prior/frozen_prior_bundle.json')
    for name in manifest_names(SOURCE):
        a,b=(SOURCE/name).read_bytes(),(OFFLINE/name).read_bytes()
        if name in critical or name.startswith('truth/') or (name.startswith('corpus/') and name.endswith(('documents.jsonl','chunk_claims.jsonl'))):
            invariant='exact frozen data bytes'
            if a!=b and name.endswith('.json'):
                before=json.loads(a);after=json.loads(b)
                assert clean_json(before)==after,(name,'change exceeds authorized identity metadata')
                for field in ('recipes','drops','items','item_ids','blocks','registries','samples','assertions'):
                    assert before.get(field)==after.get(field),(name,field,'research content changed')
                invariant='exact research objects; only source-path identity metadata relocated'
            else:assert a==b,(name,'frozen data bytes changed')
            rows.append({'file':name,'invariant':invariant,'rows':'','passed':True})
        if name.startswith('assets/frozen/'):
            rows.append({'file':name,'invariant':'all numeric/action/metric and public event fields unchanged',
                         'rows':compare_records(name,a,b),'passed':True})
        if name.endswith('.csv'):
            left=list(csv.reader(io.StringIO(a.decode())));right=list(csv.reader(io.StringIO(b.decode())))
            assert len(left)==len(right),(name,'CSV row count')
            numeric=0
            for aa,bb in zip(left,right):
                assert len(aa)==len(bb),(name,'CSV columns')
                for av,bv in zip(aa,bb):
                    try:float(av)
                    except ValueError:continue
                    assert av==bv,(name,'numeric CSV cell changed',av,bv)
                    numeric+=1
            rows.append({'file':name,'invariant':'numeric CSV cells unchanged','rows':numeric,'passed':True})
    old=archive(SOURCE/'corpus/encrypted/w9_replay_restricted.tar.gz.enc')
    new=archive(OFFLINE/'corpus/encrypted/w9_replay_restricted.tar.gz.enc')
    assert set(old)==set(new)
    for name,a in old.items():
        b=new[name]
        if name.startswith('assets/frozen/'):
            n=compare_records(name,a,b);rule='all public events, actions, numerical metrics and experimental keys unchanged'
        elif name.endswith('.jsonl.gz'):
            aa=list(objects(a,True));bb=list(objects(b,True));assert aa==bb,(name,'evidence bundles changed')
            n=len(aa);rule='exact evidence objects unchanged'
        else:
            assert a==b,(name,'restricted claims/index bytes changed')
            n='';rule='exact restricted corpus bytes'
        rows.append({'file':'encrypted!'+name,'invariant':rule,'rows':n,'passed':True})
    write_csv(OUT/'semantic_invariants.csv',rows)
    summary={'checks':len(rows),'passed':all(r['passed'] for r in rows),
             'frozen_main_reference_episodes':11220,'CP_reference_episodes':1442,
             'no_experimental_rerun':True}
    (OUT/'semantic_invariants.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
