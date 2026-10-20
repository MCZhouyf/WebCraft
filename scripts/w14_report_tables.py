#!/usr/bin/env python3
"""Small presentation-only tables from the completed W14 CSV exports."""
import csv
from collections import Counter
import w14_audit as a


def read(name):
    with (a.OUT/name).open(newline='') as f:return list(csv.DictReader(f))


def main():
    effects=read('paired_effects.csv');rates=read('results_by_condition.csv')
    cal=read('dev/calibration.csv');refs=read('version_informed_reference_summary.csv')
    lines=['# W14 numeric overview','',
        'All entries below are generated from CSV; rates are not pooled across reading modes.',
        'Full provenance and step-zero corrections are in REPORT.md and STAGE0_REPORT.md.','',
        '## Calibration did not meet its target','',
        '|Posterior threshold|Odds threshold|Eligible dev episodes|Ever formed|Rate|',
        '|---:|---:|---:|---:|---:|']
    for r in cal:
        lines.append(f"|{r['threshold']}|{r['odds']}|{r['eligible']}|{r['formed']}|{100*float(r['formation_rate']):.1f}%|")
    lines+=['','The 50% requirement was NOT achieved. The preregistered fallback was frozen before held-out execution.',
        'Do not describe VB-RAG-cal as a successfully calibrated method. It is the registered threshold-only diagnostic.','',
        'Minimum-threshold eligible-dev diagnostic: '+str(dict(Counter(r['barrier'] for r in
          read('dev/calibration_barriers.csv') if r['candidate']=='calibration_4' and r['eligible']=='1')))+'.',
        'No update/probe change was allowed to repair these barriers.','',
        '## Overall SR changes versus frozen Naive','',
        '|Scope|Mode|Method|Paired n|Difference (pp)|95% CI (pp)|','|---|---|---|---:|---:|---|']
    for r in effects:
        if r['condition']=='all' and r['metric']=='SR' and r['peer']=='naive_rag':
            lines.append(f"|{r['scope']}|{r['reading_mode']}|{r['method']}|{r['paired_episodes']}|{100*float(r['delta']):.3f}|[{100*float(r['ci95_low']):.3f}, {100*float(r['ci95_high']):.3f}]|")
    lines+=['','No recall-upper SR gain was observed. VB-cal also did not improve the primary mixed SR;',
        'its nonprimary overall point estimates include declines, as shown above.','',
        '## Primary mixed side-by-side references','',
        'The last two rows per mode are historical version-informed references, not W14 reruns.',
        '|Mode|Method|n|SR|Declared plan acceptance|','|---|---|---:|---:|---:|']
    for mode in ('all_public','llm_only','oracle_reading'):
        for r in rates:
            if r['scope']=='main' and r['reading_mode']==mode and r['grouping']=='condition_group' and r['condition']=='mixed':
                lines.append(f"|{mode}|{r['method']}|{r['n']}|{float(r['SR']):.3f}|{float(r['plan_acceptance']):.3f}|")
        for r in refs:
            if r['reading_mode']==mode and r['condition_group']=='mixed':
                lines.append(f"|{mode}|{r['method']} (reference)|{r['n']}|{float(r['SR']):.3f}|{float(r['plan_acceptance']):.3f}|")
    lines+=['','## Recall opportunities','',
        'W13_118_overlap.csv uses ONLY exists_bm25_missed with compiler_after_full_recall=true.',
        'Three already-retrieved compilable cases are not members of that 118-case set.',
        'Novel compiled candidates are not necessarily valid, goal-relevant, or previously absent correct focal operators.',
        'An opportunity with a confirmed compilable valid fact need not add a NEW operator ID.','',
        '## Limits','',
        '- CDS conditions change polarity but not positive operator admissibility; see the separate audit.',
        '- Original VB has no polluted rows; never compare its 272-key overall rate directly to a 374-key overall rate.',
        '- Specific contact/v3-d retain catalogue coverage; equal public trajectories can have attribution differences.',
        '- Year accuracy is literal release-calendar-year agreement, not version identification. Revision year 2026 is not relabelled 2025.',
        '- Grounded smelt validation compares per-unit recipe and operator counts; logged batch n affects costs but not that unit predicate.']
    (a.OUT/'RESULTS_OVERVIEW.md').write_text('\n'.join(lines)+'\n')
    a.write('PRESENTATION_INPUT_HASHES.csv',[dict(path=name,sha256=a.sha(a.OUT/name)) for name in
        ('paired_effects.csv','results_by_condition.csv','dev/calibration.csv','version_informed_reference_summary.csv')])


if __name__=='__main__':main()
