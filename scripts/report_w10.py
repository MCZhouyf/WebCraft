#!/usr/bin/env python3
"""Render the W10 evidence summary and hash indexes, without experiments."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/paperA/w10_release_final'


def read(path):
    with path.open(newline='') as f:return list(csv.DictReader(f))


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def table(rows,fields):
    def val(v):return str(v).replace('|',' / ').replace('\n',' ')
    return ['| '+' | '.join(fields)+' |','|'+'|'.join('---' for _ in fields)+'|',
            *['| '+' | '.join(val(r.get(k,'')) for k in fields)+' |' for r in rows]]


def main():
    impact=json.loads((OUT/'impact/SUMMARY.json').read_text())
    appendix=json.loads((OUT/'impact/APPENDIX_SUMMARY.json').read_text())
    cp=json.loads((OUT/'cp_queries/SUMMARY.json').read_text())
    invariant=json.loads((OUT/'release/semantic_invariants.json').read_text())
    tests=json.loads((OUT/'release/variant_tests.json').read_text())
    scans={k:json.loads((OUT/f'release/{k}_summary.json').read_text())
           for k in ('before','online_after','offline_after')}
    sizes=read(OUT/'release/variant_sizes.csv')
    large=read(OUT/'release/large_files.csv')
    download=json.loads((OUT/'release/offline_download.json').read_text())
    cases=read(OUT/'impact/difference_instances.csv')
    assert len(cases)==9 and impact['reported_baseline_unmapped']==0
    assert appendix['S7_interval_baseline_errors']==0 and invariant['passed']
    assert impact['discrimination_old_summary']==impact['discrimination_counterfactual_summary']
    for rel,digest in impact['source_hashes'].items():assert sha(ROOT/rel)==digest
    # Validate every file hash again after the package tests and anonymous scan.
    for name in ('w10_online','w10_offline'):
        root=ROOT/'release'/name
        for line in (root/'MANIFEST.sha256').read_text().splitlines():
            digest,relative=line.split('  ',1)
            assert sha(root/relative)==digest,relative
    assert sha(ROOT/download['file'])==download['sha256']
    assert all(r['tests_passed']==75 and r['subtests_passed']==119 for r in tests)
    assert cp['episodes']==cp['callbacks_with_frozen_claim_provenance']==308
    with (OUT/'impact/difference_metrics.csv').open('w',newline='') as f:
        fields=['reading_mode','method','condition','world','task_id','metric','frozen','replayed','reason']
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader()
        for r in cases:
            a,b=json.loads(r['frozen_values']),json.loads(r['replay_values'])
            for m in a:w.writerow({**{k:r[k] for k in fields if k in r},'metric':m,'frozen':a[m],'replayed':b[m]})
    changes=[('Reported source-table point cells','impact/changed_reported_cells.csv'),
             ('Registered S7 interval cells','impact/changed_S7_interval_cells.csv'),
             ('G5 discrimination point/interval cells','impact/changed_discrimination_cells.csv'),
             ('Unified A3, effects, confusion, opportunity and full-v3 cells','impact/changed_a3_reported_cells.csv'),
             ('CP versus Naive paired appendix cells','impact/changed_CP_paired_cells.csv')]
    detail=['# Counterfactual reported-number changes','',
            'Replace only the nine W9 metric-different instances; retain every other frozen result. '
            'Rows are source-cell occurrences, not independent findings; duplicates across tables remain visible. '
            'All original reports and manuscript numbers are untouched.','']
    for title,rel in changes:
        rows=read(OUT/rel)
        detail.extend(['## '+title,'',f'{len(rows)} changed source cells.','',
                       *table(rows,list(rows[0])),''])
    (OUT/'ALL_ROUNDED_CHANGES.md').write_text('\n'.join(detail)+'\n')
    ci=impact['discrimination_counterfactual_summary']
    lines=['# W10 pre-release closeout','',
           'Registration commit: `c2f1717`; source W9 result: `78fb419`. '
           'Author date: 2026-10-16; execution host date: 2026-10-17 UTC. '
           'Result commit is the commit containing this report (use `git log -1 --format=%H -- '+
           str((OUT/'REPORT.md').relative_to(ROOT))+'`).','',
           'Zero experimental/model calls and no new experimental episodes. Original frozen files, '
           'source reports, manuscript and W9 package are unchanged. No remote push, GPU work, '
           'non-task process/service operation or new O-gate attempt. CPU nice 19, one BLAS worker; '
           'plan 2 GB plus 8 GB headroom. All changes below are release copies or separate sensitivity tables.','',
           '## 1. Nine differences and reported-number impact','',
           *table(cases,['reading_mode','method','condition','world','task_id','metrics','frozen_values','replay_values','reason']),'',
           'The five pla_010 cases have identical public action methods/parameters/intents, not merely '
           'a different action order. The selected stone-pickaxe operator at semantic step 3 changes '
           'from catalogue `cf_0cf08e1cf6803d3f` to non-catalogue `cf_b55d59dd1aaa0ede`; '
           'stale-map attribution therefore drops one action. This is measurement-attribution sensitivity. '
           'The exact source-level selector cause is not proven. Four other cases change the selected '
           'action/probe path and cost. Do not merge these nine with the 75 metric-equal resource-node cases.','',
           f"Checked {impact['reported_cells_checked']:,} source point cells, with zero frozen-baseline mismatches; "
           f"{impact['reported_rounded_changes']} change at three decimals. "
           f"Recomputed {appendix['S7_interval_cells_checked']} relevant interval cells using the original "
           f"cluster construction, RNG streams and seed: zero baseline mismatches, "
           f"{appendix['S7_interval_rounded_changes']} rounded interval changes. "
           'The full per-cell old/new/unrounded/rounded listing is [ALL_ROUNDED_CHANGES.md](ALL_ROUNDED_CHANGES.md).','',
           '- Common 110: Naive/Majority/TruthFinder/Verify-All discriminative contact '
           '**2/110 = .018 -> 1/110 = .009** (-0.9 pp); the same-shell ablation also changes .018 -> .009. '
           'ARBITER remains .018, Oracle .000, Parametric variants .027, Probe-then-RAG .209.',
           '- Corresponding four pipeline rows in the existing A3 common-110 table: v3 upper '
           '**.009 -> .000**, lower remains .000. External-minus-Naive upper/difference intervals change; '
           'ratios with a now-zero reference are undefined. External events themselves are unchanged.',
           '- Table-2 real cost, all_public: ARBITER mean actions **1.746 -> 1.749**, semantic cost '
           '**1.810 -> 1.813**, probe count/cost **1.075 -> 1.080**; Probe-then-RAG mean actions '
           '**5.088 -> 5.094**, semantic cost **5.262 -> 5.267**. Search calls unchanged.',
           '- S7 secondary action totals (two identical seeds): ARBITER **1306 -> 1308**, '
           'Probe-then-RAG **3806 -> 3810**. Efficiency-type ARBITER cost **1.468 -> 1.481**; '
           'probe count/cost **.987 -> 1.013**. ARBITER old2new probes **1.912 -> 1.971**, '
           'one-sided probes **1.033 -> 1.041**.',
           '- ARBITER all-subset probe count/cost CI lower **.866 -> .874**; old2new CI upper '
           '**2.288 -> 2.390**. G5 has '+str(impact['discrimination_changed_rounded_cells'])+
           ' changed point/interval cells, but significance/separation verdicts do not change.',
           '- A3 confusion counts, permissive precision/recall/coverage, full-v3 and opportunity-density '
           'changes are listed individually ('+str(impact['a3_changed_reported_cells'])+' rounded cells across those source tables/effects). '
           'CP-minus-Naive main all_public discriminative-action difference **-.003 -> .000**, '
           'CI lower **-.008 -> .000**; CI upper remains .000.',
           '- **Unchanged:** all SRs, terminal distributions, feasible-task SR, false abstention, execution failure, '
           'formal plan-source HTR_commit, HTR_all, feasible reports/F1, IC, IAA query answers, OPR/OVR reported ratios, '
           'and current English RQ1--RQ5 displayed numerical claims. Three optional OVR candidate denominators '
           'change but the reported OVR remains unavailable; see the explicit optional-metric check. '
           'Verify-All negative-event signatures are unchanged; its 69 unique negative events/0 propagation '
           'reference is unaffected.','',
           f"Discrimination v2 remains {ci['sr_indistinguishable_v2_pair_mode_count']} SR-indistinguishable rows; "
           f"{ci['sr_indistinguishable_secondary_separated_v2_pair_mode_count']} significantly separated rows / "
           f"{ci['sr_indistinguishable_secondary_separated_v2_unique_method_pair_count']} unique pairs. "
           'The old nonzero-gap count remains 53/23; mechanism-cluster sensitivity remains 53/24.','',
           '**Scope limitation:** only `paper/PAPER_A_DRAFT.md` is present; requested Chinese Table 2/3/4 '
           'numbering differs from the available English draft. Table-2 real costs and v13 primary Table-4 '
           'sources are cross-checked by source name, and every available draft numeric location is inventoried '
           'in `impact/draft_numeric_inventory.csv`. No unseen Chinese/Word Table 3 is claimed audited. '
           'Its author-facing numbering/location crosswalk remains to be checked by the author.','',
           '## 2. Anonymization','',
           *table([{'stage':k,**v} for k,v in scans.items()],['stage','hit_rows','occurrences','project_identity_rows','by_origin']),'',
           'Before: 2,239 broad scanner rows (2,600 occurrences), including 1,224 project and 1,015 '
           'third-party rows. W10 neutralizes/excludes 1,204 project path/archive rows; the 20 remaining '
           'project hits are relative `corpus/private/...` suffixes, not absolute local paths. '
           'Both forms have **zero project-author/name/email/hostname matches, zero project absolute paths, '
           'zero .git directories, and zero bytecode/cache files**. Initial project-author identity hits were '
           'already zero; do not describe path cleanup as removal of author names.','',
           'Offline retains 1,014 third-party dependency rows (license/author emails, build provenance and '
           'test archive metadata), explicitly classified rather than silently erased. One unnecessary '
           'NumPy template bytecode file was removed and wheel RECORD rebuilt; source, binaries and notices '
           'remain intact. Online has no wheelhouse. These are **not zero raw scanner matches**. '
           'Detailed individual hit/disposition and source-to-release transform records are in '
           '`release/*_hits.csv`, `release/transforms.csv`, `release/vendor_bytecode_cleanup.csv`; '
           'excluded local plaintext/caches are separately inventoried in `before_local_excluded.csv`.','',
           'Nested encrypted assets were decrypted only for audit, metadata neutralized and re-encrypted '
           'using the existing external password file. Tar owner/time and gzip original-filename/time '
           'fields are neutralized, without changing research revision timestamps or claim text. '
           'JSON metadata keys and values were sanitized. Public results/claims retain semantic payloads; '
           'source-specific third-party attribution remains. The reviewer password is never printed or committed.','',
           f"All {invariant['checks']} semantic checks passed: actual truth recipe/drop/item objects, frozen tasks, "
           'policies, prior, evidence, claims and numeric CSV cells match; all 11,220 frozen main reference '
           'and 1,442 CP reference actions/metrics remain semantically identical. Whole truth-file bytes '
           'can differ because local JAR/provenance paths were anonymized. Distribution hashes are new '
           'and distinct from source research hashes. No matrix rerun was performed in W10.','',
           '## 3. Delivery forms, sizes and tests','',
           *table(sizes,['variant','files','bytes','wheelhouse','root']),'',
           f"Complete offline download: **{download['bytes']:,} bytes** "
           f"({download['bytes']/1e6:.2f} MB), SHA-256 `{download['sha256']}`. "
           'No individual file in either directory exceeds 50 MB or 100 MiB. The only >100 MB / >100 MiB '
           'file is the separate offline download below; it is ignored by Git, never staged/uploaded.','',
           *table(large,['file','bytes','over_50_MB','over_100_MB','over_GitHub_100_MiB']),'',
           *table(tests,['variant','tests_passed','subtests_passed','dependency_install','integrity_restricted_and_imports','elapsed_s']),'',
           'Each form was tested in a fresh Python 3.10.8 environment at a path containing a space, '
           'PYTHONHASHSEED=0, no GPU, one solver thread. Online used public PyPI and exact pins (no '
           'local-wheel fallback); offline used bundled wheels with --no-index. Integrity/decryption '
           'and isolated imports passed. These are package tests, not 11,220 new episode runs. '
           'The complete offline tar is a metadata-neutral archive of the tested offline directory; '
           'it is not a third independently tested environment.','',
           'Use a new anonymous repository containing only `release/w10_online/`, not a mirror of this '
           'work repository or its Git history. The old `release/package/` is the unchanged W9 forensic '
           'source, not an anonymous W10 distribution. Both forms have LICENSE-CODE (author decision '
           'pending) and LICENSE-DATA (supplied CC BY-NC-SA terms, attribution file and original-page '
           'exclusion); no code license is chosen or granted. Final license approval remains an author task.','',
           '## 4. CP-RAG queries','',
           f"{cp['episodes']} triggering episodes / {cp['callback_occurrences']} callbacks, all linked to frozen "
           'original claim ID, payload, span and source provenance. Query strings are reconstructed '
           'from the exact callback expression `f"{subject} {goal}"`, **not verbatim query telemetry**. '
           f"{cp['inferred_executed_searches']} callbacks had a public index; {cp['no_public_index']} returned "
           'before search (intended query only: 100 CDS, 5 oracle-reading). Resource-failure callbacks: 0. '
           'Raw retrieved-hit counts are unavailable; no new search was run. '
           'See `cp_queries/queries_and_denied_claims.csv`, `trigger_episodes.csv` and README.','',
           '## 5. README First Screen (Verbatim)','',
           (OUT/'release/README_FIRST_SCREEN.md').read_text().rstrip(),'','## 6. Reanalysis Reproduction','',
           'Run in the working repository with its existing read-only evidence (not a new agent run):','',
           '```bash',
           'PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONHASHSEED=0 nice -n 19 python3 scripts/w10_impact.py',
           'PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONHASHSEED=0 nice -n 19 python3 scripts/w10_appendix_impact.py',
           'PYTHONDONTWRITEBYTECODE=1 nice -n 19 python3 scripts/w10_cp_queries.py',
           'python3 scripts/report_w10.py','```','',
           'Packaging scripts: `w10_release.py` (scan/build/refresh/rescan), '
           '`w10_test_variants.py`, `w10_semantic_invariants.py`. '
           'Inputs retain their original hashes; output inventory is `OUTPUTS.sha256`. '
           'Only W10-owned files are staged. No publication is performed.']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    # Do not hash this output index into itself.
    paths=sorted(p for p in OUT.rglob('*') if p.is_file() and p.name!='OUTPUTS.sha256')
    (OUT/'OUTPUTS.sha256').write_text(''.join(f'{sha(p)}  {p.relative_to(OUT)}\n' for p in paths))
    linked=[ROOT/'release/README.md',ROOT/'release/.gitignore',
            ROOT/'release/w10_online/MANIFEST.sha256',ROOT/'release/w10_offline/MANIFEST.sha256',
            ROOT/download['file']]
    (ROOT/'release/W10_MANIFEST.sha256').write_text(''.join(f'{sha(p)}  {p.relative_to(ROOT/"release")}\n' for p in linked))
    print(json.dumps({'report':str((OUT/'REPORT.md').relative_to(ROOT)),
                      'output_files':len(paths),'all_validation_assertions_passed':True}))


if __name__=='__main__':main()
