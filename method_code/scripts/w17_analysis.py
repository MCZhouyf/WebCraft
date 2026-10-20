"""Post-review VI: registered CSV-only annotation and LOO analysis."""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import math
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent
OUT = ROOT / 'reports/main/post_review/w17_20261019'
REG = ROOT / 'prereg/w17_post_review_vi_20261019.md'
CONTACT = ('无', '查询', '执行', '无法确定')
PROP = ('否', '是', '无法确定')
UNKNOWN = '无法确定'
FILES = {
    'heldout_ann2': BASE / 'blind_sheet2_heldout80_annotated.csv',
    'cds_ann2': BASE / 'blind_sheet2_cds30_annotated.csv',
    'heldout_frozen': ROOT / 'reports/main/post_review/a19_human_audit_20261017/episode_alignment_80.csv',
    'cds_frozen': ROOT / 'reports/main/post_review/a20_audit_cds_scoring_20261017/cds_episode_alignment.csv',
    'a8': ROOT / 'reports/main/post_review/cds_agents_20261017/episode_metrics.csv',
    'w15_loo': BASE / 'webcraft/reports/paperA/w15/loo_results.csv',
    'w15_code': BASE / 'webcraft/scripts/w15_reanalysis.py',
    'w15_prereg': BASE / 'webcraft/prereg/w15_post_review_iv_20261017.md',
    'registration': REG,
    'lock': BASE / 'server.lock',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(name, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / name).open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def unique(rows, key):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f'Duplicate {key}')
    return result


def contact(label):
    label = label.strip()
    if label not in CONTACT:
        raise ValueError(f'Unregistered contact label: {label!r}')
    return label


def propagation(label, contact_label):
    label = label.strip()
    if not label:
        return '否' if contact_label == '无' else UNKNOWN
    if label not in PROP:
        raise ValueError(f'Unregistered propagation label: {label!r}')
    return label


def binary(label):
    return None if label == UNKNOWN else int(label != '无')


def wilson(successes, total):
    if not total:
        return None, None, None
    z = 1.959963984540054
    p = successes / total
    den = 1 + z*z / total
    center = (p + z*z / (2*total)) / den
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / den
    return p, max(0.0, center-half), min(1.0, center+half)


def detection(reference, prediction):
    valid = [(a, b) for a, b in zip(reference, prediction) if a is not None and b is not None]
    counts = Counter(valid)
    tp, fp, fn, tn = (counts[x] for x in [(1, 1), (0, 1), (1, 0), (0, 0)])
    p, pl, ph = wilson(tp, tp+fp)
    r, rl, rh = wilson(tp, tp+fn)
    return dict(n_eligible=len(valid), n_excluded=len(reference)-len(valid), tp=tp, fp=fp, fn=fn, tn=tn,
                precision_numerator=tp, precision_denominator=tp+fp, precision=p,
                precision_wilson_low=pl, precision_wilson_high=ph,
                recall_numerator=tp, recall_denominator=tp+fn, recall=r,
                recall_wilson_low=rl, recall_wilson_high=rh)


def kappa(left, right):
    if len(left) != len(right):
        raise ValueError('Unpaired kappa inputs')
    n = len(left)
    if not n:
        return dict(n=0, agreements=0, raw_agreement=None, expected_agreement=None, kappa=None)
    agreements = sum(a == b for a, b in zip(left, right))
    lm, rm = Counter(left), Counter(right)
    pe = sum(lm[k]*rm[k] for k in set(lm) | set(rm)) / n**2
    return dict(n=n, agreements=agreements, raw_agreement=agreements/n, expected_agreement=pe,
                kappa=(agreements/n-pe)/(1-pe) if pe < 1 else None,
                ann1_marginal=json.dumps(lm, ensure_ascii=False, sort_keys=True),
                ann2_marginal=json.dumps(rm, ensure_ascii=False, sort_keys=True))


def merged(a, b, kind, policy):
    if policy == 'mutually_determinate' and (a is None or b is None):
        return None
    if kind == 'union':
        return 1 if a == 1 or b == 1 else (0 if a == 0 and b == 0 else None)
    return 0 if a == 0 or b == 0 else (1 if a == 1 and b == 1 else None)


def join_cohort(cohort):
    a2 = unique(read_csv(FILES[cohort+'_ann2']), 'audit_id')
    old = unique(read_csv(FILES[cohort+'_frozen']), 'audit_id')
    if set(a2) != set(old):
        raise ValueError(f'{cohort}: mismatched audit IDs')
    if len(a2) != (80 if cohort == 'heldout' else 30):
        raise ValueError(f'{cohort}: unexpected sample size')
    result = []
    for audit_id in sorted(a2):
        a, b = old[audit_id], a2[audit_id]
        for key in ('method', 'condition', 'world', 'task_id'):
            if a[key] != b[key]:
                raise ValueError(f'{cohort}/{audit_id}: identity mismatch {key}')
        c1, c2, sc = contact(a['human_contact']), contact(b['contact_type']), contact(a['scorer_contact'])
        result.append(dict(cohort=cohort, audit_id=audit_id,
                           **{k:a[k] for k in ('method', 'condition', 'world', 'task_id')},
                           ann1_contact=c1, ann2_contact=c2, scorer_contact=sc,
                           ann1_propagation=propagation(a['human_propagation'], c1),
                           ann2_propagation=propagation(b['propagation'], c2),
                           ann2_propagation_raw=b['propagation'],
                           scorer_propagation=propagation(a['scorer_propagation'], sc)))
    return result


def annotation_analysis(joined):
    scores, merges, kappas, matrices, fps = [], [], [], [], []
    for cohort in ('heldout', 'cds'):
        rows = [r for r in joined if r['cohort'] == cohort]
        a1, a2, scorer = ([binary(r[k]) for r in rows] for k in ('ann1_contact', 'ann2_contact', 'scorer_contact'))
        ids = [r['audit_id'] for r in rows]
        scorer_positive = {key for key, value in zip(ids, scorer) if value == 1}
        ann2_positive = {key for key, value in zip(ids, a2) if value == 1}
        old_eligible_positive = {key for key, a, s in zip(ids, a1, scorer) if a is not None and s == 1}
        original_fp = {key for key, a, s in zip(ids, a1, scorer) if a == 0 and s == 1}
        for reference, values in [('ann2', a2), ('ann1_frozen_reference', a1)]:
            scores.append(dict(cohort=cohort, reference=reference, n_total=len(rows),
                               **detection(values, scorer),
                               scorer_positive_all=len(scorer_positive),
                               scorer_positive_ann1_determinate=len(old_eligible_positive),
                               ann2_positive_all=len(ann2_positive),
                               ann2_positive_in_scorer_all=len(ann2_positive & scorer_positive),
                               ann2_positive_in_original_32_scope=len(ann2_positive & old_eligible_positive),
                               ann2_positive_in_scorer_all_ids=';'.join(sorted(ann2_positive & scorer_positive)),
                               ann2_positive_in_original_32_scope_ids=';'.join(sorted(ann2_positive & old_eligible_positive))))
        for policy in ('mutually_determinate', 'partial_information'):
            for kind in ('union', 'intersection'):
                vals = [merged(a, b, kind, policy) for a, b in zip(a1, a2)]
                dispositions = {key: ('excluded' if v is None else ('true_positive' if v else 'false_positive'))
                                for key, v in zip(ids, vals) if key in original_fp}
                merges.append(dict(cohort=cohort, merge=kind, unknown_policy=policy, n_total=len(rows),
                                   **detection(vals, scorer), original_fp_n=len(original_fp),
                                   original_fp_to_tp=sum(v == 'true_positive' for v in dispositions.values()),
                                   original_fp_stays_fp=sum(v == 'false_positive' for v in dispositions.values()),
                                   original_fp_excluded=sum(v == 'excluded' for v in dispositions.values()),
                                   original_fp_to_tp_ids=';'.join(sorted(k for k,v in dispositions.items() if v == 'true_positive'))))
        for r in rows:
            if r['audit_id'] in original_fp:
                fps.append(dict(r, union_disposition='true_positive' if binary(r['ann2_contact']) == 1 else
                                ('excluded' if binary(r['ann2_contact']) is None else 'false_positive'),
                                intersection_disposition='false_positive' if binary(r['ann2_contact']) is not None else 'excluded'))
        known = [r for r in rows if UNKNOWN not in (r['ann1_contact'], r['ann2_contact'])]
        for kind, expected in [('contact_3way', .854 if cohort=='heldout' else .944),
                               ('contact_binary', .831 if cohort=='heldout' else 1.0)]:
            left = [r['ann1_contact'] if kind=='contact_3way' else binary(r['ann1_contact']) for r in known]
            right = [r['ann2_contact'] if kind=='contact_3way' else binary(r['ann2_contact']) for r in known]
            stats = kappa(left, right)
            kappas.append(dict(cohort=cohort, metric=kind, scope='mutually_contact_determinate',
                               n_excluded=len(rows)-len(known), **stats, author_expected_kappa=expected,
                               rounded3_matches_expected=round(stats['kappa'],3)==expected if stats['kappa'] is not None else False))
        for scope in ('all_mutually_determinate', 'union_contact_positive', 'intersection_contact_positive'):
            rr = [r for r in rows if UNKNOWN not in (r['ann1_propagation'], r['ann2_propagation'])]
            if scope != 'all_mutually_determinate':
                kind = scope.split('_')[0]
                rr = [r for r in rr if merged(binary(r['ann1_contact']), binary(r['ann2_contact']), kind, 'partial_information') == 1]
            stats = kappa([r['ann1_propagation'] for r in rr], [r['ann2_propagation'] for r in rr])
            kappas.append(dict(cohort=cohort, metric='propagation', scope=scope, n_excluded=len(rows)-len(rr),
                               **stats, author_expected_agreements=20 if cohort=='heldout' else 15,
                               author_expected_denominator=20 if cohort=='heldout' else 15,
                               author_count_matches=(stats['agreements']==stats['n']==(20 if cohort=='heldout' else 15))))
        for metric, levels in [('contact', CONTACT), ('propagation', PROP)]:
            for comparison, left, right in [('ann1_vs_ann2', 'ann1_', 'ann2_'), ('ann2_vs_scorer', 'ann2_', 'scorer_')]:
                counts = Counter((r[left+metric], r[right+metric]) for r in rows)
                for a in levels:
                    for b in levels:
                        matrices.append(dict(cohort=cohort, comparison=comparison, metric=metric,
                                             row_label=a, column_label=b, count=counts[(a,b)]))
    return scores, merges, kappas, matrices, fps


def bootstrap(pairs):
    families = sorted({r['family_id'] for r in pairs})
    sums = np.array([sum(r['delta'] for r in pairs if r['family_id']==f) for f in families])
    counts = np.array([sum(r['family_id']==f for r in pairs) for f in families])
    rng = np.random.Generator(np.random.PCG64(20261017))
    draws = rng.integers(0, len(families), size=(10000, len(families)))
    estimates = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return [float(v) for v in np.quantile(estimates, [.025, .975])]


def loo_analysis():
    rows = [r for r in read_csv(FILES['a8']) if r['method']=='react']
    maps = {c:unique([r for r in rows if r['condition']==c], 'instance_id') for c in ('empty', 'K')}
    if set(maps['empty']) != set(maps['K']) or len(maps['K']) != 52:
        raise ValueError('A8 ReAct empty/K pair coverage mismatch')
    pairs = []
    for key in sorted(maps['K']):
        a, b = maps['empty'][key], maps['K'][key]
        if a['family_id']!=b['family_id'] or {a['success'],b['success']}-{'0','1'}:
            raise ValueError('A8 family or binary metric mismatch')
        pairs.append(dict(instance_id=key, family_id=a['family_id'], empty_success=int(a['success']),
                          K_success=int(b['success']), delta=int(a['success'])-int(b['success'])))
    old = unique([r for r in read_csv(FILES['w15_loo']) if
                  (r['scope'],r['model'],r['method'],r['metric'],r['contrast'])==
                  ('CDS','GLM-5.2','react','SR','empty_minus_K')], 'excluded_family')
    families = sorted({r['family_id'] for r in pairs})
    if set(old) != set(families):
        raise ValueError('W15 family list mismatch')
    output = []
    for exclude in ['NONE'] + families:
        kept = [r for r in pairs if r['family_id']!=exclude]
        remaining = sorted({r['family_id'] for r in kept})
        means = [sum(r['delta'] for r in kept if r['family_id']==f)/sum(r['family_id']==f for r in kept) for f in remaining]
        equal_family = sum(means)/len(means)
        low, high = bootstrap(kept)
        frozen = float(old[exclude]['estimate']) if exclude in old else None
        output.append(dict(excluded_family=exclude, n_pairs=len(kept), n_families=len(remaining),
                           empty_successes=sum(r['empty_success'] for r in kept),
                           K_successes=sum(r['K_success'] for r in kept),
                           difference_numerator=sum(r['delta'] for r in kept),
                           corrected_episode_weighted_delta=sum(r['delta'] for r in kept)/len(kept),
                           ci95_low=low, ci95_high=high, crosses_zero=low<=0<=high,
                           equal_family_reconstruction=equal_family, frozen_w15_estimate=frozen,
                           reconstruction_abs_error=abs(equal_family-frozen) if frozen is not None else None,
                           draws=10000, seed=20261017))
    return output, pairs


def pct(x):
    return 'undefined' if x is None else f'{100*x:.2f}%'


def make_reports(scores, merges, kappas, loo, pairs):
    lines = ['# W17: second annotator versus frozen scorer', '',
             'Zero LLM calls; zero new episodes. Exact audit_id joins: held-out 80/80, CDS 30/30.',
             'All original inputs are retained; sample-based rates do not estimate population prevalence.', '',
             '## Scorer precision/recall', '',
             '| Cohort | Reference | Eligible | TP / FP / FN / TN | Precision [Wilson 95%] | Recall [Wilson 95%] |',
             '|---|---|---:|---|---|---|']
    for r in scores:
        lines.append(f"| {r['cohort']} | {r['reference']} | {r['n_eligible']} | {r['tp']}/{r['fp']}/{r['fn']}/{r['tn']} | "
                     f"{pct(r['precision'])} [{pct(r['precision_wilson_low'])}, {pct(r['precision_wilson_high'])}] | "
                     f"{pct(r['recall'])} [{pct(r['recall_wilson_low'])}, {pct(r['recall_wilson_high'])}] |")
    h = next(r for r in scores if r['cohort']=='heldout' and r['reference']=='ann2')
    lines += ['', f"Held-out annotator-2 positives: {h['ann2_positive_all']}. "
              f"{h['ann2_positive_in_original_32_scope']} fall within the original "
              f"{h['scorer_positive_ann1_determinate']} scorer-positive rows restricted to annotator-1-determinate cases. "
              f"The complete 80-row frozen scorer has {h['scorer_positive_all']} positives, "
              f"of which {h['ann2_positive_in_scorer_all']} are annotator-2-positive. "
              'Thus 32 and the full-cohort scorer-positive count have different denominators.', '',
              '## Merged human reference', '',
              '| Cohort | Policy | Merge | Eligible | Precision | Recall | Original FP -> TP / remain FP / excluded |',
              '|---|---|---|---:|---:|---:|---|']
    for r in merges:
        lines.append(f"| {r['cohort']} | {r['unknown_policy']} | {r['merge']} | {r['n_eligible']} | "
                     f"{pct(r['precision'])} | {pct(r['recall'])} | {r['original_fp_to_tp']} / {r['original_fp_stays_fp']} / {r['original_fp_excluded']} |")
    lines += ['', '## Inter-annotator agreement', '', '| Cohort | Metric | Scope | Agreement | Kappa |', '|---|---|---|---|---:|']
    for r in kappas:
        lines.append(f"| {r['cohort']} | {r['metric']} | {r['scope']} | {r['agreements']}/{r['n']} | "
                     f"{r['kappa']:.6f} |" if r['kappa'] is not None else
                     f"| {r['cohort']} | {r['metric']} | {r['scope']} | {r['agreements']}/{r['n']} | undefined |")
    lines += ['', 'The provided propagation counts are checked for both full determinate cohorts and contact-positive subsets.',
              'Blank propagation is inferred negative only for explicit no-contact; explicit unknown remains unknown.', '',
              '## LOO audit', '', 'See `loo_fix.md` and `loo_corrected.csv` for the weighting correction and retained denominators.', '',
              '## Files', '', '`joined_labels.csv` contains only IDs and labels, not raw evidence text or endpoints.',
              '`source_manifest.json` and `output_manifest.json` provide hashes; the analysis verifies unchanged inputs.',
              'The preregistration is `prereg/w17_post_review_vi_20261019.md` (commit 604e182).', '']
    (OUT/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    full, drops = loo[0], loo[1:]
    counts = Counter(r['family_id'] for r in pairs)
    code = FILES['w15_code'].read_text().splitlines()
    snippets = []
    for token in ('ordered = np.asarray', 'ordered.mean()', 'family_delta[family] =', 'point, low, high = interval(retained)'):
        snippets.extend((i+1,line) for i,line in enumerate(code) if token in line)
    lo, hi = min(r['corrected_episode_weighted_delta'] for r in drops), max(r['corrected_episode_weighted_delta'] for r in drops)
    oldlo, oldhi = min(r['frozen_w15_estimate'] for r in drops), max(r['frozen_w15_estimate'] for r in drops)
    text = ['# W15 CDS ReAct(GLM) empty-minus-K SR: denominator and estimand audit', '',
            f"Full frozen sample: {full['n_pairs']} paired instances; empty {full['empty_successes']}/{full['n_pairs']}, "
            f"K {full['K_successes']}/{full['n_pairs']}. Episode-weighted delta = {full['difference_numerator']}/{full['n_pairs']} "
            f"= {100*full['corrected_episode_weighted_delta']:.4f} pp.", '',
            f"The data contain {len(counts)} families: {json.dumps(dict(sorted(counts.items())), sort_keys=True)}.", '',
            '## Established cause', '',
            'W15 interval() takes an unweighted mean of family-level contrasts. The input is not reduced to 44 pairs before every exclusion. '
            'One family has eight pairs and the other eleven have four each. Equal-family averaging gives the eight-pair family half '
            'the per-instance weight of the four-pair families. This differs from the episode-weighted Table-4 estimand and '
            'from W15 registration language specifying pooled episode weighting.', '',
            f"The equal-family full estimate is {100*full['equal_family_reconstruction']:.4f} pp; "
            f"its old LOO range is [{100*oldlo:.4f}, {100*oldhi:.4f}] pp. "
            f"Every old exclusion is reproduced to absolute error <= {max(r['reconstruction_abs_error'] for r in drops):.3g}.", '',
            'Source: webcraft/scripts/w15_reanalysis.py, interval() and loo():', '', '```python']
    text += [f'{n}: {line}' for n,line in sorted(snippets)]
    text += ['```', '', '## Corrected Table-4-compatible row', '',
             '| Method / contrast | Full paired n | Full delta (pp) | Corrected LOO range (pp) | Retained pairs |',
             '|---|---:|---:|---|---|',
             f"| ReAct GLM / empty - K / SR | {full['n_pairs']} | {100*full['corrected_episode_weighted_delta']:.4f} | "
             f"[{100*lo:.4f}, {100*hi:.4f}] | {', '.join(map(str,sorted({r['n_pairs'] for r in drops})))} |", '',
             'Dropping the eight-pair rename family leaves 44 pairs; dropping any other family leaves 48. '
             'A 44-equivalent denominator in equal-family arithmetic is therefore not evidence that every exclusion actually used 44 episodes.', '',
             '| Excluded family | Retained n | empty / K successes | Numerator | Corrected delta (pp) | 95% family bootstrap (pp) | Old equal-family delta (pp) |',
             '|---|---:|---|---:|---:|---|---:|']
    for r in drops:
        text.append(f"| {r['excluded_family']} | {r['n_pairs']} | {r['empty_successes']} / {r['K_successes']} | "
                    f"{r['difference_numerator']} | {100*r['corrected_episode_weighted_delta']:.4f} | "
                    f"[{100*r['ci95_low']:.4f}, {100*r['ci95_high']:.4f}] | {100*r['frozen_w15_estimate']:.4f} |")
    text += ['', 'Corrected LOO intervals containing zero: '+(', '.join(r['excluded_family'] for r in drops if r['crosses_zero']) or 'none')+'.',
             'Existing W15 exports and reported numbers are preserved. The corrected row is a separately identified reanalysis.', '']
    (OUT/'loo_fix.md').write_text('\n'.join(text), encoding='utf-8')


def main():
    with FILES['lock'].open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = {name:sha(path) for name,path in FILES.items()}
        prereg_commit = subprocess.check_output(['git','log','-1','--format=%H','--',str(REG)], cwd=ROOT, text=True).strip()
        if not prereg_commit:
            raise ValueError('Preregistration must be committed before calculation')
        committed = subprocess.check_output(['git','show',f'{prereg_commit}:{REG.relative_to(ROOT)}'], cwd=ROOT)
        if hashlib.sha256(committed).hexdigest() != before['registration']:
            raise ValueError('Registration has uncommitted changes')
        OUT.mkdir(parents=True, exist_ok=True)
        joined = join_cohort('heldout') + join_cohort('cds')
        scores, merges, kappas, matrices, fps = annotation_analysis(joined)
        loo, pairs = loo_analysis()
        for name, rows in [('joined_labels.csv',joined), ('scorer_vs_ann2.csv',scores),
                           ('merged_precision.csv',merges), ('kappa_check.csv',kappas),
                           ('confusion.csv',matrices), ('original_fp.csv',fps),
                           ('loo_corrected.csv',loo), ('loo_pairs.csv',pairs)]:
            write_csv(name, rows)
        make_reports(scores, merges, kappas, loo, pairs)
        after = {name:sha(path) for name,path in FILES.items()}
        if before != after:
            raise RuntimeError('Source changed during W17 analysis')
        manifest = dict(prereg_commit=prereg_commit, computed_at_utc=datetime.now(timezone.utc).isoformat(),
                        inputs={name:dict(path=str(path),sha256=before[name],bytes=path.stat().st_size) for name,path in FILES.items()},
                        inputs_unchanged=True, llm_calls=0, new_episodes=0)
        (OUT/'source_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        (OUT/'output_manifest.json').write_text(json.dumps({p.name:sha(p) for p in sorted(OUT.iterdir())
                                                         if p.is_file() and p.name!='output_manifest.json'},indent=2)+'\n')
        print(json.dumps(dict(output=str(OUT),joined=len(joined),source_integrity=True)))


if __name__ == '__main__':
    main()
