# W7b zero-call review diagnostics preregistration

**Registration date:** 2026-10-16 UTC. This document is committed before any
W7b intervention, CP-RAG evaluation, split-placebo rerun, cost rescore, or
reconstruction result is computed or inspected.

## Scope, invariants, and chronology

W7b is a deterministic, zero-LLM-call post-review diagnostic. It reads frozen
truth, tasks, conflicts, corpus-derived claims, simulator, planner, main-matrix,
EXT-v2, CDS, external-agent logs, and release assets. It does not edit those
assets or any previously reported number. All counterfactual results are
written to `reports/paperA/review_v14_zero_call/` and are labeled diagnostic.

The ten executable pipeline rows are `parametric`,
`probe_then_parametric`, `naive_rag`, `majority_rag`, `truthfinder_rag`,
`verify_all`, `arbiter_shell_naive`, `arbiter`, `oracle`, and
`probe_then_rag`. CP-RAG is an additional diagnostic row. Deterministic runs
use seed `20261016`, `PYTHONHASHSEED=0`, and no gateway or model client.

All percentile intervals below use 10,000 bootstrap draws and NumPy RNG seed
`20261016`. Main-matrix resampling units are the 34 evaluation task IDs: every
cell, method, and intervention observation for a sampled task travels together.
EXT-v2 and CDS use the preregistered mechanism family as the resampling unit.
No row, task, family, or outcome may be dropped after results are known.

The source inputs are pinned at rule-registration time:

| Asset | SHA-256 |
|---|---|
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` |
| `data/conflicts.jsonl` | `e655dc89e7a51c9c2973649b0c4cd605f56efabf989499e90bfece7411db7f44` |
| `reports/paperA/c_m_dv.csv` | `fc07fa0c1add3969a8e695cadfb39bb5418c301c397cffc41c80b25181cc411a` |
| ARBITER `all_public_manifest.json` | `a0d3cac2a0ed1fbbe1d4b57e964830256dfd2e40d5a9a09831bb1adca8ac9976` |
| W7a CDS material manifest | `0c17870198d1fbbaeaedbb3a3b2ba6ce87b119f13ec8dff01d44a84b6a511838` |

## 1. Validity-by-coverage orthogonal decomposition

### Population and cells

This diagnostic uses `all_public`, all ten pipeline rows, and the 34 frozen
evaluation tasks. The two mismatch/match pairs are:

1. T1 (2021) evidence in world 1.21.11 versus matched T3 evidence in 1.21.11;
2. T3 (2026) evidence in world 1.16.5 versus matched T1 evidence in 1.16.5.

The first pair is the prespecified primary decomposition because its frozen
Naive estimate is the reported 11.7647 percentage-point matched-minus-mismatch
SR gap. The reverse pair and every method are mandatory companion analyses.

### Evidence interventions

For each original retrieved hit and its compiled claims, construct four
conditions without changing query text, hit count, hit order, score, source,
timestamp, or position except where C explicitly appends a new hit:

- **O (original):** byte-equivalent frozen evidence.
- **V (validity correction):** if a compiled claim is false in the target
  world, replace only its payload with the target-world truth for the same
  executable semantic relation. Relation keys are `(family, subject,
  terminal-action-kind)`: drop block for drops, output plus station for
  recipes/smelting, identifier pair for names, and goal for craftability.
  The replacement is generated directly from the target truth files and
  serialized through the public claim schema. When no target-world relation
  exists, retain the original claim and mark `no_valid_counterpart`; report
  this count separately. Multiple valid truth routes are serialized in
  canonical action-JSON order. Non-stale claims remain byte-equivalent.
- **C (coverage completion):** for every target-world oracle-path relation not
  represented by any compiled O claim, append one synthetic-in-memory hit
  containing the target truth generated directly from truth files. Its source
  is `w7b_coverage_completion`, its timestamp is the evidence era (T1 or T3),
  and appended hits are ordered by oracle-path position then canonical payload.
  Existing stale and valid claims are retained.
- **V+C:** apply V, then compute missing path relations and apply C.

No template gold inventory is read. Drop truth, recipes, smelting, names, and
craftability are generated from frozen truth and the target-world oracle path.
Intervention metadata is evaluator-private and is not exposed to methods.

O must reproduce the corresponding frozen episode outcome and semantic action
sequence exactly; any mismatch stops this component and is reported. For each
method, cell, and intervention report SR, overconservatism among feasible
instances, execution failure among feasible instances, and plan-level stale
acceptance. Paired effects use `intervention - O`:

`dV = V - O`, `dC = C - O`, `dVC = V+C - O`, and
`interaction = (V+C) - V - C + O`.

The requested allocation of the 11.7647 pp loss is reported as the Naive
primary-pair SR recovery under dV and dC with dVC and the interaction beside
them. Because effects need not add when the pipeline is nonlinear, dV and dC
are not normalized into percentages that sum to 100.

## 2. Version-blind conflict-aware CP-RAG

### Frozen behavior

CP-RAG uses the frozen reader, compiler, planner, simulator, limits, and Naive
aggregation. It receives no version mapping, candidate-version list, separator
probe table, private task metadata, truth vector, or oracle path.

At each demanded mechanism during planning:

1. Group compiled candidates by the same executable relation key. If fewer
   than two distinct payloads remain, use Naive behavior.
2. If at least two payloads conflict, execute the cheapest supported
   discriminating action. Cost ties are resolved by semantic cost, then action
   count, then canonical action JSON. Names use `check_name`; recipes try each
   canonical candidate grid; smelting tries each candidate input/fuel tuple;
   drops mine the named block and observe item/count; craftability uses the
   cheapest terminal action implied by a candidate. Probe actions retain the
   existing `intent=probe` logging convention and count toward cost.
3. Retain candidates consistent with the public observation and discard
   contradicted candidates. If all candidates for the demanded relation are
   contradicted, issue exactly one frozen-reader query using
   `"<denied subject> <goal item>"`, compile the returned claims, and consider
   the next canonical candidate. No more than the existing search/probe/action
   limits are allowed.
4. One-sided evidence follows Naive: execute it; on public failure, replan with
   that candidate disabled. If no candidate or plan remains, submit infeasible.

The action sequence includes every search, probe, semantic action, and terminal
submission. Sequence agreement is exact normalized action-JSON equality.

### Development and freeze

Only the eight frozen development tasks may be used before the implementation
hash is frozen. Debugging may repair crashes, schema violations, nondeterminism,
or divergence from the four numbered rules; it may not select thresholds,
queries, tie breaks, or behavior based on SR/HTR. A second preregistration
commit records the CP-RAG source hash and a twice-run deterministic dev test.
Evaluation results may not be inspected before that commit.

After freezing, run once on (a) 374 evaluation task-condition instances in
each of `llm_only`, `all_public`, and `oracle_reading`; (b) the frozen EXT-v2
Web instances; and (c) all frozen CDS instances. Report exact action-sequence
agreement with Naive and Verify-All; SR, feasible SR, overconservatism,
execution failure, plan acceptance, stale-specific query/action exposure,
semantic actions, semantic cost, and search calls. Paired SR differences are
CP-RAG minus Naive with the intervals defined above.

## 3. Split placebo interventions

Use all ten frozen pipeline rows, `all_public`, the same unique evaluation
instances and seed as the prior placebo.

- **Metadata placebo:** apply a fixed-seed bijection to source-site labels and
  deterministically replace only registered decorative text spans. Scores,
  ranking keys, tie order, top-k membership, claim payloads, timestamps,
  version tokens, and all executable text remain unchanged.
- **Candidate-order placebo:** preserve every hit and field but reverse the
  canonical order within exact-score tie groups using the existing fixed
  perturbation seed. This may change top-k content only when a tie crosses the
  top-k boundary. It does not perturb source labels or decorative text.

Each intervention is generated once; no retry or redesign is allowed. Report
per method: retrieval-sequence changes, top-k set changes, terminal changes,
and plan-acceptance changes. A source-only display difference does not count as
a retrieval-sequence change after canonical source-label normalization.

## 4. Search-cost sensitivity

Rescore, without behavioral reruns, every unique pipeline episode in each
available reading mode and the existing first-round external five-method
episodes. For `c_s` in `{0.1, 0.25, 0.5}`, adjusted cost is frozen semantic
cost plus `c_s * search_call_count`. An episode is over budget iff adjusted
cost is strictly greater than its frozen action-budget value. Under the
`over-budget-is-failure` convention, an originally successful over-budget
episode becomes failed and every other terminal is unchanged. Report counts
and SR by method, reading mode/setting, and cost. External methods are explicitly
described as a static rescore: their policy did not adapt to search price.

## 5. Type-specific adjudication cost

Use all DV rows in the frozen `c_m_dv.csv`. Map types, before calculation, to
`name`, `recipe`, `smelting`, `drop`, and `craftability`; unmapped rows are
reported and stop this component. For each type report count, median, Tukey
quartiles (NumPy linear percentile), minimum, and maximum of the registered
minimum adjudication cost `c(m)`. No world-conditioned or worst-world cost is
substituted.

## 6. Reproducibility layers and reconstruction check

`release/docs/REPRODUCIBILITY.md` will define:

1. executor-layer bitwise replay from frozen tasks, truth, simulator, and
   expectations;
2. cache-layer reproduction of every table from released derived claims and
   per-instance results; and
3. raw-corpus reconstruction from page/revid manifests, Internet Archive dump
   identifiers, documented manual `Special:Export`, and local rebuild scripts.

The reconstruction test samples 20 eligible revid rows without replacement
using seed `20261016`. It may read already-downloaded public dump/export files
and Internet Archive identifiers but performs no HTTP request to any Wiki
domain. For each sampled revid it attempts the registered T1/T2 parse and
records source file, expected and observed page/revid, parser status, and
derived-claim count. Missing local dump material is a failure, not replaced by
network access or a different sample.

## Outputs and reporting

Outputs include intervention episode tables and paired effects, CP-RAG dev and
evaluation manifests, split-placebo diffs, cost sensitivity, type-specific
`c(m)`, the 20-row reconstruction audit, source/input hashes, and a report that
also lists all failures. Existing results remain untouched. The final report
must provide the requested decomposition effects and interaction, CP-RAG
agreement/effects for main/EXT-v2/CDS, both placebo terminal-change counts,
over-budget counts at all three prices, and the full type-specific cost table.

## CP-RAG implementation freeze

**Frozen after dev8 and before any evaluation-split, EXT-v2, or CDS CP-RAG
episode.** The five registered main cells over eight development tasks were run
twice (40 episodes per repeat) with `PYTHONHASHSEED=0`. Normalized action
sequences and terminals were identical for all 40 paired repeats. The dynamic
re-search branch was exercised 32 times across both repeats.

- `scripts/run_cp_rag_20261016.py` SHA-256:
  `32c167a9492f7c02b94a584901a5cf8bcb8740e6d61dc7bb1b5ee97f9ad3e4ed`
- Machine-readable freeze: `reports/paperA/review_v14_zero_call/cp_rag/dev_freeze.json`
- Per-repeat audit: `reports/paperA/review_v14_zero_call/cp_rag/dev8_determinism.csv`

The evaluation phase aborts unless the source hash still equals the value
above. No development SR or HTR result was used to alter the registered policy.
