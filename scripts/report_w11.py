#!/usr/bin/env python3
"""Render W11 evidence and hash inventory; never execute experimental episodes."""
from w11_common import *
import subprocess


def table(values,fields):
    def clean(v):return str(v).replace('|',' / ').replace('\n',' ')
    return ['| '+' | '.join(fields)+' |','|'+'|'.join('---' for _ in fields)+'|',
            *['| '+' | '.join(clean(r.get(k,'')) for k in fields)+' |' for r in values]]


def main():
    resources('report')
    summary=json.loads((OUT/'CONTACT_SUMMARY.json').read_text())
    roots=read(OUT/'root_cause_cases.csv');checks=read(OUT/'ordering_source_checklist.csv')
    bounds=read(OUT/'attribution_sensitivity_bounds.csv')
    counts=read(OUT/'full_matrix_contact_counts.csv')
    episodes=read(OUT/'episode_attribution_sensitivity.csv')
    assert summary['frozen_records']==summary['original_annotations_validated']==11220
    assert len(counts)==len(bounds)==720 and summary['interval_rows']==3240
    overall=[r for r in bounds if r['scope']=='overall']
    changes=[r for r in bounds if f"{float(r['frozen_rate']):.3f}"!=f"{float(r['repaired_rate']):.3f}"]
    for cell in changes:
        cell['frozen_3dp']=f"{float(cell['frozen_rate']):.3f}"
        cell['repaired_3dp']=f"{float(cell['repaired_rate']):.3f}"
        cell['delta_pp_1dp']=f"{float(cell['delta_repaired_pp']):.1f}"
    write('changed_repaired_reported_cells.csv',changes)
    old110={r['method']:r for r in read(ROOT/'reports/paperA/w10_release_final/impact/common110_discriminative_contact.csv')}
    common=read(OUT/'common110_repaired_contact.csv')
    for r in common:
        r['W9_replay_events']=int(round(float(old110[r['method']]['counterfactual'])*110))
        r['W9_replay_rate']=old110[r['method']]['counterfactual']
    write('common110_frozen_replay_repaired_comparison.csv',common)
    stats=[]
    for metric in ('action','contact'):
        numerator=summary[metric+'_sensitive_episodes']
        stats.append({'metric':metric,'affected_episodes':numerator,'denominator':11220,
                      'fraction':numerator/11220,'percent':100*numerator/11220})
    write('sensitivity_population_counts.csv',stats)
    components=[]
    for mode,method in sorted({(e['reading_mode'],e['method']) for e in episodes}):
        values=[e for e in episodes if e['reading_mode']==mode and e['method']==method]
        components.append({'reading_mode':mode,'method':method,'denominator':len(values),
                           'action_events':sum(int(e['specific_action']) for e in values),
                           'query_events':sum(int(e['specific_query']) for e in values),
                           'query_or_action_events':sum(int(e['specific_contact']) for e in values)})
    write('contact_component_counts.csv',components)
    component_index={(c['reading_mode'],c['method']):c for c in components}
    pr=component_index[('all_public','probe_then_rag')]
    na=component_index[('all_public','naive_rag')]
    ar=component_index[('all_public','arbiter')]
    pa=component_index[('all_public','parametric')]
    oracle=component_index[('all_public','oracle')]
    cancel=next(c for c in read(OUT/'full_matrix_paired_intervals.csv')
                if c['reading_mode']=='all_public' and c['scope']=='overall'
                and c['method_A']=='arbiter' and c['method_B']=='naive_rag' and c['metric']=='specific_contact')
    affected_tasks=sorted({e['task_id'] for e in episodes if e['contact_attribution_sensitive']=='1'})
    # Re-run only the small audit unit tests and retain their actual output.
    test=subprocess.run([sys.executable,str(ROOT/'scripts/test_w11_attribution.py')],
                        cwd=ROOT,text=True,capture_output=True,check=True)
    (OUT/'unit_tests.txt').write_text(test.stdout+test.stderr)
    hypothesis=read(OUT/'hypothesis_ledger_pass1.csv')
    for h in hypothesis:
        h['pass2']={
            'different planner source':'excluded: ff3bf4a, working and W9 planning/core/decision bytes equal',
            'different compiled evidence/operator insertion order':'excluded for nine cases: actual bundle/compiled operator order/weighted inventory state equal',
            'set-ordered MILP balance rows / alternative optima':'supported causally: change only PYTHONHASHSEED, numeric first-MIP matrix fixed; both historical first choices recovered for 9/9',
            'filesystem ordering':'excluded on inspected selection path; no enumeration in these functions',
            'floating-point/objective differences':'different numeric inputs excluded: equal double coefficients, invariant first-MIP objective/matrix; native arithmetic may still respond to ordering',
            'solver runtime source outside PYTHONHASHSEED':'not eliminated historically: original ordered MPS, hash seed and native solve trace not archived; repeated same-seed tests agree'
        }[h['hypothesis']]
    write('hypothesis_ledger_second_pass.csv',hypothesis)
    source=json.loads((OUT/'source_quotes.json').read_text())
    quotes=['# Original Selection Logic and Sort Keys','',
            'These are original source bytes, not a patched planner. Numeric line references and source hashes are retained.','']
    for q in source:
        labelled_source='\n'.join(line.rstrip() for line in q['source'].splitlines())
        quotes.extend([f"## {q['file']}:{q['start_line']}",'',f"SHA-256: `{q['sha256']}`",'',
                       '```python',labelled_source,'```',''])
    (OUT/'SOURCE_LOGIC.md').write_text('\n'.join(quotes).rstrip()+'\n')
    rate=['# Frozen Full-Matrix Discriminative Contact Counts','',
          'Frozen labels, not repaired labels. Reading modes remain separate; no pooled three-mode performance rate. '
          'Every exact condition/world cell has 34 tasks, within-mode overall has 374 instances per method. '
          'Numerators are episodes with at least one matching action/query.','',
          *table(counts,['reading_mode','scope','condition','world','method','metric','events','denominator','rate','event_flag'])]
    (OUT/'FULL_MATRIX_COUNTS.md').write_text('\n'.join(rate)+'\n')
    lines=['# W11: W10 Replay Root Causes and Attribution Sensitivity','',
        'Registration: `3e47a4a`, registered before new calculations; W10 reference: `cec4835`. '
        'This post-review analysis uses zero model calls and zero new episodes/world actions. '
        'Shared `/root/autodl-tmp/server.lock` held with exclusive flock; nice 19, CPU only, one worker, '
        '2 GiB plan plus 8 GiB headroom. No frozen file, reported number, service or unrelated process changed; no remote push.','',
        '## 0--1. Source-Level Root Causes','',
        '**Conclusion 1 for all nine cases: an established source-level unfixed alternative-optimum tie-break.** '
        'The exact source is `BeliefPlanner._solve_once`, `agent/planning.py:170--173`: material-balance '
        'constraints are inserted by iterating a set. Lines 186--195 minimize cost/risk only, call CBC, '
        'and accept its count vector without a canonical optimal-solution key. Candidate support '
        'exclusion at 125--140 and truncation to five propagate this choice to policy selection. '
        'Naive/Verify-All use plans[0] (`core.py:181--185`); ARBITER scores the first-step set '
        '(`core.py:166--177,193--218`). Its own max(score,id) is ordered, but the preceding candidate '
        'set is not canonical. `DecisionController.choose` also resolves equal plan-success scores '
        'with the first np.argmax result, inheriting candidate enumeration.','',
        'Two reasoning passes are preserved in `hypothesis_ledger_pass1.csv` and '
        '`hypothesis_ledger_second_pass.csv`. Source quotes and all original keys/iteration logic '
        'are printed verbatim in [SOURCE_LOGIC.md](SOURCE_LOGIC.md), with hashes and line numbers.','',
        'Causal pure-function test: seven distinct logged planning-state fixtures, five isolated '
        'tests each (hash seeds 0,1,2,3,0), **35 tests, no world/agent loop**. In each fixture '
        'the first-MIP numeric objective and constraint multiset are identical; only row order changes. '
        'Four row-order fingerprints result. Both observed selections are recovered for **9/9** '
        'cases, including the pure VOI/controller decision on the already solved candidate sets. '
        'The repeated seed-0 candidate sets are identical. Actual original/post-run versus packaged '
        'evidence, operator ordering and weighted state were separately cross-checked and match for '
        'all nine. Planning/core/decision source bytes also match ff3bf4a and W9.','',
        '**Historical qualification:** this establishes the source-level causal mechanism and recovers '
        'both observed branches. It does not recover the original process hash seed, ordered MPS or '
        'CBC pivots: those were not archived. In particular, a standalone seed-0 captured-state solve '
        'need not identify the exact historical long-running process state. Python hash control is '
        'not a mathematical full ordering of native-solver optima. No claim of native-solver '
        'bitwise invariance or of a uniquely recovered historical seed is made.','',
        *table(checks,['source','answer','evidence']),'',
        '### Five pla_010 cases','',
        'The old and new IDs are distinct columns with the same resolved cobblestone/stick action, '
        'same output/count/cost/probability and tool requirements. The catalogue ID '
        '`cf_0cf08e1cf6803d3f` accepts blackstone/cobblestone in its input_options; the non-catalogue '
        '`cf_b55d59dd1aaa0ede` accepts cobblestone only. Given the logged inventory both resolve to '
        'the identical public craft_grid. The first has a false target-world catalogue label and '
        'is scored specific; the second is absent from the catalogue stale map and is not scored '
        'specific. No public behavior difference is required. Hash seed 0 selects the non-catalogue '
        'column in the tested fixtures; seeds 1/2/3 recover the catalogue column. These are diagnostic '
        'seed contrasts, not a declaration of the unrecorded original process seed. The logged craft '
        'succeeds in both versions of the trace.','',
        '### Four cost cases','',
        '- Probe-then-RAG / con_012 in both worlds: two equal-cost/equal-belief recipes consume seven '
        'leather and predict one leather_horse_armor. One has a wrong seven-filled-slots layout '
        '(cf_5ad9c837fedfc437), the other the successful shaped layout (cf_dd46c04e302f722c). '
        'The MILP input/output balances do not distinguish grid geometry. Candidate-order tie '
        'selection adds one failed craft before recovery, exactly the +1 action/+1 cost in each log.',
        '- ARBITER / eff_002: the first divergent candidate set includes or excludes the erroneous '
        'two-stick stone-pickaxe operator cf_1a0be0521107fc85 versus cf_b55d59dd1aaa0ede. '
        'The first-step VOI choice changes when support enumeration changes. The replay omits '
        'the failed craft, explaining 25 -> 24 actions and 29 -> 28 cost from the existing traces.',
        '- ARBITER / eff_008: after the same seven-action prefix, candidate support enumeration '
        'changes the VOI first-step set. The replay includes coal_ore->coal_ore and a '
        'wooden_pickaxe self-smelt claim before the redstone action; the old trace takes redstone '
        'directly. These two logged additions explain 8 -> 10. No new behavior is generated here.','',
        'The four cost cases concern different executed behavior; they are not counted as pure '
        'same-action attribution aliases merely because the MILP has equal optima.','',
        '## 2. Attribution Sensitivity on 11,220 Frozen Records','',
        *table(stats,['metric','affected_episodes','denominator','fraction','percent']),'',
        f"Every original specific-action/query annotation was validated against W9: **{summary['original_annotations_validated']:,}/{summary['frozen_records']:,}**. "
        f"Captured planning states validated: **{summary['captured_states_checked']:,}**; "
        f"episodes containing an eligible equivalent tie: **{summary['episodes_with_equivalent_tied_actions']:,}**. "
        f"All {summary['contact_sensitive_episodes']} label-sensitive records are all_public and involve only "
        f"{len(affected_tasks)} distinct task IDs: "+', '.join(affected_tasks)+'. '
        'This is not a count of independent mechanisms or independent task goals. '
        'Probabilities are checked against each logged probability_before (tolerance 1e-12); '
        'selected IDs, resolved public parameters and current resources must match. Any failed '
        'assertion would stop the audit, not change the rule.','',
        'Method x reading-mode x exact condition/world counts, including zero cells, are in '
        '`attribution_sensitivity_bounds.csv`; per-episode and per-candidate evidence is in '
        '`episode_attribution_sensitivity.csv` / `candidate_attributions.csv`.','',
        '**Bounds are a fixed-behavior local-attribution sensitivity envelope, not new experimental '
        'results or a claim that a joint alternative agent trajectory realizes every extreme.** '
        'Only same public action, expected output/count, cost, resources and equal objective '
        'coefficient qualify. Unequal-objective aliases are exported but excluded from the primary '
        'bounds. Changes in action layout, expected product, route or future belief are not imputed. '
        'Episode lower bounds retain any unavoidable positive action; a tie on one action does not '
        'erase another positive action. Reading-mode rates are never pooled.','',
        *table(overall,['reading_mode','method','metric','frozen_events','denominator','affected_episodes',
                        'lower_events','upper_events','lower_rate','upper_rate']),'',
        '## 3. Conditional Canonical Annotation Repair','',
        'Since step 0 has source-level conclusion 1, the registered full ascending key is implemented '
        'in `w11_contact_audit.selector_key` (no catalogue/truth access):','',
        '```python',
        '(op.method, op.output, op.output_count, op.action_cost, canonical(resolved_params),',
        ' canonical(sorted(op.requires)), canonical(op.input_options), op.mechanism_id)',
        '```','',
        'It selects the same attribution for an already equivalent logged action regardless of '
        'candidate order. Catalogue membership and outcome never enter the key. Exactly identical '
        'keys collapse trivially; distinct mechanism IDs form a final full-order discriminator. '
        'For the two stone-pickaxe aliases, canonical input_options choose the blackstone/cobblestone '
        'catalogue form. This is not selected to improve the rate.','',
        f"Repair changes the action/query episode label in **{summary['annotation_repaired_episodes']:,}** frozen records; "
        f"**{len(changes)}** method/mode/scope/metric cells change at three decimals. "
        'All exact new counts/rates and one-decimal pp deltas are in `attribution_sensitivity_bounds.csv`; '
        'rounded changed cells are separately exported in `changed_repaired_reported_cells.csv`.','',
        *table(common,['method','denominator','frozen_events','W9_replay_events','repaired_events',
                       'frozen_rate','W9_replay_rate','repaired_rate']),'',
        'The repaired column refers to the frozen main actions; the W9 column is its existing replay '
        'comparison, not a new W9-policy rerun. SR, HTR_all, declared plan-source HTR, terminal, '
        'semantic actions/cost and retrieval counts are not replaced. The four cost differences '
        'cannot be repaired by relabelling. No revised behavioral performance is claimed.','',
        'For future planning determinism, sorting material rows and operators is necessary hygiene '
        'but insufficient to prove a unique MILP optimum. A complete future selector must '
        'lexicographically choose the canonical operator-count vector within the primary-optimal '
        'solution set, then schedule ready actions by the same full key. That full-policy change '
        'is **not implemented or experimentally validated** here, because frozen methods and new '
        'episodes are prohibited. This task implements the requested fixed-log annotation repair only.','',
        'A stable label is not proof of construct validity: catalogue-level truth can distinguish '
        'two differently scoped assertions even when the logged action is identical and succeeds. '
        'The new sensitivity table must accompany comparisons, rather than treating a canonical '
        'choice as new evidence of harmful behavior.','',
        '## 4. Full-Matrix Comparisons','',
        f"`full_matrix_contact_counts.csv` has **{summary['counts_rows']}** rows: 10 methods x 3 reading modes x "
        '12 scopes (11 condition/world cells plus within-mode overall) x two metrics. '
        'The full matrix is usable: each cell has 34 tasks, each within-mode method summary 374 '
        'instances. The maximum comparable population is 374 per method per mode, not a mixture '
        'of 110-instance subsets or reading modes. See [FULL_MATRIX_COUNTS.md](FULL_MATRIX_COUNTS.md).','',
        f"`full_matrix_paired_intervals.csv` has **{summary['interval_rows']}** rows: all 45 executable-method pairs, "
        'each reading mode/scope and both specific_action / query-action OR. Difference is B minus A '
        'in percentage points. Task-paired bootstrap averages configurations within each task, '
        '10,000 shared resamples, seed 20261017, 95% percentile intervals. Raw A/B numerators, '
        'denominators and discordant event counts are retained.','',
        'These are exploratory paired intervals, not multiplicity-adjusted method rankings. '
        'Zero/1--4-event cells are marked; a zero-event empirical bootstrap interval is not proof '
        'that population risk is zero. Expanding the denominator does not guarantee enough '
        'events for each pair, so sparse cells must remain descriptive. Task-paired intervals '
        'are not evidence of independent mechanism families, and describe resampling uncertainty '
        'only; attribution uncertainty is reported separately in the registered bounds.','',
        '**Power and interpretation:** the full frozen all_public Naive/Majority/TruthFinder/Verify-All/'
        f"same-shell rows still have only {na['query_or_action_events']}/{na['denominator']} contact events; "
        f"ARBITER has {ar['query_or_action_events']}/{ar['denominator']}, Parametric variants "
        f"{pa['query_or_action_events']}/{pa['denominator']}, Oracle {oracle['query_or_action_events']}/{oracle['denominator']}. "
        f"Probe-then-RAG has {pr['query_or_action_events']}/{pr['denominator']} query/action contacts but only "
        f"**{pr['action_events']} action contact and {pr['query_events']} query contacts**. "
        'Its larger count is mostly the explicit name-identification probe, '
        'not evidence of harmful commitment. Component counts are exported separately. Expanding from '
        '110 to 374 does not cure event scarcity for the ordinary pipeline comparisons.','',
        f"An example of cancellation under the registered estimand: ARBITER versus Naive has {cancel['A_only_events']} A-only "
        f"and {cancel['B_only_events']} B-only instances, yet their within-task configuration-averaged differences cancel "
        f"and the overall paired-task interval is [{cancel['ci95_low_pp']},{cancel['ci95_high_pp']}] pp. "
        'This does not mean their per-configuration '
        'behavior is identical; consult the eleven exact-cell comparisons. The resampling rule was '
        'not changed to widen this interval.','',
        '## 5. Tests, Provenance and Reproduction','',
        'Five audit tests pass (canonical permutation invariance, truth/probability-independent key, '
        'unavoidable-positive bound, paired resampling, and AST absence of world/agent entrypoints). '
        'See `unit_tests.txt`. Resource snapshots and both reasoning ledgers are preserved. '
        'The same-seed source tests and matrix-invariant assertions are additional checks, '
        'not new experimental episodes.','',
        '```bash',
        'python3 scripts/w11_cases.py',
        'python3 scripts/w11_source_diagnostics.py',
        'python3 scripts/w11_decision_diagnostics.py',
        'python3 scripts/w11_input_provenance.py',
        'python3 scripts/w11_root_conclusions.py',
        'python3 scripts/w11_contact_audit.py',
        'python3 scripts/report_w11.py',
        '```','',
        'Run under the registered exclusive server.lock, CUDA_VISIBLE_DEVICES empty, nice 19, '
        'PYTHONDONTWRITEBYTECODE=1, one BLAS worker and PYTHONHASHSEED=0 (diagnostic children '
        'explicitly vary only the hash seed). Existing read-only working evidence is required. '
        'Every output is listed in OUTPUTS.sha256; frozen input/source hashes are in INPUTS.sha256. '
        'Result commit is the commit containing this report. No remote action is performed.']
    (OUT/'REPORT.md').write_text('\n'.join(lines).rstrip()+'\n')
    inputs=[ROOT/'prereg/w11_attribution_root_cause_20261017.md',
            ROOT/'data/tasks.jsonl',ROOT/'data/conflicts.jsonl',
            ROOT/'reports/paperA/w9_release_fix/replay/main_comparison.csv',
            ROOT/'release/package/assets/frozen/main.jsonl.gz',
            ROOT/'reports/paperA/w10_release_final/impact/nine_episode_pairs.json',
            ROOT/'paper/PAPER_A_DRAFT.md',
            *sorted((ROOT/'scripts').glob('w11_*.py')),ROOT/'scripts/report_w11.py',ROOT/'scripts/test_w11_attribution.py']
    (OUT/'INPUTS.sha256').write_text(''.join(f'{sha(p)}  {p.relative_to(ROOT)}\n' for p in inputs))
    outputs=sorted(p for p in OUT.rglob('*') if p.is_file() and p.name!='OUTPUTS.sha256')
    (OUT/'OUTPUTS.sha256').write_text(''.join(f'{sha(p)}  {p.relative_to(OUT)}\n' for p in outputs))
    print(json.dumps({'report':str((OUT/'REPORT.md').relative_to(ROOT)),**summary}))


if __name__=='__main__':main()
