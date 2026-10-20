"""W17b: pair and pool retained CDS episodes; preserve all frozen exports."""
from __future__ import annotations

import csv
import fcntl
import json
import subprocess
from pathlib import Path

from w17_analysis import bootstrap, read_csv, sha, unique

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/main/post_review/w17b'
REG = ROOT / 'prereg/w17b_cds_loo_addendum.md'
SOURCES = {
    'GLM-5.2': ROOT / 'reports/main/post_review/cds_agents_20261017/episode_metrics.csv',
    'Qwen': ROOT / 'reports/main/post_review/a18_qwen_cds_20261017/remote_service/episode_metrics.csv',
    'W17': ROOT / 'reports/main/post_review/w17_20261019/loo_corrected.csv',
    'W15': ROOT.parent / 'webcraft/reports/paperA/w15/loo_results.csv',
    'bootstrap_code': ROOT / 'scripts/w17_analysis.py',
    'script': Path(__file__).resolve(),
    'registration': REG,
    'server_lock': ROOT.parent / 'server.lock',
}
SPECS = [
    ('GLM-5.2', 'react', 'K+S', 'K+N', 'action_contact', 'new_correction'),
    ('GLM-5.2', 'direct_llm', 'empty', 'K', 'success', 'new_correction'),
    ('Qwen', 'react', 'empty', 'K', 'success', 'new_correction'),
    ('Qwen', 'react', 'K+S', 'K+N', 'action_contact', 'new_correction'),
    ('GLM-5.2', 'react', 'empty', 'K', 'success', 'W17_consistency_check'),
]


def write_csv(name, rows):
    with (OUT / name).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def pair_rows(rows, method, left, right, field):
    maps = {c: unique([r for r in rows if r['method']==method and r['condition']==c], 'instance_id')
            for c in (left, right)}
    if set(maps[left]) != set(maps[right]) or len(maps[left]) != 52:
        raise ValueError(f'Expected equal 52-key sets: {method} {left} {right}')
    pairs = []
    for key in sorted(maps[left]):
        a, b = maps[left][key], maps[right][key]
        if any(a[k] != b[k] for k in ('family_id', 'task_id', 'target_world')):
            raise ValueError(f'Pair identity mismatch: {key}')
        if a[field] not in ('0','1') or b[field] not in ('0','1'):
            raise ValueError(f'Nonbinary metric: {key} {field}')
        pairs.append(dict(instance_id=key, family_id=a['family_id'], left_value=int(a[field]),
                          right_value=int(b[field]), delta=int(a[field])-int(b[field])))
    return pairs


def analyse_pairs(pairs):
    details = []
    for excluded in ['NONE'] + sorted({p['family_id'] for p in pairs}):
        kept = [p for p in pairs if p['family_id'] != excluded]
        numerator = sum(p['delta'] for p in kept)
        low, high = bootstrap(kept)
        details.append(dict(excluded_family=excluded, n_pairs=len(kept),
                            n_families=len({p['family_id'] for p in kept}),
                            left_events=sum(p['left_value'] for p in kept),
                            right_events=sum(p['right_value'] for p in kept),
                            delta_numerator=numerator, delta=numerator/len(kept),
                            ci95_low=low, ci95_high=high, crosses_zero=low<=0<=high))
    return details


def worst_deletion(details):
    return min((r for r in details if r['excluded_family']!='NONE'),
               key=lambda r:(abs(r['delta']),r['excluded_family']))


def main():
    with SOURCES['server_lock'].open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = {k:sha(p) for k,p in SOURCES.items()}
        commit = subprocess.check_output(['git','log','-1','--format=%H','--',str(REG)],cwd=ROOT,text=True).strip()
        if not commit:
            raise ValueError('Preregistration not committed')
        registered = subprocess.check_output(['git','show',f'{commit}:{REG.relative_to(ROOT)}'],cwd=ROOT)
        if registered != REG.read_bytes():
            raise ValueError('Uncommitted registration changes')
        inputs = {m:read_csv(SOURCES[m]) for m in ('GLM-5.2','Qwen')}
        prior = unique(read_csv(SOURCES['W17']), 'excluded_family')
        summary, detail_rows, pair_exports = [], [], []
        for model, method, left, right, field, status in SPECS:
            identity = dict(suite='CDS',model=model,method=method,contrast=f'{left}_minus_{right}',metric=field)
            pairs = pair_rows(inputs[model],method,left,right,field)
            details = analyse_pairs(pairs)
            full, dropped = details[0], details[1:]
            worst = worst_deletion(details)
            if status=='W17_consistency_check':
                if set(prior) != {r['excluded_family'] for r in details}:
                    raise ValueError('W17 reference family mismatch')
                for r in details:
                    old = prior[r['excluded_family']]
                    if int(old['n_pairs'])!=r['n_pairs']:
                        raise ValueError('W17 denominator mismatch')
                    for oldkey,newkey in [('corrected_episode_weighted_delta','delta'),('ci95_low','ci95_low'),('ci95_high','ci95_high')]:
                        if float(old[oldkey]) != r[newkey]:
                            raise ValueError(f'W17 exact consistency check failed: {oldkey}')
            summary.append(dict(**identity, status=status, estimand='paired_episode_mean',
                                n_full_pairs=full['n_pairs'], n_full_families=full['n_families'],
                                full_left_events=full['left_events'],full_right_events=full['right_events'],
                                full_delta_pp=100*full['delta'],
                                loo_min_pp=100*min(r['delta'] for r in dropped),
                                loo_max_pp=100*max(r['delta'] for r in dropped),
                                worst_excluded_family=worst['excluded_family'],
                                worst_n_pairs=worst['n_pairs'],worst_delta_pp=100*worst['delta'],
                                worst_ci95_low_pp=100*worst['ci95_low'],worst_ci95_high_pp=100*worst['ci95_high'],
                                worst_crosses_zero=worst['crosses_zero'],
                                any_deletion_crosses_zero=any(r['crosses_zero'] for r in dropped),
                                zero_crossing_families=';'.join(r['excluded_family'] for r in dropped if r['crosses_zero']),
                                retained_pair_counts=';'.join(map(str,sorted({r['n_pairs'] for r in dropped}))),
                                bootstrap='whole_family_resampling_pooled_paired_episodes',draws=10000,seed=20261017,
                                ext_v2_estimand_unchanged='equal_family_mean_of_within_family_contrasts'))
            detail_rows.extend(dict(**identity,**r) for r in details)
            pair_exports.extend(dict(**identity,**p) for p in pairs)
        after = {k:sha(p) for k,p in SOURCES.items()}
        if before!=after:
            raise RuntimeError('Input changed during W17b')
        OUT.mkdir(parents=True,exist_ok=True)
        write_csv('loo_corrected_cds_v2.csv',summary)
        write_csv('loo_deletions.csv',detail_rows)
        write_csv('paired_inputs.csv',pair_exports)
        lines = ['# W17b: CDS episode-weighted LOO corrections','',
                 'CDS targets the Table-4 paired episode mean: sum of retained paired differences / retained pairs. '
                 'EXT-v2 retains the author-specified equal-family mean of within-family mismatch-minus-match contrasts; '
                 'no EXT-v2 values or frozen files are changed. W17b registration records both estimands explicitly.','',
                 'Least favourable means the deletion point closest to zero (family_id lexical tie-break). '
                 'Zero crossing includes either endpoint. All changes and intervals are in percentage points. '
                 'Action contact uses the frozen action_contact column, not query-plus-action contact.','',
                 '| Model / method / contrast / metric | Full | LOO range | Least-favourable family | Its 95% CI | Its CI includes zero | Any deletion CI includes zero |',
                 '|---|---:|---|---|---|---|---|']
        for r in summary:
            lines.append(f"| {r['model']} / {r['method']} / {r['contrast']} / {r['metric']} | {r['full_delta_pp']:.2f} | "
                         f"[{r['loo_min_pp']:.2f}, {r['loo_max_pp']:.2f}] | {r['worst_excluded_family']} | "
                         f"[{r['worst_ci95_low_pp']:.2f}, {r['worst_ci95_high_pp']:.2f}] | {r['worst_crosses_zero']} | {r['any_deletion_crosses_zero']} |")
        lines += ['', 'All comparisons have 52 matched pairs across 12 families; deletions retain 44 or 48 pairs. '
                  'The already-corrected GLM ReAct empty-minus-K row, including every CI, exactly reproduces W17.',
                  'Confidence intervals use the unchanged W17 bootstrap implementation, 10,000 family resamples, '
                  'PCG64 seed 20261017. Original W15/W17 exports and EXT-v2 rows remain unchanged.', '',
                  f'Preregistration commit: {commit}. Zero LLM calls; zero new episodes.', '']
        (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
        (OUT/'source_manifest.json').write_text(json.dumps(dict(preregistration_commit=commit,
             inputs={k:dict(path=str(p),sha256=before[k]) for k,p in SOURCES.items()},
             inputs_unchanged=True, w17_exact_reproduction=True, llm_calls=0,new_episodes=0),indent=2)+'\n')
        (OUT/'output_manifest.json').write_text(json.dumps({p.name:sha(p) for p in sorted(OUT.iterdir())
                                                          if p.is_file() and p.name!='output_manifest.json'},indent=2)+'\n')
        print('\n'.join(lines))


if __name__=='__main__':
    main()
