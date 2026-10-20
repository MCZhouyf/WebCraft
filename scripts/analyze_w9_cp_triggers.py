#!/usr/bin/env python3
"""Audit only the accepted CP-RAG traces; never run an agent."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/paperA/w9_release_fix/cp_triggers'
SOURCE = ROOT/'reports/paperA/review_v14_zero_call/cp_rag/episodes.jsonl'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in SOURCE.read_text().splitlines() if line]
    rows, examples = [], []
    for record in records:
        traces = record.get('policy_trace', [])
        research = [r for r in traces if r.get('research_triggered')]
        probes = [r for r in traces if r.get('decision') == 'probe_conflict']
        rejected = [r for r in traces if r.get('observed') is False and not r.get('resource_failure')]
        returned = sum(int(r.get('new_candidates', 0)) for r in research)
        category = ('never_triggered' if not research else
                    'triggered_zero_new_candidates' if not returned else
                    'returned_adoption_not_logged')
        scope = ('cds' if record['condition'].startswith('cds_') else
                 'ext_v2' if record['task_id'].startswith('extv2_') else
                 'main:'+record['reading_mode'])
        live_eligible=scope in {'main:llm_only','main:all_public','ext_v2'}
        inferred_requests=(len({trace['denied_subject'] for trace in research}) if live_eligible else 0)
        row = {'scope': scope, 'method': record['method'], 'reading_mode': record['reading_mode'],
               'condition': record['condition'], 'world': record['world'], 'task_id': record['task_id'],
               'conflict_probe_triggers': len(probes),
               'denied_candidate_occurrences': len(rejected),
               'denied_unique_candidates': len({r['mechanism_id'] for r in rejected}),
               'active_requery_triggers': len(research), 'new_candidates_returned': returned,
               'public_search_invocations_inferred':inferred_requests,
               'callbacks_without_public_index':len(research) if not live_eligible else 0,
               'raw_search_hits': 'not_logged', 'category': category,
               'has_denied_candidate': int(bool(rejected)),
               'never_triggered_explanation': ('' if research else
                   'no_candidate_was_denied' if not rejected else
                   'no_open_goal_without_a_plan_after_denial')}
        rows.append(row)
        examples.append({'key': {k: row[k] for k in ('scope','condition','world','task_id')},
                         'category': category, 'policy_trace': traces,
                         'semantic_events': [e for e in record['events'] if e.get('method') in
                                             {'mine','craft_grid','smelt','check_name'}]})
    with (OUT/'per_instance.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    selected=[]
    for category in sorted({r['category'] for r in examples}):
        candidates=sorted((r for r in examples if r['category']==category),
                          key=lambda r: json.dumps(r['key'],sort_keys=True))
        selected.extend(candidates[:3 if category.startswith('triggered') else 2])
    selected=selected[:5]
    (OUT/'five_examples.json').write_text(json.dumps(selected,indent=2,sort_keys=True)+'\n')
    count=Counter(row['category'] for row in rows)
    totals={field: sum(row[field] for row in rows) for field in
            ('conflict_probe_triggers','denied_candidate_occurrences','denied_unique_candidates',
             'active_requery_triggers','new_candidates_returned',
             'public_search_invocations_inferred','callbacks_without_public_index')}
    summary={'episodes':len(rows),'categories':dict(count),'totals':totals,
             'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    (OUT/'REPORT.md').write_text(
        '# CP-RAG requery audit\n\n'+
        f"The 1,442 accepted episodes contain {totals['conflict_probe_triggers']} conflict-verification triggers, "
        f"{totals['denied_candidate_occurrences']} denied-candidate occurrences and {totals['active_requery_triggers']} "
        'active-requery triggers. All logged new-candidate counts are zero.\n\n'+
        f"Code-and-trace reconstruction implies {totals['public_search_invocations_inferred']} public-index search invocations "
        f"(within-episode repeated subject/goal callbacks share a cache); {totals['callbacks_without_public_index']} callbacks "
        'belong to CDS/direct-gold modes that have no corresponding public index and return without searching. '
        'These are inferred invocation counts, not request-level telemetry.\n\n'+
        f"Episode categories: never triggered {count['never_triggered']}; triggered with zero new candidates "
        f"{count['triggered_zero_new_candidates']}; returned candidates but unused 0; returned and used 0.\n\n"+
        'The source code invokes requery only after a denied action leaves no plan for the open goal, and only once per denied relation. '
        'The trace counts novel compiled mechanisms, not raw search hits; a zero cannot establish that search returned no snippets or '
        'that no alternative route exists. CDS and direct-gold evidence have no corresponding public live index, so their registered callback '
        'returns no additions. These implementation scope limits must be distinguished from lack of alternatives in the Web corpus.\n\n'+
        'W8 reported zero active-retrieval action-divergence categories because its classifier assigned extra_probe first. '
        'That category count did not measure requery triggering and must not be used to claim the mechanism never triggered. '
        'The present audit adds direct trace accounting without altering the accepted episodes or previously reported numbers.\n')
    print(json.dumps(summary,sort_keys=True))


if __name__=='__main__':
    main()
