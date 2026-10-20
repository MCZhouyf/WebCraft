#!/usr/bin/env python3
"""Export existing CP callbacks and exact frozen-code reconstructed queries."""
import csv
import hashlib
import json
import gzip
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/paperA/w10_release_final/cp_queries'
sys.path[:0]=[str(ROOT.parent/'arbiter/src'),str(ROOT)]
from arbiter.evaluation.p6_execution import _compiler, _claims, _claim_assertions
from arbiter.agent.aggregation import operator_from_assertion


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    tasks={json.loads(l)['id']:json.loads(l) for file in
           (ROOT/'data/tasks.jsonl',ROOT/'data/ext_v2/ext_v2_tasks_20261016.jsonl')
           for l in file.read_text().splitlines() if l}
    catalog={json.loads(l)['id']:json.loads(l) for l in (ROOT/'data/conflicts.jsonl').read_text().splitlines() if l}
    source=ROOT/'reports/paperA/review_v14_zero_call/cp_rag/episodes.jsonl'
    normalizer,_=_compiler(ROOT)
    evidence={}
    for scope in ('main','ext_v2','cds'):
        paths=[ROOT/f'release/package/assets/evidence/{scope}.jsonl.gz']
        if scope=='main':paths.append(ROOT/'release/package/assets/restricted/evidence/main.jsonl.gz')
        for path in paths:
            with gzip.open(path,'rt') as handle:
                for line in handle:
                    entry=json.loads(line)
                    evidence[(entry['mode'],entry['condition'],entry['task_id'])]=entry['bundle']
    provenance_cache={}
    def provenance(record,mid):
        key=(record['reading_mode'],record['condition'],record['task_id'])
        if key not in provenance_cache:
            bundle=evidence[key];hits={r['chunk_id']:r for r in bundle['search_rows']}
            _,operators=_claims(bundle,normalizer=normalizer)
            actual={op.mechanism_id for op in operators};index={}
            for raw in bundle['claims']:
                hit=hits.get(raw['chunk_id'])
                if not hit:continue
                for assertion in _claim_assertions(raw,hit['text'],set(bundle.get('mineable_nodes',())) or None,normalizer):
                    try:op=operator_from_assertion(assertion)
                    except ValueError:continue
                    if op.mechanism_id not in actual:continue
                    index.setdefault(op.mechanism_id,[]).append({
                        'claim_id':raw['claim_id'],'span_text':raw.get('span_text',''),
                        'claim_type':raw['claim_type'],'subject_item':raw['subject_item'],
                        'payload':raw['payload'],'polarity':raw['polarity'],'tense':raw['tense'],
                        'normalized_assertion':assertion,
                        'source':{n:hit[n] for n in ('source_site','page_title','revid','timestamp','chunk_id')}})
            provenance_cache[key]=index
        return provenance_cache[key].get(mid,[])
    rows=[]; episodes=[]
    for line in source.read_text().splitlines():
        record=json.loads(line)
        if not any(t.get('research_triggered') for t in record.get('policy_trace',[])):continue
        goal=tasks[record['task_id']]['target']['item']
        identity={n:record[n] for n in ('reading_mode','condition','world','task_id')}
        eligible=(record['reading_mode'] in {'all_public','llm_only'} and not record['condition'].startswith('cds_'))
        seen=set(); denied=None; occurrence=0
        semantic=[e for e in record['events'] if e.get('method') in {'mine','craft_grid','smelt','check_name'}]
        action_index=-1
        for trace_index,trace in enumerate(record['policy_trace']):
            if trace.get('decision') in {'probe_conflict','execute_naive'}:
                action_index+=1
                if trace.get('observed') is False:
                    denied={**trace,'event':semantic[action_index]}
            if not trace.get('research_triggered'):continue
            occurrence+=1
            subject=trace['denied_subject']; query=f'{subject} {goal}'
            relation=denied.get('relation_key') if denied else None
            exact=bool(denied and relation and relation[1]==subject)
            mid=denied.get('mechanism_id','') if denied else ''
            claim=catalog.get(mid,{}).get('assertion')
            linked=provenance(record,mid)
            if claim is None and linked:claim=linked[0]['normalized_assertion']
            if claim is None and denied:
                claim={'statement_source':'recorded_operator_and_actual_call',
                       'relation_key':relation,'expected_subject':subject,
                       'action_method':denied['event']['method'],'action_params':denied['event'].get('params',{}),
                       'catalog_assertion':'not_present'}
            executed=eligible and (subject,goal) not in seen
            seen.add((subject,goal))
            rows.append({**identity,'trigger_number':occurrence,'trace_index':trace_index,
                         'query':query,'query_origin':'deterministically_reconstructed_from_frozen_callback',
                         'logged_verbatim_query':False,'executed_search_inferred':int(executed),
                         'no_public_index':int(not eligible),'cache_reuse':int(eligible and not executed),
                         'denied_mechanism_id':mid,'denied_claim':json.dumps(claim,sort_keys=True),
                         'frozen_claim_provenance':json.dumps(linked,sort_keys=True),
                         'frozen_claim_matches':len(linked),
                         'denied_action_observed_false':bool(denied and denied.get('observed') is False),
                         'denied_resource_failure':bool(denied and denied.get('resource_failure')),
                         'denied_relation_matches_query':exact,'new_candidates':trace['new_candidates'],
                         'raw_search_hits':'not_logged'})
        episodes.append({**identity,'triggers':occurrence,
                         'queries':json.dumps([r['query'] for r in rows[-occurrence:]]),
                         'denied_mechanism_ids':json.dumps([r['denied_mechanism_id'] for r in rows[-occurrence:]])})
    assert len(episodes)==308 and len(rows)==308
    assert all(r['denied_relation_matches_query'] for r in rows)
    for filename,values in (('trigger_episodes.csv',episodes),('queries_and_denied_claims.csv',rows)):
        with (OUT/filename).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(values[0]),lineterminator='\n');w.writeheader();w.writerows(values)
    summary={'episodes':len(episodes),'callback_occurrences':len(rows),
             'inferred_executed_searches':sum(r['executed_search_inferred'] for r in rows),
             'no_public_index':sum(r['no_public_index'] for r in rows),
             'callbacks_with_frozen_claim_provenance':sum(bool(r['frozen_claim_matches']) for r in rows),
             'resource_failure_callbacks':sum(r['denied_resource_failure'] for r in rows),
             'verbatim_query_telemetry':'not_logged',
             'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    (OUT/'README.md').write_text('# CP-RAG callback query export\n\n'
        'All 308 accepted trigger-positive episodes are exported. Query strings are reconstructed '
        'exactly from the frozen callback expression f"{subject} {goal}"; they were not logged verbatim. '
        '203 callbacks imply index searches; 105 have no corresponding public index and return before '
        'searching, so their strings are intended queries, not observed requests. Denied claims are '
        'linked to the preceding negative action and frozen evidence. All 308 denied claims are '
        'resolved to original claim IDs, payloads, source metadata and span text using the frozen compiler; '
        'uncatalogued assertion IDs are not treated as missing raw-claim provenance. '
        'No retrieval or agent is rerun. Raw hit counts are unavailable.\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
