#!/usr/bin/env python3
"""Evaluator-only full-corpus retrieval ceilings and snapshot selection audit."""
from __future__ import annotations
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT.parent/'arbiter/src')]
from corpus.search_backend import SearchBackend
from corpus.claims_cache import ClaimsCache
from corpus.gold_claims import NameResolver, normalized_truth_recipes, recipe_truth
from corpus.block_templates import public_template_claims, public_prose_drop_claims
from corpus.solvability_audit import requirements_from_task, claim_matches
from arbiter.evaluation.p6_execution import _compiler, _claims
from w13_run import OUT, csvout, jsonl, sha, resource_gate

CONDITIONS=('matched_1.16.5','matched_1.19','matched_1.21.11',
            'mismatched_new2old','mismatched_old2new','mixed','polluted')
TRUTHS={}
BACKENDS={}
CACHES={}
PARSED={}
NORMALIZER=None
INPUTS={}


def record_input(path):
    INPUTS[str(path.relative_to(ROOT))]=dict(sha256=sha(path),bytes=path.stat().st_size)


def support_excerpt(claim,text):
    span=str(claim.get('span_text',''))
    if span and span in text:
        return span[:300]
    subject=str(claim.get('subject_item','')).replace('_',' ')
    at=text.casefold().find(subject.casefold())
    return text[max(0,at-60):max(0,at-60)+300]


def valid(claim,world):
    if claim.get('polarity')!='positive':
        return False
    kind,payload,subject=claim.get('claim_type'),claim.get('payload',{}),claim.get('subject_item')
    try:
        if kind in ('recipe','smelting'):
            if not payload.get('ingredients') or payload.get('conditional_variants'):
                return False
            q={**claim,'payload':{**payload,'output_count':int(payload.get('output_count',1)),
                                  'station':'furnace' if kind=='smelting' else payload.get('station','crafting_grid')}}
            return recipe_truth(q,normalized_truth_recipes(world))
        if kind=='drop':
            if payload.get('conditional_variants'):
                return False
            key='silk_touch_items' if payload.get('silk_touch') else 'no_silk_touch_items'
            rows=[r for r in TRUTHS[world]['drops'] if r['block'].removeprefix('minecraft:')==subject]
            wanted={v.removeprefix('minecraft:') for r in rows for v in r.get(key,[])}
            return bool(rows) and set(payload.get('drops',[]))==wanted
    except (KeyError,ValueError,TypeError,IndexError):
        return False
    return False


def compiled_ids(hit,claims):
    try:
        _,ops=_claims(dict(search_rows=[hit],claims=claims),normalizer=NORMALIZER)
        return {op.mechanism_id for op in ops}
    except (KeyError,ValueError,TypeError):
        return set()


def prepare():
    global NORMALIZER
    resource_gate('diagnostics')
    NORMALIZER,_=_compiler(ROOT)
    for world in ('1.16.5','1.19','1.21.11'):
        p=ROOT/f'truth/{world}.json'; record_input(p)
        TRUTHS[world]=json.loads(p.read_text())
    for condition in CONDITIONS:
        path=ROOT/'corpus/indexes/formal_p1'/condition
        backend=SearchBackend(condition,str(path))
        BACKENDS[condition]=backend
        CACHES[condition]=ClaimsCache(str(path))
        for p in (path/'documents.jsonl',path/'bm25.json',path/'config.json',
                  ROOT/'corpus/claims_cache/formal_p1'/condition/'chunk_claims.jsonl'):
            record_input(p)
    resolver=NameResolver()
    subjects=sorted({r['block'].removeprefix('minecraft:') for truth in TRUTHS.values() for r in truth['drops']})
    parsed_errors=[]
    # Parse each existing chunk once; no generation or modification of the corpus.
    for condition,backend in BACKENDS.items():
        for doc in backend.documents:
            cid=doc['chunk_id']
            if cid in PARSED:
                continue
            try:
                facts=public_template_claims(doc['text'],doc['page_title'],cid,versions=())
                visible_subjects=[s for s in subjects if s.replace('_',' ').casefold() in
                                  (doc['text']+' '+doc['page_title']).casefold()]
                facts+=public_prose_drop_claims(doc['text'],doc['page_title'],cid,visible_subjects)
                PARSED[cid]=facts
            except (KeyError,ValueError,TypeError,IndexError) as exc:
                PARSED[cid]=[]
                parsed_errors.append(dict(chunk_id=cid,error_type=type(exc).__name__,
                                           convention='unresolved_not_absent'))
    csvout('diagnostics/parser_discrepancies.csv',parsed_errors)
    for name in ('corpus/manifests/page_map_p1.json','corpus/manifests/p1.json',
                 'corpus/manifests/export_titles_p1.txt','corpus/release_times.json',
                 'corpus/build_manifest.py','corpus/dump_pipeline.py','corpus/build_indexes.py',
                 'reports/STAGE2_REPORT.md','prereg/corpus_freeze_p1.md',
                 'reports/paperA/w10_release_final/cp_queries/queries_and_denied_claims.csv',
                 'data/tasks.jsonl', 'prereg/w13_vbrag_20261017.md',str(Path(__file__).relative_to(ROOT))):
        record_input(ROOT/name)


def retrieval():
    source=ROOT/'reports/paperA/w10_release_final/cp_queries/queries_and_denied_claims.csv'
    with source.open(newline='') as f:
        raw=list(csv.DictReader(f))
    queries=[r for r in raw if r['executed_search_inferred']=='1' and r['no_public_index']=='0'
             and r.get('cache_reuse','0') in ('0','False','false','')]
    rows,examples=[],[]
    rank_cache={}
    fact_cache={}
    for n,q in enumerate(queries):
        condition,world,mode=q['condition'],q['world'],q['reading_mode']
        backend=BACKENDS.get(condition)
        if backend is None:
            rows.append({**{k:q[k] for k in ('reading_mode','condition','world','task_id','query')},
                         'category':'not_reported','reason':'no_public_index'})
            continue
        denied=json.loads(q['denied_claim'])
        kind=('drop' if denied.get('kind')=='drop' else 'smelting' if denied.get('station')=='furnace' else
              'name' if denied.get('kind')=='name' else 'recipe')
        subject=denied.get('block',denied.get('output',denied.get('item',q['query'].split()[0])))
        fk=(condition,world,mode,kind,subject)
        if fk not in fact_cache:
            facts=[];candidates=[];uncompiled_supported=[]
            for doc in backend.documents:
                cid=doc['chunk_id']
                all_claims=CACHES[condition].get(cid)
                mode_claims=[c for c in all_claims if mode!='llm_only' or c.get('extractor_version')=='96b07e80']
                parsed=[c for c in PARSED[cid] if c.get('claim_type')==kind and
                        NORMALIZER.item_id(c.get('subject_item',''))==subject and valid(c,world)]
                cached=[c for c in mode_claims if c.get('claim_type')==kind and
                        NORMALIZER.item_id(c.get('subject_item',''))==subject and valid(c,world)]
                # A deterministic source marker with an in-chunk span is usable evidence;
                # unsupported LLM parameter completion alone cannot prove textual existence.
                grounded=[c for c in cached if c.get('payload',{}).get('deterministic') and
                          str(c.get('span_text','')) and c['span_text'] in doc['text'] and
                          c.get('parent_claim_id') is None]
                confirmed=parsed+grounded
                if confirmed:
                    allowed_confirmed=[c for c in cached if compiled_ids(doc,[c])]
                    facts.append(dict(doc=doc,claims=confirmed,mode_compilable=bool(allowed_confirmed),
                                      supported_candidates=len(confirmed),cache_valid=len(cached)))
                text=(doc['text']+' '+doc['page_title']).casefold()
                needle=subject.replace('_',' ').casefold()
                words={'recipe':r'craft|recipe','smelting':r'smelt|furnace|recipe',
                       'drop':r'drops?|break|mined','name':r'called|renamed|name'}
                if cached or (needle in text and re.search(words[kind],text)):
                    candidates.append(cid)
            fact_cache[fk]=(facts,candidates)
        facts,candidates=fact_cache[fk]
        rk=(condition,q['query'])
        if rk not in rank_cache:
            ranked=backend.search(q['query'],len(backend.documents))
            rank_cache[rk]={r['chunk_id']:i+1 for i,r in enumerate(ranked)}
        ranking=rank_cache[rk]
        found=sorted(facts,key=lambda x:(ranking.get(x['doc']['chunk_id'],10**9),x['doc']['chunk_id']))
        top=[x for x in found if ranking.get(x['doc']['chunk_id'],10**9)<=10]
        if top and any(x['mode_compilable'] for x in top):
            category='existing_compilable_or_other_zero_novel'
            reason='valid supported chunk recalled and a valid cached payload compiles; zero NEW IDs is not empty retrieval'
        elif top:
            category='retrieved_uncompilable'
            reason='source-supported valid fact recalled but reading-mode cache yields no valid compilable payload'
        elif found:
            category='exists_bm25_missed'
            reason='source-supported valid fact not returned at k=10'
        elif candidates:
            category='unresolved_textual_existence'
            reason='cooccurrence or valid cached payload is not sufficient proof of explicit source support'
        else:
            category='corpus_absent_confirmed'
            reason='no positive supported payload and no subject/mechanism textual candidate in complete frozen index'
        best=found[0] if found else None
        minimum=ranking.get(best['doc']['chunk_id']) if best else None
        row={**{k:q[k] for k in ('reading_mode','condition','world','task_id','query')},
             'target_family':kind,'target_subject':subject,'category':category,'reason':reason,
             'all_index_chunks_scanned':len(backend.documents),'confirmed_valid_chunks':len(found),
             'unresolved_candidate_chunks':len(candidates),'minimum_k':minimum if minimum else
             'unrankable_positive_score_filter' if found else '',
             'ranked_chunks':len(ranking),'top10_valid_chunks':len(top),
             'compiler_after_full_recall':int(any(x['mode_compilable'] for x in found)),
             'best_chunk_id':best['doc']['chunk_id'] if best else '',
             'source_page':best['doc']['page_title'] if best else '',
             'source_revid':best['doc']['revid'] if best else '',
             'request_provenance':'W10_code_trace_reconstruction_not_telemetry'}
        rows.append(row)
        if best and condition!='polluted':
            examples.append({**row,'source_site':best['doc']['source_site'],
                             'source_timestamp':best['doc']['timestamp'],
                             'excerpt':support_excerpt(best['claims'][0],best['doc']['text'])})
        elif not best and candidates and condition!='polluted':
            doc=next(d for d in backend.documents if d['chunk_id']==candidates[0])
            examples.append({**row,'source_page':doc['page_title'],'source_revid':doc['revid'],
                             'excerpt':doc['text'][:300],
                             'caution':'candidate excerpt only; NOT confirmed target-world-valid text'})
        if (n+1)%25==0:
            print(f'retrieval full-scan {n+1}/{len(queries)}',flush=True)
    csvout('diagnostics/retrieval_203.csv',rows)
    counts=Counter(r['category'] for r in rows)
    csvout('diagnostics/retrieval_categories.csv',[dict(category=k,count=v,denominator=len(rows),rate=v/len(rows))
                                                 for k,v in sorted(counts.items())])
    samples=[]
    for category in sorted(counts):
        samples.extend([r for r in examples if r['category']==category][:4])
    csvout('diagnostics/retrieval_examples.csv',samples)
    return dict(requests=len(rows),expected_requests=203,categories=dict(counts),
                parsed_unique_chunks=len(PARSED),
                zero_score_chunks_are_unrankable=True,
                absent_category_is_explicit_scan_bound_not_semantic_omniscience=True)


def coverage():
    tasks=[r for r in jsonl(ROOT/'data/tasks.jsonl') if r.get('split')=='eval']
    requirements=[r for t in tasks for r in requirements_from_task(t)]
    page_map={r['item_id']:r for r in json.loads((ROOT/'corpus/manifests/page_map_p1.json').read_text())}
    mech_rows,page_rows=[],[]
    for label,source_version,condition in [('T1','1.16.5','matched_1.16.5'),
                                           ('T2','1.19','matched_1.19'),('T3','1.21.11','matched_1.21.11')]:
        backend=BACKENDS[condition]
        titles={d['page_title'].casefold().replace('_',' ') for d in backend.documents}
        ids={x.removeprefix('minecraft:') for x in TRUTHS[source_version]['names']['items']}
        all_claims=[c for d in backend.documents for c in CACHES[condition].get(d['chunk_id'])]
        by_kind=defaultdict(list)
        for c in all_claims:
            by_kind[(c.get('claim_type'),c.get('subject_item'))].append(c)
        for req in requirements:
            mapping=page_map.get(req.subject_item,{})
            resolved=mapping.get('resolved_title',{}).get(label) or mapping.get('page_title')
            page_present=bool(resolved and resolved.casefold().replace('_',' ') in titles)
            covered=any(claim_matches(req,c) for c in by_kind[(req.mechanism_type,req.subject_item)])
            own=req.subject_item in ids
            inputs={x for x in req.payload.get('ingredients',[]) if isinstance(x,str)}
            inputs.update(req.payload.get('drops',[]))
            mech_rows.append(dict(timepoint=label,source_version=source_version,condition=condition,
                                   task_id=req.task_id,oracle_world=req.world,mechanism_id=req.mechanism_id,
                                   family=req.mechanism_type,subject=req.subject_item,page_title=resolved or '',
                                   page_present=int(page_present),mechanism_covered=int(covered),
                                   subject_not_yet_exists=int(not own),
                                   payload_item_not_yet_exists=int(bool(inputs-ids)),
                                   missing_existing_subject=int(not covered and own),
                                   missing_subject_not_yet_exists=int(not covered and not own)))
        for task in tasks:
            for world,plan in task.get('oracle_plans',{}).items():
                if not plan or not plan.get('feasible'):
                    continue
                items=set()
                for action in plan.get('actions',[]):
                    items.add(action['expected']['name'])
                    for field in ('node_type','input','fuel','requires_tool'):
                        if isinstance(action.get(field),str): items.add(action[field])
                    items.update(x for x in action.get('layout',[]) if isinstance(x,str))
                for item in sorted(items):
                    mapping=page_map.get(item,{})
                    title=mapping.get('resolved_title',{}).get(label) or mapping.get('page_title')
                    present=bool(title and title.casefold().replace('_',' ') in titles)
                    page_rows.append(dict(timepoint=label,task_id=task['id'],oracle_world=world,item=item,
                                           page_title=title or '',page_present=int(present),
                                           item_not_yet_exists=int(item not in ids),
                                           missing_not_yet_exists=int(not present and item not in ids),
                                           missing_existing_item=int(not present and item in ids)))
    csvout('diagnostics/coverage_mechanism_detail.csv',mech_rows)
    csvout('diagnostics/coverage_page_detail.csv',page_rows)
    sums=[]
    for label in ('T1','T2','T3'):
        for unit,details,id_field in [('mechanism_instance',mech_rows,'mechanism_id'),
                                       ('mechanism_unique',mech_rows,'mechanism_id'),
                                       ('page_instance',page_rows,'item'),('page_unique',page_rows,'item')]:
            selected=[r for r in details if r['timepoint']==label]
            if unit.endswith('unique'):
                selected=list({r[id_field]:r for r in selected}.values())
            den=len(selected)
            metric='mechanism_covered' if unit.startswith('mechanism') else 'page_present'
            unknown='subject_not_yet_exists' if unit.startswith('mechanism') else 'item_not_yet_exists'
            missing_history='missing_subject_not_yet_exists' if unit.startswith('mechanism') else 'missing_not_yet_exists'
            present=sum(r[metric] for r in selected);missing=den-present
            history=sum(r[missing_history] for r in selected)
            sums.append(dict(timepoint=label,unit=unit,denominator=den,covered=present,
                             coverage=present/den if den else '',missing=missing,
                             item_not_yet_exists=sum(r[unknown] for r in selected),
                             missing_due_to_item_not_yet_exists=history,
                             history_fraction_all_requirements=history/den if den else '',
                             history_fraction_missing=history/missing if missing else '',
                             missing_existing_item=missing-history))
    csvout('diagnostics/coverage_summary.csv',sums)
    manifest=json.loads((ROOT/'corpus/manifests/p1.json').read_text())
    exported=(ROOT/'corpus/manifests/export_titles_p1.txt').read_text().splitlines()
    title_count=len([l for l in exported if l.strip()])
    report=['# 2026 source selection and coverage audit','',
            f'The author export list contains {title_count} nonempty titles. The P1 manifest contains '
            f'{len(manifest["items"])} item identifiers across {manifest["task_count"]} frozen tasks.', '',
            'The documented rule is PURPOSEFUL, not random: corpus/build_manifest.py:65-90',
            'takes all P1 tasks, unions per-version oracle dependency closures plus targets,',
            'resource blocks/outputs and the twelve separator items. dump_pipeline.py:127',
            'resolves these item-derived titles against the historical title/redirect catalog.',
            'The author then supplied browser Special:Export XML for the resulting list',
            '(reports/STAGE2_REPORT.md:193-230). No evidence supports claiming random sampling.', '',
            'page_map_p1.json supplies snapshot-specific resolved titles. coverage_summary.csv',
            'separates page presence from source claim-mechanism coverage; the latter uses the',
            'existing public solvability matcher, not a new model or task-specific compiler.',
            'Unique mechanisms/pages and task-world-weighted requirements are separate rows.',
            'All 34 eval tasks contribute their feasible oracle paths; an infeasible world',
            'has no oracle path and contributes no invented path requirements.', '',
            'Source registries identify items absent at the corresponding source version.',
            'The share of missing pages/mechanisms attributable to those absent items is',
            'reported explicitly. Remaining misses for EXISTING items are not automatically',
            'caused by manual export selection: rendering, redirect mapping, extraction and',
            'scope omissions can contribute. The export list itself was task/closure-guided,',
            'so it is not an independent estimate of general-Web coverage.', '',
            'A named page absent from a manifest but present in the actual index is matched',
            'only when the preregistered mapping resolves it; unresolved title aliases are',
            'conservatively counted missing and retained for author inspection.', '']
    (OUT/'diagnostics/EXPORT_SELECTION.md').write_text('\n'.join(report))
    return dict(eval_tasks=len(tasks),oracle_requirements=len(requirements),
                exported_titles=title_count,p1_item_identifiers=len(manifest['items']))


def main():
    prepare()
    summary={'retrieval':retrieval(),'coverage':coverage(),'LLM_calls':0,'new_episodes':0}
    csvout('diagnostics/INPUT_MANIFEST.csv',[dict(path=p,**v) for p,v in sorted(INPUTS.items())])
    changed=[p for p,v in INPUTS.items() if sha(ROOT/p)!=v['sha256']]
    if changed:
        raise RuntimeError('SAFETY_STOP: frozen diagnostic input changed')
    (OUT/'diagnostics/SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
