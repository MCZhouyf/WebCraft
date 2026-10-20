# ARBITER P6 Pipeline-Closure Amendment

Date: 2026-10-07

Status: preregistered before diagnosis or implementation changes.

This amendment records the author's determination that the completed formal P6
`FAIL` is a pipeline-closure failure rather than a method conclusion. It
authorizes one documented, method-independent pipeline repair round. It does not
authorize access to, execution of, or tuning on any evaluation task.

## Immutable boundaries

- Use only the eight frozen development tasks and their existing P6 matrix:
  three worlds, conditions `mixed`, `mismatched_old2new`, and
  `mismatched_new2old`, methods `naive_rag` and `arbiter`, and one seed.
- Do not read or execute evaluation tasks.
- Treat WebCraft as read-only and do not modify any frozen file.
- Do not call an LLM. Claims must come from the frozen ClaimsCache; priors must
  come from already-frozen prior samples.
- Do not change ARBITER belief inference, method-specific behavior, or method
  hyperparameters during the pipeline repair.
- Every pipeline repair must apply identically to every retrieval-based method.
- After round 3, at most one additional hyperparameter round is allowed, only
  for a `MARGINAL` result and only for `w`, `tau_s`, and `tau_inf`. A `FAIL`
  result is reported without further tuning.

## Step 1: diagnosis before code changes

Analyze all 144 episodes in `artifacts/p6_formal_latest/`. For every episode,
record:

1. terminal type: `solved`, `reported_infeasible`, `plan_not_found`,
   `budget_exhausted`, or `execution_failed`;
2. issued search count, unique retrieved chunk count, and claims admitted to the
   belief table;
3. graph closure: the fraction of `(item, mechanism)` requirements in the
   current-world oracle plan that have at least one matching claim in the belief
   table; for an infeasible task, use the critical mechanism needed to establish
   infeasibility;
4. MIP infeasibility reason: `missing_recipe`, `missing_drop`,
   `insufficient_resources`, or `unreachable_tool`.

Write per-episode records and method-by-condition summaries under
`artifacts/p6_diagnosis/`. The prospective hypothesis is that most failed
episodes terminate as `plan_not_found` or `reported_infeasible` and have graph
closure materially below 1.0. If that hypothesis is not supported, stop after
diagnosis and report it.

The diagnosis must also distinguish whether Naive RAG has HTR zero because it
issues no stale execution action or because stale-action classification is not
connected. It must report the number of truly infeasible task-world pairs among
the 24 pairs and, for each method, the number correctly reported infeasible.

Time limit for this diagnosis is 1.5 hours. Diagnosis itself must not change
implementation code.

## Step 2: prospective pipeline sanity gates

### G1: gold-claim agent

Evaluator-side test tooling derives every claim true in the current world from
WebCraft truth and injects those claims into the unchanged
claim-to-belief-to-MIP-to-execution pipeline. These claims never enter method
code or method results.

Pass criteria over the 24 development task-world pairs:

- SR = 1.0;
- every feasible task reaches its goal at oracle cost;
- every infeasible task is reported infeasible;
- HTR = 0.

### G2: stale-gold agent

Evaluator-side test tooling injects every claim true in world `1.16.5` and runs
the eight development tasks in world `1.21.11` through the same pipeline.

Pass criteria:

- success and stale-action classifications match the eight corresponding
  expectations in `data/stale_agent_expectations.jsonl` one by one;
- HTR > 0.

If either gate fails, repair only the shared claim-to-belief-to-MIP conversion,
including normalization, payload mapping, or quantity semantics. Record one
line per repair. Continue only after both gates pass.

## Step 3: shared demand-driven reader

The same deterministic reader is used by every retrieval-based method.
`parametric` remains non-retrieval and uses only frozen prior samples.

- Begin with the target item.
- For each unresolved `(item, mechanism)` demand, issue the fixed templates
  `<item> crafting recipe`, `<item> smelting`, `<block> drops`, and `<item> ID`.
- Use retrieval `k = 5` and admit only claims returned by ClaimsCache for the
  retrieved chunks.
- Add ingredients and other prerequisites from newly admitted claims to the
  demand queue.
- Continue until no new demand exists or 40 searches have been issued in the
  episode.
- If MIP planning fails, query its reported gap once more, still within the
  same 40-search limit.
- Search cost remains zero under the frozen cost model. Log search count,
  unique chunk count, and admitted claim count for every episode.
- Add mean search count to the P6 summary table.

The templates, `k = 5`, and per-episode search limit `40` must be literal frozen
configuration values in `config/arbiter_hparams.json`. Method behavior differs
only in the registered belief aggregator.

## Step 4: P6 round 3 after pipeline repair

Rerun exactly the same 144-episode development matrix and use the same gate
script. Report before/after terminal distributions and graph closure for every
method-condition cell. Report Naive RAG HTR separately for
`mismatched_old2new` and `mismatched_new2old`.

If either Naive mismatch HTR is below 0.20, classify the pipeline as still not
closed, stop, and do not interpret method differences. Continue to report the
registered `all`, `two_sided`, and `one_sided` tables. The development
`one_sided` subset overlaps the old-world infeasible tasks and therefore has
limited explanatory scope.

## Evaluation-task non-access declaration

This amendment and all authorized work use only the eight development task IDs
already frozen in the P6 manifest. Evaluation-task records must not be loaded,
enumerated, inspected, or executed. Any accidental evaluation-task access stops
the run immediately and must be reported.

## Checkpoint commits

- Pre-amendment ARBITER commit: `69d5933`.
- Amendment preregistration commit: `489f3d1`.
- Step 1 diagnosis commit: `f8a5128`.
- Step 2 G1/G2 commit: `865e321`.
- Step 3 shared-reader commit: `cb25772`.
- Step 4 round-3 result commit: `3b37f39`.

## Repair log

The following shared repairs and checks were made under this amendment; no
method-specific repair or hyperparameter change was made.

- G1/G2 evaluator baseline: G1 failed with SR `0.583333` and 14/24 oracle
  cost matches; G2 passed with 8/8 classification matches and HTR `1.0`.
- Repair 1: preserve evaluator-supplied tool requirements and guaranteed drop
  quantities when compiling a drop assertion to a shared MIP operator, while
  excluding that evaluator-only execution metadata from mechanism identity.
- Repair 1 gate result: G1 passed with SR `1.0`, HTR `0`, and 24/24 oracle-cost
  matches. G2 passed with 8/8 success/stale classification matches and HTR
  `1.0`. No second conversion repair was required.
- Shared-reader integration repair: compile drop claims as mine operators only
  when their subject is a node in the public, cross-world-identical frozen
  resource model. This prevents entity drops and unavailable scene blocks from
  becoming zero-input resource operators for every method.
- P0 cache audit: all four frozen `oak_planks` queries return zero cached
  producer claims in all three P6 conditions. No rule was synthesized or
  hard-coded to fill this evidence gap; round 3 retains it and lets the
  preregistered mismatch-HTR gate determine pipeline closure.

## Step 1 outcome

The diagnosis used all 144 frozen P6 development episodes and no evaluation
task. The prospective hypothesis passed: 66 of 105 failed episodes terminated
as `plan_not_found` or `reported_infeasible`, mean closure among failed episodes
was `0.120693`, and no feasible episode was solved. Nine of the 24 unique
development task-world pairs are truly infeasible. ARBITER correctly reported
all nine under each condition; Naive RAG reported 3/5/4 under
mixed/old2new/new2old. Naive RAG issued primary actions in 27 of 48 mismatch
episodes but issued zero evaluator-classified stale actions, so HTR zero is due
to stopping before a stale conflict action rather than a disconnected HTR
classifier. Detailed evidence is in `artifacts/p6_diagnosis/`.

## Round-3 outcome

The shared-reader rerun used 24 precomputed-cache evidence bundles and exactly
144 development episodes. It made no fallback or LLM call and did not read or
run an evaluation task. Mean graph closure increased from `0.088005` to
`0.137416`, but Naive RAG HTR was `0.0` in both `mismatched_old2new` and
`mismatched_new2old`, below the prospectively registered `0.20` pipeline
closure threshold. The scripted all-subset gate also returned `FAIL`: Naive
RAG/ARBITER SR was `0.236111`/`0.388889`, HTR was
`0.013889`/`0.013889`, and ARBITER mean probes was `0.208333`. No method
hyperparameter adjustment was made. Per the stop rule, P7 and the main
experiment remain prohibited.

## Round 4 authorization: cache contract v2, 2026-10-08

The author authorizes a fourth pipeline round using CLAIMS_CACHE_V3_READY,
without hyperparameter tuning, on the identical 144 development episodes.
The public claim contract now requires exactly eleven fields: the original
nine plus nullable `normalizer_version` and `parent_claim_id`. The recursive
forbidden-field set is unchanged. Legacy rows are upgraded by the upstream
loader; the ARBITER adapter rejects nine-field public returns.

The selected WebCraft commit is
`1307fe9d8a7ffc6fd820f276d5a91fe338982d7a`. Its P0 corpus freeze SHA-256 is
`9e58f5c860febdba9ed99e7ae0fa102275ec6520284cef74546e38cfc5167cf4`.
All twelve P0 index/cache directory hashes match the v5 freeze tables. Sim,
truth, task-data, and resource/config hashes are unchanged from round 3.
The upstream report states matched coverage 527/531 and closure 88/92;
these are corpus-side audit figures, not ARBITER P6 results.

Round 4 keeps the round-3 task snapshot, inference, planner, reader, retrieval
budget, and gate thresholds unchanged. No LLM is called. Output goes to
`artifacts/p6_round4_pipeline/`, and closure is compared with the preserved
round-3 diagnosis using its same required-mechanism sets. Existing
`judge_gate` and mismatch-HTR checks determine the reported decisions.
No evaluation-task records are loaded or run, and WebCraft remains read-only.

At the first round-4 launch, the commit advanced to
`dc1683307c1af79eea70629eeaf86dce527d331e`. The pin check stopped execution
before any evidence bundle or episode was generated. Every pinned file hash
was unchanged. The pin was refreshed to that latest commit before restarting.

Round-4 checkpoints: contract/pin update `a46a4cc`; concurrent-commit pin
refresh `6a4f918`; results and audit `c03a5e8`.
All 144 episodes completed. The unchanged gate returned `FAIL`, with HTR gap
`-0.08333333333333333`. Naive old2new/new2old HTR was `0.16666666666666666` /
`0.041666666666666664`; the unchanged diagnostic script returned
`pipeline_closed=false`. Mean required-mechanism closure increased from
`0.13741582491582488` in round 3 to `0.31047979797979797` in round 4.
No hyperparameter tuning or additional experiment follows this failed gate.

Post-round-4 evaluator audit: all 12 ARBITER HTR episodes replayed against their
saved public results and mechanism sequences. The 12 stale actions comprise 7
VOI probes and 5 committed plan steps. Naive RAG's 34 `execution_failed`
episodes comprise 6 catalogued stale claims, 22 uncatalogued extraction errors
that are false in the execution world, and 6 other execution failures for
world-true mechanisms. This audit changes no method state or result.

## Reader v5 prospective freeze, 2026-10-08

Before round 5, the shared reader is prospectively changed for every retrieval
method. For each unresolved item it issues, in order, the display name, the
normalizer-family name when one exists, and the namespaced item ID. After each
base-query hit set, it follows each unique returned `page_title` once for that
item. The retrieval depth is `k=10`, the per-episode limit is 60 searches, and
the existing single MIP-gap retry remains unchanged. Search events record the
query kind, query string, demand item, phase, and returned chunk IDs.

The family table is frozen in `config/arbiter_hparams.json` from WebCraft
`corpus/normalize_claims_cache.py`, SHA-256
`661c93765fd998d0cf69f855ae1a762ce33bad3fa192b326a9d8fb276850f367`.
Runtime verifies that source hash before retrieval. This is a shared reader
change only: no belief inference, method hyperparameter, planner, task, or gate
logic is changed. Formal cache loading and pin readiness now require the exact
`CLAIMS_CACHE_V4_READY` marker.

At freeze time WebCraft HEAD is `d76db011b5b6838b7f0371caec86d6beba95cf37`;
neither the exact V4-ready marker nor amendment v6 is present. Therefore no
round-5 evidence bundle or episode has been generated. The formal WebCraft pin
will be refreshed only after both prerequisites arrive and are reviewed.

## Test-resource exception, 2026-10-08

The author lowered the reserve used only by pytest and static checks from 8 GiB
to 6 GiB, in addition to the test suite's 512 MiB planned allocation. This
exception is implemented only in `tests/conftest.py`. The production
`require_memory` default remains 8 GiB, so all experiment entry points retain
the original resource gate. Reader v5 must pass the complete suite before any
round-5 run.

The authorized run completed with all 46 tests passing under `nice -n 15`, no
visible GPU, and one BLAS/OMP thread. This satisfies the reader-v5 test gate but
does not waive the separate corpus and amendment prerequisites for round 5.

## Probe-aware HTR amendment V6, 2026-10-08

WebCraft amendment V6 is frozen at commit `1991c20`. This change is explicitly
post hoc to round 4 and prospective to round 5: the round-4 evaluator audit had
already shown that 7 of ARBITER's 12 stale episodes were VOI probe actions and
5 were committed plan actions. No round-4 value is relabelled or discarded.

Round 5 uses `HTR_commit`, which applies the existing stale-action test after
excluding action records whose agent-declared `intent` is exactly `probe`, as
the primary HTR gate. `HTR_all` is the unchanged prior HTR definition and is
reported and judged in parallel. Missing or other intent values are committed.
The evaluator reads intent but does not verify its truthfulness. Every probe
retains its primary semantic-action cost and OVR eligibility; tables report
probe count and probe cost separately.

The primary P6 decision is PASS exactly when the Naive-RAG minus ARBITER
`HTR_commit` gap is at least 0.15 and ARBITER SR is not lower. The former
mean-probe cap is removed from this conjunction. Applying the same 0.15/SR
rule to `HTR_all` produces a separate comparison decision. No task, world,
condition, seed, reader-v5 setting, planner, inference parameter, or cost rule
changes. Round 5 remains unrun at this amendment point.

## Structured reading modes for round 5

Following the V6 no-regression reconciliation and amendment V6, the default
round-5 mode is `template_union_llm`: it admits exactly cache claims with
`payload.extractor=template` or `extractor_version=96b07e80`. The preregistered
control is `llm_only`, which admits only the latter. The mode is shared by both
methods and changes only cache-row admission; retrieval, belief aggregation,
planning, tasks, and all method hyperparameters remain unchanged. Evidence
bundles record the mode and cannot be reused across modes.

The current normalizer source SHA-256 is
`04adaa1adf0d1ee9a722a99150f6e0e664c35eff792d84c0f7e8023ddc96b29f`.
The formal pin must include the complete public claims-cache tree and
`prereg/amendment_v6.md` before execution. No pin refresh or round-5 episode is
part of this implementation checkpoint.

## Reading-mode v6.2 correction and memory exception, 2026-10-09

The author supersedes the preceding `template_union_llm` mode before round 5.
The primary mode is `all_public`, which admits every row passing the public
eleven-field cache contract without inspecting extractor markers. The
`llm_only` control remains restricted to `extractor_version=96b07e80`. Every
evidence bundle records its mode and counts admitted claims by the v6.2
effective-extractor rule: a nonempty `payload.extractor` marker takes priority,
the frozen LLM version maps to `llm`, and other rows map to `unclassified`.

For pytest, static checks, and each deterministic 144-episode P6 run, the
author-authorized resource gate is the planned allocation plus a 2 GiB reserve.
This exception does not change any other experiment entry point. Peak process
RSS and effective available memory are recorded before and after each run. If
the gate fails, polling is at ten-minute intervals without terminating or
modifying any concurrent process.

Admission checkpoint: all 50 ARBITER tests passed with a child-process peak RSS
of 398,820 KiB. The formal pin selects WebCraft
`a571e8546603e50e5d57b73d162bc5457b36129e`; all readiness flags are true.
The pin includes the complete public claims-cache tree, both v6.2 corpus freeze
files, the effective-extractor implementation and frozen count CSV, and
`prereg/amendment_v6.md`.
