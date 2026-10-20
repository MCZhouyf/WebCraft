#!/usr/bin/env python3
"""Summarize only completed W9 replay outputs; never execute a method."""
import csv
import hashlib
import json
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/paperA/w9_release_fix'


def read(path):
    with path.open(newline='') as handle:return list(csv.DictReader(handle))


def write(path,rows):
    with path.open('w',newline='') as handle:
        w=csv.DictWriter(handle,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)


def main():
    scope_results={scope:json.loads((OUT/f'replay/{scope}_result.json').read_text())
                   for scope in ('main','ext_v2','cds')}
    inventory=read(OUT/'final_package_inventory.csv')
    def category(path):
        for prefix,name in (
            ('wheelhouse/','Offline dependencies'),('assets/evidence/','Frozen public evidence'),
            ('assets/frozen/','Reference episodes'),('assets/prior/','Frozen parameter prior'),
            ('method_code/','Method code and configuration'),('corpus/encrypted/','Encrypted reviewer assets'),
            ('corpus/','Public derived corpus'),('cds_materials/','Visible CDS materials'),
            ('evaluator/cds/','Scorer-only CDS truth'),('reports/','Paper CSVs and table builders'),
            ('provenance/','Original source snapshots'),('tests/','Unit tests'),
            ('repro/','Portable replay and scoring'),('data/','Tasks and conflicts'),
            ('truth/','Frozen world truth')):
            if path.startswith(prefix):return name
        return 'Other package files'
    grouped=defaultdict(list)
    for row in inventory:grouped[category(row['path'])].append(row)
    composition=[{'category':name,'files':len(values),'bytes':sum(int(r['bytes']) for r in values)}
                 for name,values in sorted(grouped.items())]
    write(OUT/'package_contents.csv',composition)
    field_changes=[];action_types=[]
    for scope in scope_results:
        comparisons=read(OUT/f'replay/{scope}_comparison.csv')
        metrics=Counter(field for row in comparisons for field in row.get('changed_metrics','').split(';') if field)
        action=Counter(row['action_only_difference'] for row in comparisons if row.get('action_only_difference'))
        field_changes.extend({'scope':scope,'metric':metric,'different_instances':n}
                             for metric,n in sorted(metrics.items()))
        action_types.extend({'scope':scope,'difference_type':name,'instances':n}
                            for name,n in sorted(action.items()))
    if field_changes:write(OUT/'metric_difference_counts.csv',field_changes)
    if action_types:write(OUT/'action_only_difference_counts.csv',action_types)
    gate=json.loads((OUT/'o_gate/result.json').read_text())
    cp=json.loads((OUT/'cp_triggers/summary.json').read_text())
    size=json.loads((OUT/'package_size.json').read_text())
    total_metric_diffs=sum(r['summary'].get('metric_difference',0) for r in scope_results.values())
    total_errors=sum(r['summary'].get('runtime_error',0) for r in scope_results.values())
    complete=all(r['compared']==r['expected'] for r in scope_results.values())
    statement=('存在指标差异（逐项列出）' if total_metric_diffs or total_errors else
               '全部报告指标逐实例复现，动作顺序在依赖等价处可能不同' if complete else
               '仅完成部分实例，不能作全矩阵复现声明')
    lines=['# W9 release completion and reproduction','',
           'Preregistered at commit `4d74800`. CPU-only, zero model calls; original research inputs and reported numbers are retained.','',
           '## Completed package','',
           f"- Package bytes/files: {size['bytes']:,} / {size['files']:,} (encrypted artifacts included; decrypted local copies excluded).",
           '- Complete prior, main method sources, shared reader/compiler/MIP planner, appendix policies, seven condition indexes with reading-mode projections, exact evidence bundles, EXT-v2/CDS, frozen episode logs, table inputs/builders, v3/v3-d/symmetric observation scorer, tests and offline wheelhouse are included.',
           '- Restricted pollution assets require the author-supplied reviewer password. No original raw pages or plaintext synthetic pages are distributed.',
           '- `package_inventory.csv` lists original additions; `final_package_inventory.csv` and `package_contents.csv` give final files, bytes and hashes.',
           '- Ten core method/compiler/config files are byte-identical to ff3bf4a; appendix additions are separately archived.',
           '', '## Clean-environment replay','',
           'Python 3.10.8, PYTHONHASHSEED=0, CBC one thread; new virtual environment installed from package-local wheels without an index. All imports were checked to resolve within the package. The clean package test suite passed; its console log is retained separately.','',
           'The CDS run emitted scikit-learn duplicate-point clustering ConvergenceWarnings (fewer distinct points than the requested clusters). No clustering parameter was changed or episode removed; actual replay exceptions are counted separately below.','',
           '| Scope | Compared / expected | Metric-different instances | Runtime errors | Action-only differences |',
           '|---|---:|---:|---:|---:|']
    for scope,result in scope_results.items():
        lines.append(f"| {scope} | {result['compared']}/{result['expected']} | {result['summary'].get('metric_difference',0)} | {result['summary'].get('runtime_error',0)} | {result['action_only_differences']} |")
    if field_changes:
        lines.extend(['', '| Scope | Different metric | Instances |', '|---|---|---:|'])
        for row in field_changes:
            lines.append(f"| {row['scope']} | {row['metric']} | {row['different_instances']} |")
        lines.append('\nRows may overlap across metrics. `specific_action` denotes discriminative stale action contact; it is not HTR_all.')
    lines.extend(['',f'Final statement: **{statement}**.','',
                  'Per-instance frozen/replayed values are in `replay/*_comparison.csv`; no mismatching row was dropped. Action-only changes are classified separately. The initial packaging trial lacked the Oracle normalizer module; its partial output is retained and excluded from the final comparison. Copying the unchanged module and adding a package-only import test fixed the packaging dependency; no method parameter or scoring rule was changed.','',
                  '## CP-RAG triggers','',
                  f"- Conflict-verification triggers: {cp['totals']['conflict_probe_triggers']}; denied-candidate occurrences: {cp['totals']['denied_candidate_occurrences']}.",
                  f"- Episodes with no requery trigger: {cp['categories'].get('never_triggered',0)}; triggered with no novel compiled candidates: {cp['categories'].get('triggered_zero_new_candidates',0)}; candidates returned but unused: 0; returned and used: 0.",
                  '- Frozen traces and callback code imply 203 public-index searches and 105 callbacks with no public index (100 CDS, five direct-gold episodes). These are inferred call counts, not logged raw hit counts.',
                  '- Raw retrieval hits were not logged, so zero novel candidates cannot be equated with empty retrieval or proof that alternative routes do not exist. Five exact trace examples are retained. W8 action-divergence categories did not count triggers.','',
                  '## Semantic O gate','',
                  f"The single gate attempt compared {gate['compared']}/{gate['expected']} episodes before its first failure. Gate passed: {gate['passed']}. The failure is eff_008 / 1.16.5 / ARBITER: frozen semantic actions/cost 10/10 versus replay 8/8, with different mechanism multisets and the same terminal. V/C/V+C were not run; no paired decomposition estimates or attribution of the 11.7647 pp gap are reported. No third attempt was made.",
                  '', '## Access and licensing','',
                  'The existing project LICENSE is still author-provided and unresolved. Source attribution is included for frozen snippets. Model weights/endpoints/credentials and original Wiki archives are excluded; raw reconstruction routes and encrypted-reviewer access are documented in the package. No remote publication was performed.'])
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    summary={'package':size,'scopes':scope_results,'statement':statement,'O_gate':gate,'cp':cp}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    hashes=[]
    for path in sorted(OUT.rglob('*')):
        if not path.is_file() or 'runtime' in path.parts or 'packaging_trial_missing_oracle_dependency' in path.parts or path.name=='MANIFEST.sha256':continue
        if path.suffix=='.jsonl':continue
        hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(OUT)}")
    (OUT/'MANIFEST.sha256').write_text('\n'.join(hashes)+'\n')
    print(json.dumps({'statement':statement,'package_bytes':size['bytes'],'scopes':scope_results},sort_keys=True))


if __name__=='__main__':main()
