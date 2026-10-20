# A20: human-audit completion and CDS contact-scoring checks

Rules registered on 2026-10-17 before A20 computation and before opening the
CDS scorer key. Commit this document and its input-hash manifest first.
Hard cutoff: 2026-10-18 23:59 America/Tijuana. Zero model calls, zero episodes,
no service/GPU operations, no remote push. WebCraft, original keys, frozen
methods, prompts, and A8/A19 results are read-only. New outputs only under
`reports/main/post_review/a20_audit_cds_scoring_20261017/`.

On data/specification mismatch, log it and continue unaffected analyses. Never
invent a label or repair a source file. An unidentifiable result is NA or a
conservative lower/upper bound, not zero. Record CDS key-open time, source
hash, and this registration commit. The task uses no game server and preserves
the existing server.lock without changing it or touching other processes.

## 1. Human comparison: CDS 30, main 80, pooled reference

Validate the uploaded `/root/autodl-tmp/audit_sheet_cds_v3.csv` with the existing
v3b validator. Match neutral AC IDs through the original CDS ID map to the
saved CDS key. Use A19's exact event-to-episode precedence: execution > query >
no contact; preserve unable-to-determine separately when no positive event is
established. Propagation is yes if any event is yes, otherwise unresolved if
any is unresolved, otherwise no. Human no-contact/blank entails no propagation;
human unresolved contact/blank remains unresolved. No new human adjudication.

Use the same A19 determinate-pair denominators for binary contact precision/
recall and propagation precision/recall. Human and scorer unresolved judgments
are excluded per metric and tabulated, never negatives. Export full contact
4x4 and propagation 3x3 matrices including zero/unresolved cells. Report human,
scorer, and either-side unresolved rates, plus either-human-metric unresolved.

Use two-sided Wilson score intervals at 95%, z=1.959963984540054, for each
binomial numerator/denominator (precision, recall, unresolved rates). A zero
denominator is NA. Wilson intervals are descriptive conditional-binomial
intervals, not adjustments for stratified sampling or repeated mechanisms.
Keep CDS and main separate as primary displays; pooled 110 is explicitly
reference-only, not a new representative population estimate. If CDS identity
checks find repeated original episodes, aggregate their events, mark conflicting
human labels unresolved, and report the actual unique n rather than force 30.
Do not reopen the main key: reuse A19's archived 80-row alignment and lineage.

## 2. Main A19 error decomposition

Take the frozen A19 contact FP=10, propagation FP=5 and propagation FN=1 lists
without relabeling. Preserve their metric-specific counts even if the same
episode appears in two metrics. Give original-stratum and exact-condition
counts, including zero cells, sampled n, determinate n and human-negative n.
Also pool matched versus mismatched descriptively; unequal raw counts alone
cannot identify population error-rate bias in this stratified sample.

Classify each human rationale by these fixed primary categories, in priority
order if multiple descriptions apply. Save all text/keyword evidence and any
secondary matching categories; do not retune categories after seeing totals.

1. prerequisite loop: notes explicitly describe missing/insufficient tool,
   input, fuel or prerequisite AND looping/repetition;
2. repeated collection: notes describe repeated mining/collection for quantity,
   without the above missing-prerequisite loop;
3. action needed/valid under both: notes explicitly state shared/current-valid
   action or old/new equivalence, rather than stale-specific behavior;
4. reasonable verification: notes describe a query/check/test followed by no
   continued reliance or explicitly call the behavior validation/exploration;
5. other: no sufficiently explicit evidence for the above.

Use deterministic literal cue matching with the above semantic requirements,
and export the exact matched cues for inspection. Do not infer an annotator's
reason from a scorer label. For each observed category and metric, select the
lexicographically first neutral audit ID as the example; zero-count categories
have no invented example. No new significance test or balance threshold.

## 3. A8 ReAct K+N and K+S positive-case audit

Start from the frozen A8 episode table, not a newly selected cohort. Expected
positives are K+N 14/52 and K+S 24/52. If actual saved counts differ, report both
and use the saved complete 52-instance paired set without replacing episodes.
For every originally discriminative-positive action, export full parameters,
feedback, referenced later actions, stale mechanism IDs/assertions, truth in
target and comparison worlds, and the actual scorer world argument. Verify
target world against frozen visible material; do not confuse comparison-world
truth with the scorer's reference-world choice.

Static mechanism checks read the three frozen truth files and documented
resource-yield table only; no environment reset, action, simulation or oracle
planning. Check the full target-world recipe/name/mechanism universe, not just
whether the limited conflict catalog happens to contain a matching valid row.
Separate mechanism validity from execution-time missing inputs/tools/fuel.

Action categories, fixed in advance:

- (iii) reference-world defect: saved scoring reference is demonstrably not
  the material's target world, or an alleged stale assertion is true in the
  target and false only in the comparison world;
- (ii) non-specific/misclassified action: an identical public action is
  licensed by target-world facts (including the same actual recipe output),
  or it is a shared mining action without a demonstrated stale-prediction
  dependency. For mining, a same-name item from initial inventory or another
  producer is not causal evidence; use existing FIFO item-flow provenance.
- (i) stale-specific: target facts do not license the concrete action but the
  stale assertion/another-world valid mechanism does, or the shared action has
  a demonstrated downstream reference to its stale predicted product, with
  FIFO origin at that action or the referenced item absent from inventory.
- (iv) other/undetermined: neither proof is available, format unsupported,
  source mismatch, or semantics cannot be established. Preserve the reason.

For conflicting multi-mechanism candidates, any proven (i) makes the action
positive; otherwise an unresolved candidate makes it unresolved, rather than
discarding it because another candidate is a false positive. Episode priority
is (i), then (iv), then (iii), then (ii). Export action-level labels as well as
the primary episode category. Textual absence of stale facts from K+N is not
proof of an internal model-prior cause: genuine events are reported as
consistent with unstated knowledge, with no causal source claim. Inspect the
actual selected material lists rather than infer contents from condition names.

Corrections are **positive-case corrections only**, not a new global scorer:
original negatives stay zero (unreviewed for false negatives); proven (ii)/(iii)
actions no longer establish stale-specific contact; (i) stays positive; (iv)
gives a conservative episode interval [0,1] unless another (i) establishes 1.
Apply this to all 52 paired instances per condition and save new files only.
When point-identified, report corrected K+S minus K+N and its A8 family-cluster
percentile bootstrap interval (10,000 draws, NumPy seed 20261017). Otherwise
report the identified difference bounds and separate bootstrap sensitivity
intervals, not an invented single point. Preserve the frozen 24/52, 14/52 and
their original paired contrast alongside corrections.

## Deliverables and limits

Report, comparisons with Wilson intervals, matrices, every CDS alignment,
main-error category/stratum/condition tables and examples, all A8 positive
actions and mechanisms, corrected 52-pair scores/contrast, source integrity,
exceptions, tests, key-unseal record, and local commit. Any unknown provenance
or source mismatch stays visible. No frozen result or original key is changed.
