# W17: second-annotator comparison (post-review VI)

Registered on 2026-10-19 America/Tijuana before W17 calculations. Schema inspection
and inspection of the existing W15 implementation have occurred; this is not a
blind registration. User-supplied expected counts/kappas are checks, never fitting
targets. Report all outcomes. No LLM/network calls, new episodes, remote push, or
changes to frozen files. WebCraft is read only. Use a shared read lock on the
existing server.lock, consistent with concurrent deterministic analyses.

## Inputs, identity, and two checks

Second annotations are the uploaded `blind_sheet2_heldout80_annotated.csv` and
`blind_sheet2_cds30_annotated.csv` in autodl-tmp. First annotations and frozen
episode scorer labels are A19 `episode_alignment_80.csv` and A20
`cds_episode_alignment.csv`. Join only by audit_id, requiring unique IDs, exact
cohort sets, and matching method/condition/world/task_id. Do not infer labels from
notes. Retain a row-level joined export and input SHA-256 manifest. Check hashes
again on completion. Schema/identity errors fail the affected calculation; they
do not authorize fallback joins or label imputation.

First check the episode unit, label vocabulary, and sample identity. Second check
numerators, denominators, unknown exclusions, and weighting independently. These
are audit-cohort descriptive estimates, not population prevalence estimates.

## 1. Scorer versus annotator 2

Contact is binary positive for 查询 or 执行, negative for 无; 无法确定 is missing
for binary accuracy. Strip surrounding whitespace only. On rows where annotator 2
and the scorer are determinate, compute TP, FP, FN, TN; precision TP/(TP+FP), recall
TP/(TP+FN). Give numerator, denominator and two-sided Wilson 95% intervals with
z=1.959963984540054. Zero denominator is undefined, not zero. Report each cohort
separately, all exclusions, total scorer positives, total annotator-2 positives,
and their intersection with audit IDs. Check 35 versus 32 in held-out without
forcing these totals. Also reproduce annotator-1/scorer counts as provenance for
the original ten held-out false positives.

## 2. Union and intersection reference labels

Primary merged comparisons use the same mutually contact-determinate human rows
for both definitions, and require a determinate scorer. Union is positive if
either human is positive; intersection only if both are positive. Report Wilson
precision/recall and exclusions with the same definitions as above.

Supplementary partial-information comparisons retain logically determined labels:
union is positive if either label is positive, negative only if both are negative,
otherwise unknown; intersection is negative if either is negative, positive only
if both are positive, otherwise unknown. This is explicitly a different denominator
and never substitutes for the primary comparison. Unknown is never coerced to no.

Define original FPs from frozen scorer-positive/annotator-1-negative rows. For each
merge report counts and IDs becoming TP, remaining FP, or excluded/unknown. Do not
conflate this with new positives outside the scorer-positive set.

## 3. Inter-annotator agreement

On mutually determinate contact rows calculate unweighted Cohen kappa for
{无,查询,执行} and binary {无,有}. Report n, observed agreement, expected chance
agreement, marginals and confusion matrices. Kappa=(po-pe)/(1-pe); pe=1 yields
undefined. Compare rounded-to-three-decimal values to held-out .854/.831 and CDS
.944/1.000. Report deviations without changing inclusion or definitions.

Propagation agreement uses mutually determinate literal 是/否 judgments, separately
from contact agreement. Blank propagation is inferred 否 only for an explicit 无
contact; otherwise it is unknown. Explicit 无法确定 stays unknown even on a no-contact
row. Report all determinate propagation agreements/n and additionally agreements/n
on the human-union-contact-positive subset and intersection-positive subset. This
distinguishes whole-cohort agreement from positive-contact-only 20/20 or 15/15
claims without choosing a denominator to match them.

## 4. W15 LOO audit

Use frozen A8 `cds_agents_20261017/episode_metrics.csv`, method react, conditions
empty and K, paired on instance_id. Require equal sets, binary success, and
matching family_id across conditions. The Table-4-compatible full estimate is
sum(SR_empty - SR_K)/52 if 52 valid pairs exist; do not silently substitute another
population. For each actual family, drop that family and report retained pair count,
each side's successes, and paired episode-weighted difference. Also reconstruct
the W15 implementation's equal-family-mean difference on identical input, comparing
every excluded-family estimate to `webcraft/reports/paperA/w15/loo_results.csv`.
Inspect its source lines and preregistered weighting to explain any difference.

For corrected LOO intervals sample retained families with replacement, retaining
all paired rows and pooling numerator/denominator, 10,000 draws, NumPy PCG64 seed
20261017 (W15 seed). Report full and LOO ranges and zero crossings. A range not
containing the full point is a diagnostic, not by itself proof of error; identify
the concrete estimand or population difference. Preserve W15 files unchanged.

## Deliverables

New script/tests plus `reports/main/post_review/w17_20261019/`: scorer_vs_ann2.csv,
merged_precision.csv, kappa_check.csv, loo_fix.md, joined_labels.csv, original_fp.csv,
confusion.csv, loo_corrected.csv, REPORT.md and source/output hash manifests.
Only W17 paths are staged. No result-dependent redefinition or threshold tuning.
