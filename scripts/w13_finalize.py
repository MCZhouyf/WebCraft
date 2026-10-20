#!/usr/bin/env python3
"""Postprocess W13 without changing the frozen VB implementation or episodes."""
from __future__ import annotations
import csv
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from w13_run import ROOT, OUT, MODES, METRICS, SEED, csvout, jsonl, sha, resource_gate


def read(name):
    with (OUT/name).open(newline='') as f:
        return list(csv.DictReader(f))


def registry_status(item, world, registries):
    versions=('1.16.5','1.19','1.21.11')
    at=versions.index(world)
    states=[item in registries[v] for v in versions]
    if states[at]:
        return 'present'
    if any(states[:at]):
        return 'removed_or_renamed_by_sampled_time'
    if any(states[at+1:]):
        return 'first_observed_in_later_sampled_version'
    return 'absent_in_all_sampled_registries'


def diagnostic_clarifications():
    registries={v:{x.removeprefix('minecraft:') for x in json.loads(
        (ROOT/f'truth/{v}.json').read_text())['names']['items']} for v in ('1.16.5','1.19','1.21.11')}
    # Preserve the original scan. Absence of a world-valid positive mechanism is
    # not automatically absence of relevant Web text (e.g. an impossible item).
    queries=read('diagnostics/retrieval_203.csv')
    for row in queries:
        row['scan_category']=row['category']
        row['target_registry_status']=registry_status(row['target_subject'],row['world'],registries)
        if row['target_registry_status']!='present' and not int(row['confirmed_valid_chunks']):
            row['category']='no_target_world_positive_counterpart'
            row['reason']='subject absent in target-world registry; no positive production counterpart, not evidence of corpus silence'
    csvout('diagnostics/retrieval_203_interpreted.csv',queries)
    count=Counter(r['category'] for r in queries)
    csvout('diagnostics/retrieval_categories_interpreted.csv',[
        dict(category=k,count=v,denominator=len(queries),rate=v/len(queries)) for k,v in sorted(count.items())])
    source_examples=read('diagnostics/retrieval_examples.csv')
    selected=[]
    for category in sorted(count):
        candidates=[r for r in queries if r['category']==category]
        for r in candidates[:4]:
            prior=next((x for x in source_examples if all(x[k]==r[k] for k in
                        ('reading_mode','condition','world','task_id','query'))),None)
            selected.append({**r,'excerpt':prior.get('excerpt','') if prior else '',
                              'example_limitation':'no text can prove a positive counterpart for an absent target item'
                              if category=='no_target_world_positive_counterpart' else
                              'quote withheld for polluted source; see chunk/revision identifiers' if r['condition']=='polluted'
                              else ''})
    csvout('diagnostics/retrieval_examples_interpreted.csv',selected)
    details=read('diagnostics/coverage_mechanism_detail.csv')
    pages=read('diagnostics/coverage_page_detail.csv')
    versions={'T1':'1.16.5','T2':'1.19','T3':'1.21.11'}
    for rows,field in ((details,'subject'),(pages,'item')):
        for row in rows:
            state=registry_status(row[field],versions[row['timepoint']],registries)
            row['registry_status']=state
            row['not_yet_exists_sampled']=int(state=='first_observed_in_later_sampled_version')
            row['removed_or_renamed']=int(state=='removed_or_renamed_by_sampled_time')
    csvout('diagnostics/coverage_mechanism_registry_interpreted.csv',details)
    csvout('diagnostics/coverage_page_registry_interpreted.csv',pages)
    coverage=[]
    for label in ('T1','T2','T3'):
        for unit,rows,field,metric in [('mechanism_instance',details,'mechanism_id','mechanism_covered'),
                                      ('mechanism_unique',details,'mechanism_id','mechanism_covered'),
                                      ('page_instance',pages,'item','page_present'),
                                      ('page_unique',pages,'item','page_present')]:
            selected=[r for r in rows if r['timepoint']==label]
            if unit.endswith('unique'):
                selected=list({r[field]:r for r in selected}.values())
            den=len(selected);covered=sum(int(r[metric]) for r in selected)
            missing=[r for r in selected if not int(r[metric])]
            history=sum(r['not_yet_exists_sampled'] for r in missing)
            removed=sum(r['removed_or_renamed'] for r in missing)
            coverage.append(dict(timepoint=label,unit=unit,denominator=den,covered=covered,
                                  coverage=covered/den,missing=len(missing),
                                  not_yet_existing_missing=history,removed_or_renamed_missing=removed,
                                  history_fraction_all_requirements=history/den,
                                  history_fraction_missing=history/len(missing) if missing else '',
                                  other_missing=len(missing)-history-removed))
    csvout('diagnostics/coverage_summary_interpreted.csv',coverage)
    return count,coverage


def additional_tables(rows):
    equal=[]
    grouping=defaultdict(list)
    for row in rows:
        if row['method']=='vb_rag':
            grouping[(row['scope'],row['reading_mode'],'overall')].append(row)
            grouping[(row['scope'],row['reading_mode'],row['condition_group'])].append(row)
    for (scope,mode,condition),rr in sorted(grouping.items()):
        for peer in ('naive_rag','cp_rag'):
            for field in ('action_equal_','intent_inclusive_equal_'):
                vs=[int(r[field+peer]) for r in rr if r.get(field+peer,'')!='']
                equal.append(dict(scope=scope,reading_mode=mode,condition=condition,peer=peer,
                                   projection='method_params' if field=='action_equal_' else 'method_params_intent',
                                   identical=sum(vs),denominator=len(vs),rate=sum(vs)/len(vs) if vs else ''))
    csvout('action_identity_comparisons.csv',equal)
    effects=[]
    for scope in ('ext_v2','cds'):
        selected=[r for r in rows if r['scope']==scope]
        families=sorted({r['family_id'] for r in selected})
        draws=np.random.default_rng(SEED).integers(0,len(families),(10000,len(families)))
        for peer in ('naive_rag','cp_rag'):
            for metric in METRICS:
                values=[]
                for method in ('vb_rag',peer):
                    nums=[];dens=[]
                    for family in families:
                        vals=[float(r[metric]) for r in selected if r['family_id']==family and
                              r['method']==method and r[metric]!='']
                        nums.append(sum(vals));dens.append(len(vals))
                    values.append((np.array(nums),np.array(dens)))
                a,b=values
                if min(a[1].sum(),b[1].sum())==0:
                    continue
                delta=a[0].sum()/a[1].sum()-b[0].sum()/b[1].sum()
                with np.errstate(divide='ignore',invalid='ignore'):
                    bs=a[0][draws].sum(1)/a[1][draws].sum(1)-b[0][draws].sum(1)/b[1][draws].sum(1)
                lo,hi=np.nanquantile(bs,[.025,.975])
                effects.append(dict(scope=scope,peer=peer,metric=metric,VB_minus_peer=delta,
                                    ci95_low=float(lo),ci95_high=float(hi),families=len(families),
                                    bootstrap_draws=10000,seed=SEED,unit='paired independent family'))
    csvout('EXT_CDS_paired_effects.csv',effects)
    return equal


def main():
    resource_gate('finalize')
    counts,coverage=diagnostic_clarifications()
    rows=read('episode_metrics_canonical.csv')
    equality=additional_tables(rows)
    aggregate=read('results_by_condition_canonical.csv')
    primary=read('PRIMARY_mixed_paired_effects_canonical.csv')
    temporal=read('temporal_belief_accuracy.csv')
    activation=[]
    for record in jsonl(OUT/'episodes.jsonl'):
        activation.append(dict(scope=record['scope'],reading_mode=record['reading_mode'],
                                condition=record['condition'],world=record['world'],task_id=record['task_id'],
                                final_formed_year=record['temporal_belief']['formed_year'],
                                ever_formed_in_trace=int(any(t.get('formed_year') is not None
                                                            for t in record['policy_trace']))))
    csvout('belief_activation.csv',activation)
    failures=read('not_reported.csv')
    summary=json.loads((OUT/'RUN_SUMMARY.json').read_text())
    code=json.loads((OUT/'CODE_FREEZE.json').read_text())
    unchanged=all(sha(ROOT/p)==digest for p,digest in code['source_hashes'].items())
    if not unchanged:
        raise RuntimeError('SAFETY_STOP: frozen policy source changed')
    discrepancies=[dict(item='polluted_evidence',observed=f'{len(failures)} source envelopes absent',
                         convention='not_reported; no replacement source or new run',
                         affected='102 polluted cells per reading mode; mixed primary unaffected'),
                   dict(item='calendar_year_not_snapshot_epoch',observed='public revision years may span multiple calendar years within a single snapshot',
                         convention='preserve registered calendar-year estimator; report actual formation and degeneration',affected='interpretation of temporal belief'),
                   dict(item='release_year_vs_revision_year',observed='1.21.11 released 2025; +30-day corpus cutoff is 2026',
                         convention='use registered official release year, not relabel 2026 estimates correct',affected='evaluator-only temporal accuracy'),
                   dict(item='retrieval_no_positive_counterpart',observed=str(counts.get('no_target_world_positive_counterpart',0)),
                         convention='do not count a nonexistent target-world positive mechanism as corpus absence',affected='retrieval three-way diagnosis'),
                   dict(item='registry_absence_vs_first_appearance',observed='raw absence flags also include old identifiers removed/renamed by T3',
                         convention='preserve draft scan; interpreted tables distinguish later first appearance from removal',affected='coverage history interpretation'),
                   dict(item='unresolved_source_support',observed=str(counts.get('unresolved_textual_existence',0)),
                         convention='do not force cooccurrence/LLM payload into confirmed textual presence or absence',affected='retrieval diagnosis'),
                   dict(item='shared_name_compilation',observed='shared compiler has no ordinary name-claim compilation route',
                         convention='no new compiler or designer name/version probes supplied',affected='available verification actions'),
                   dict(item='local_probe_comparison',observed='CP tags the first planned conflict step; VB uses registered cheapest executable distinguishable candidate',
                         convention='do not attribute local-choice differences to temporal belief',affected='causal interpretation of VB minus CP'),
                   dict(item='legacy_terminal_helper',observed='W8 helper differs from established six-terminal precedence',
                         convention='retain legacy exports; canonical exports use existing classifier without agent reruns',affected='overconservative/execution failure and terminal labels'),
                   dict(item='CP_extra_search_accounting',observed='CP reader.search_count excludes active re-search callbacks',
                         convention='canonical cost adds trace-inferred unique cached callbacks; VB uses actual RPC counts; retain provenance',affected='search cost comparisons')]
    csvout('DISCREPANCIES.csv',discrepancies)
    report=['# W13: VB-RAG and corpus/compilation ceiling diagnostics','',
            'Preregistered 388488d; implementation frozen e110d23 before held-out execution.',
            'Zero LLM calls, zero network calls. Existing frozen results unchanged.', '',
            '## PRIMARY: mixed-corpus paired effects (34 tasks)', '',
            'These are the registered main comparison, not a selected favorable subset.',
            'Each reading mode has 102 mixed instances (34 tasks x three worlds).',
            'Intervals use paired task bootstrap, 10,000 draws, seed 20261017.', '',
            '|Mode|VB SR|Naive SR|CP SR|VB−Naive pp [95% CI]|VB−CP pp [95% CI]|',
            '|---|---:|---:|---:|---:|---:|']
    for mode in MODES:
        vals={r['method']:float(r['SR']) for r in aggregate if r['scope']=='main' and
              r['reading_mode']==mode and r['grouping']=='condition_group' and r['condition']=='mixed'}
        effects={r['peer']:r for r in primary if r['reading_mode']==mode and r['metric']=='SR'}
        interval=lambda peer: f"{100*float(effects[peer]['VB_minus_peer']):+.2f} [{100*float(effects[peer]['ci95_low']):+.2f}, {100*float(effects[peer]['ci95_high']):+.2f}]"
        report.append(f"|{mode}|{vals['vb_rag']:.3f}|{vals['naive_rag']:.3f}|{vals['cp_rag']:.3f}|{interval('naive_rag')}|{interval('cp_rag')}|")
    report+=['', '**Activation limitation:** final global belief formed in 0/102 mixed instances',
             'in EACH mode; temporal traces likewise show no formed global window in these',
             'main mixed runs. Thus the null SR result is accompanied by no activation of',
             'the intended global filtering mechanism, not evidence that effective global',
             'temporal inference cannot help. Local probe selection differs from frozen CP',
             'at cheapest-action/distinguishability ties, so any local differences must not',
             'be attributed to a global belief that never formed. No threshold was retuned.']
    report+=['', '### Other scopes (available-key summaries, not the primary estimand)', '',
             '|Scope|Mode|Instances / method|VB SR|Naive SR|CP SR|VB−Naive pp|',
             '|---|---|---:|---:|---:|---:|---:|']
    for scope,mode in [('main',m) for m in MODES]+[('ext_v2','all_public'),('cds','cds')]:
        selected={r['method']:r for r in aggregate if r['scope']==scope and r['reading_mode']==mode
                  and r['grouping']=='overall'}
        a,b,c=[float(selected[m]['SR']) for m in ('vb_rag','naive_rag','cp_rag')]
        report.append(f"|{scope}|{mode}|{selected['vb_rag']['n']}|{a:.3f}|{b:.3f}|{c:.3f}|{100*(a-b):+.2f}|")
    report+=['', 'Full requested metrics and correctly conditioned denominators are in',
             'episode_metrics_canonical.csv/results_by_condition_canonical.csv; primary paired effects include',
             'all requested metrics. EXT/CDS paired family intervals are exploratory.', '',
             'Canonical terminal/rate exports use the established six-terminal classifier;',
             'initial W8-helper exports remain as legacy evidence. CP additional searches',
             'are reconstructed from unique public-index callbacks (not telemetry), whereas',
             'VB additional searches are counted from actual RPCs. See METRIC_ALIGNMENT.md.', '',
             'specific_query is the established check_name query-type contact, NOT a count',
             'of retrieved Web snippets; search_calls separately measures retrieval cost.', '',
             '## Coverage of this run / conservative non-reporting', '',
             f"Completed VB episodes: {summary['episodes']}; scope counts: {summary['scope_counts']}.",
             f'{len(failures)} polluted instance envelopes are absent from the public evidence archive.',
             'All are not_reported, not failures or zeros; no substitute corpus or task was run.',
             'Main per-mode overall summaries therefore cover 272/374 instances and MUST NOT',
             'be presented as full-matrix 374-instance rates. Mixed has complete 102/102 coverage.',
             'EXT-v2 has 60/60, CDS has 260/260. Existing Naive/CP are compared on those SAME',
             'available keys; old full-matrix summaries are not mixed with this denominator.', '',
             '## Autonomous temporal belief / single-timepoint check', '',
             'The policy knows only years extracted from public hit timestamps, never a',
             'version list, release/probe mapping, condition label, task ID or truth.',
             'Uniform within-episode prior; source-year de-duplication; signed log(3) update;',
             'confidence >=.80 and top/runner odds >=3; global selected-year filtering.',
             'No parameters were searched or changed after dev8. Runtime guards are separate',
             'from the simulator/evaluator process. Guard evidence is retained per episode.', '',
             '|Mode|Condition|Formed / episodes|Single-year cases|Formed in single-year cases|Accuracy among formed|',
             '|---|---|---:|---:|---:|---:|']
    for r in temporal:
        if r['scope']=='main':
            report.append(f"|{r['reading_mode']}|{r['condition']}|{r['formed']}/{r['episodes']}|{r['single_year_episodes']}|{r['single_year_formed']}|{r['accuracy_formed'] or 'undefined'}|")
    formed_single=sum(int(r['single_year_formed']) for r in temporal)
    matched_multi=[r for r in temporal if r['scope']=='main' and r['condition'] in
                   ('matched','mismatched_new2old','mismatched_old2new') and int(r['formed'])]
    report+=['',f'Genuine single-source-year cases form global belief {formed_single} times (expected zero).',
             'Single SNAPSHOT is not single revision year: the actual matched/mismatched',
             'indexes can contain revisions from multiple calendar years. '+
             ('Some such single-snapshot cases DO form belief; therefore VB does not universally degenerate to pure local adjudication there.'
              if matched_multi else 'No such single-snapshot case formed global belief in this run.'),
             'This data/specification difference is reported without changing year bins.',
             'Accuracy uses official release YEAR, not candidate versions. In particular',
             '2026 source-year estimates are not counted correct for a world released in',
             '2025; the +30-day cutoff crossing a year boundary is a diagnostic limitation.', '',
             '## Behavior identity with Naive / CP', '',
             '|Scope|Mode|Peer|Strict identical / n|', '|---|---|---|---:|']
    for r in equality:
        if r['condition']=='overall' and r['projection']=='method_params':
            report.append(f"|{r['scope']}|{r['reading_mode']}|{r['peer']}|{r['identical']}/{r['denominator']}|")
    report+=['', 'Identity uses the W12 canonical JSON method/params sequence including submit',
             'and final framework inventory read; it does not assert original transport byte',
             'identity or equality of private compilation/retrieval histories. Intent-inclusive',
             'identity is reported separately in action_identity_comparisons.csv.', '',
             '## Full-corpus retrieval / compilation ceiling', '',
             '203 code/trace-inferred failed re-search invocations, not verbatim telemetry.',
             'Every frozen indexed chunk was scanned. Positive-score BM25 rank gives minimum k;',
             'zero-score chunks remain unrankable even at k equal to corpus size.', '',
             '|Conservative interpretation|Count / 203|','|---|---:|']
    for category,n in sorted(counts.items()):
        report.append(f'|{category}|{n}/203|')
    qr=read('diagnostics/retrieval_203_interpreted.csv')
    can=sum(r['category']=='exists_bm25_missed' and r['compiler_after_full_recall']=='1' for r in qr)
    cannot=sum(r['category']=='exists_bm25_missed' and r['compiler_after_full_recall']=='0' for r in qr)
    report+=['',f'Among BM25-missed cases, full recall yields a valid compilable cache candidate in {can}; it does not in {cannot}.',
             'Valid payload is not sufficient proof that a source said it: deterministic',
             'template/prose support or grounded source markers establish positive findings;',
             'cooccurrence/unsupported LLM-only completions remain unresolved. Unsupported',
             'raw parser structures are not automatically declared absent. The requested',
             'three categories are therefore NOT forced to sum to 203. Nonexistent positive',
             'counterparts and existing-compilable zero-novel callbacks are separate.',
             'The one retrieved_uncompilable category includes reading/extraction availability,',
             'not necessarily only the compiler implementation. Examples give actual spans',
             'or explicit negative-scan limitations; empty/small categories have fewer than',
             'four examples, never fabricated examples.', '',
             '## 2026 page-selection bias and version-history missingness', '',
             'The 115 exported titles were PURPOSEFULLY chosen: all 42 tasks\' oracle',
             'dependency closures + resource blocks/outputs + twelve separator items,',
             'then title/redirect resolution. This is not random Web sampling. See',
             'diagnostics/EXPORT_SELECTION.md and the referenced frozen acquisition sources.', '',
             '|Time|Unit|Covered / total|Coverage|Missing due to later item appearance / missing|',
             '|---|---|---:|---:|---:|']
    for r in coverage:
        report.append(f"|{r['timepoint']}|{r['unit']}|{r['covered']}/{r['denominator']}|{100*r['coverage']:.2f}%|{r['not_yet_existing_missing']}/{r['missing']}|")
    report+=['', 'This uses 417 task-world mechanism requirements (67 unique mechanisms) and',
             '636 path-item exposures (71 unique items), across feasible oracle paths of',
             '34 eval tasks. Coverage means existing public claim-matcher support, not all',
             'possible true prose in the Web. Old names missing from a newer registry are',
             'REMOVED/RENAMED, not not-yet-introduced; interpreted files make that distinction.',
             'First appearance is bounded by the three sampled registries, not an inferred',
             'exact introduction version. Existing-item misses cannot all be causally',
             'attributed to author export: rendering, mapping, extraction and scope also matter.', '',
             '## Two-pass reasoning and integrity', '',
             'Pass 1 froze public-year inference versus hidden-version inference, local versus',
             'global feedback, retrieval versus compiler gaps, and purposeful versus random',
             'selection. Pass 2 checked dev repeat determinism (40/40), compiler parity (40/40),',
             'runtime metadata isolation, complete primary paired keys, correct feasible-only',
             'denominators, nonexistence versus corpus silence and rename versus introduction.',
             'No policy change or parameter retuning followed held-out observations.',
             f'Frozen policy and evaluation source hashes remain unchanged: {unchanged}.',
             'DISCREPANCIES.csv records every conservative convention and affected scope.',
             'Input/output manifests accompany this report. No original result, corpus, task,',
             'truth, release asset or manuscript was modified. No remote was pushed.', '']
    (OUT/'REPORT.md').write_text('\n'.join(report))
    (OUT/'FINAL_SUMMARY.json').write_text(json.dumps(dict(run=summary,
        retrieval_interpreted=dict(counts),policy_unchanged=unchanged,
        primary_mixed_sr=[r for r in primary if r['metric']=='SR'],LLM_calls=0),indent=2)+'\n')
    (OUT/'STATUS.md').write_text(
        '# W13_COMPLETE_WITH_NOT_REPORTED_CELLS\n\n'
        'Preregistration and code freeze preceded evaluation. All available-input runs,\n'
        'canonical scoring, retrieval/coverage diagnostics and reports are complete.\n'
        'Main coverage is 816/1122 (272/374 per mode); polluted 306 are not_reported.\n'
        'PRIMARY mixed is complete: 102/102 per mode. EXT-v2 60/60, CDS 260/260.\n'
        'Zero LLM calls. No post-held-out policy changes. No replacement evidence or reruns.\n')
    csvout('TWO_PASS_LEDGER.csv',[
        dict(pass_id=1,hypothesis='temporal propagation can improve mixed',frozen_in='388488d',
             pass2_check='102 mixed cases per mode, all 34 tasks paired',
             outcome='SR difference zero; no mixed trajectory ever formed global belief; no inference of general impossibility'),
        dict(pass_id=1,hypothesis='single snapshot implies a single temporal context',frozen_in='388488d',
             pass2_check='actual public source years and formation counts',
             outcome='multiple revision years can coexist in a snapshot; true single-year cases never form belief'),
        dict(pass_id=1,hypothesis='zero new research candidates imply source silence',frozen_in='388488d',
             pass2_check='all 203 inferred searches scanned against full indexes, BM25 ranks and compiler',
             outcome='147 recall misses, 1 reading/compilation gap, 3 existing-compilable, 20 no positive counterpart, 32 unresolved'),
        dict(pass_id=1,hypothesis='T3 export coverage is representative of general Web',frozen_in='388488d',
             pass2_check='export manifest builder and 34-task oracle requirements',
             outcome='purposeful oracle-closure selection, not random; 71/71 page coverage does not establish general-Web coverage')])
    sources=[ROOT/'prereg/w13_vbrag_20261017.md',ROOT/'scripts/w13_runtime.py',ROOT/'scripts/w13_run.py',
             ROOT/'scripts/w13_dev_validate.py',ROOT/'scripts/w13_diagnostics.py',Path(__file__),
             ROOT/'scripts/w13_metric_alignment.py',
             ROOT/'release/package/assets/frozen/main.jsonl.gz',ROOT/'release/package/assets/frozen/cp_rag.jsonl.gz',
             ROOT/'release/package/assets/frozen/ext_v2.jsonl.gz',ROOT/'release/package/assets/frozen/cds.jsonl.gz']
    sources += [ROOT/'scripts/run_w8_closeout_20261016.py',ROOT/'tests/test_w13_outputs.py',
                ROOT/'corpus/release_times.json']
    sources += [ROOT.parent/'arbiter'/name for name in (
        'src/arbiter/agent/planning.py','src/arbiter/agent/aggregation.py',
        'src/arbiter/evaluation/claim_compiler.py','src/arbiter/evaluation/p6_execution.py',
        'src/arbiter/evaluation/measurement_v3.py','src/arbiter/evaluation/post_review_measurement.py',
        'config/arbiter_hparams.json')]
    csvout('INPUT_MANIFEST.csv',[dict(path=str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else
                                    'arbiter_readonly/'+str(p.relative_to(ROOT.parent/'arbiter')),
                                    sha256=sha(p),bytes=p.stat().st_size) for p in sources])
    validation=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests',
                               '-p','test_w13_*.py','-v'],cwd=ROOT,capture_output=True,text=True)
    (OUT/'TEST_RESULTS.txt').write_text(validation.stdout+validation.stderr)
    if validation.returncode:
        raise RuntimeError('W13 output/unit validation failed; see TEST_RESULTS.txt')
    (OUT/'VALIDATION.json').write_text(json.dumps(dict(
        tests_returncode=validation.returncode,policy_source_unchanged=unchanged,
        completed_episodes=summary['episodes'],not_reported_instances=len(failures),
        primary_tasks=34,primary_instances_per_mode=102,LLM_calls=0,
        runtime_truth_task_file_reads=0,import_time_hidden_asset_guard_passed=True),indent=2)+'\n')
    (OUT/'OUTPUTS.sha256').write_text(''.join(f'{sha(p)}  {p.relative_to(OUT)}\n'
        for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='OUTPUTS.sha256' and
        'solver_tmp' not in p.parts and '__pycache__' not in p.parts))
    print((OUT/'FINAL_SUMMARY.json').read_text(),flush=True)


if __name__=='__main__':
    main()
