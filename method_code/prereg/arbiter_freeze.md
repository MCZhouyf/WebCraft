# ARBITER P7 Method and Evaluation Freeze

Date: 2026-10-09

Status: **FROZEN; AUTHOR-AUTHORIZED READINESS-PIN REFRESH RECORDED**

This file freezes the post-selection method and confirmatory evaluation before
any of the 34 evaluation-task records are loaded, enumerated, inspected, or
executed. After this file is committed, method code and parameters must not
change. Analysis and reporting code may only implement the decisions written
here without changing them.

## Dependency pin

- WebCraft commit: `613a879db53345c2895ab8ef3fda638423978b80`
- ARBITER WebCraft pin SHA-256:
  `0bbf3974f8c8c5c28299eb1550716a731c23c8c468dc7ccaad1825e6e1c99a7b`
- Formal-experiment readiness decision SHA-256:
  `a35333ddc3e7bfef9e9875af808e2dfdbd986c13d90b9b103df8bd63476f024d`
- WebCraft v6.2 freeze manifest SHA-256:
  `20446196ba5434388c7ef10d6ea69350d97e7700fb7a5638e0c0fcd4225e245e`
- `prereg/corpus_freeze_p1.md` SHA-256:
  `0eab8fd53fd581755116adbe888bf22715958bdea5021351a2b515f7f3d34c7f`
- `prereg/amendment_v6.md` SHA-256:
  `4506f328b5cbc0c178e4b1564406b61534409947610c9488c5165a9d42048cd9`
- Main-table input contract SHA-256:
  `63a04dd72fb56c9714ca4247ce2536476f3b1e5980ab819b2452f7fb3e977b22`

The pin covers `sim/`, `truth/`, `data/`, `config/`, both formal index trees,
the complete public claims-cache tree, and `prereg/`, plus the payload schema,
normalizer, matcher, freeze manifest, formal-readiness decision record, and
main-table contract. It excludes
`__pycache__`, `corpus/raw/`, and `corpus/private/`. WebCraft is read-only.
The main experiment uses only `corpus/indexes/formal_p1/` and
`corpus/claims_cache/formal_p1/`; v1/v2 freeze entries are superseded by the
v3 index freeze and subsequent append-only v6.2 cache/reading-mode freeze.

### 2026-10-09 readiness-pin refresh

The author instructed ARBITER to refresh its pin after the three-party
readiness step. WebCraft HEAD remains the already frozen commit
`613a879db53345c2895ab8ef3fda638423978b80`; index, cache, simulator, truth,
task, contract, and freeze-manifest bytes are unchanged. The refreshed pin adds
the previously omitted `reports/paperA/FORMAL_EXPERIMENT_READINESS.md`, whose
decision record authorizes the below-target branch at shared compilation
closure `155/369 = 42.0%`.

An earlier complete evaluation run had already been executed against the same
WebCraft commit and data before this pin-coverage correction. That run is
retained as historical provenance and is not used as the requested post-refresh
execution. The method implementation and every method parameter remain exactly
as frozen above. A new run starts from an independent output directory after
the commit containing this refresh; no method code or parameter may change
after that commit.

## Shared pipeline

- Planner: inventory-aware MIP with receding-horizon replanning after every
  physical action, five candidate plans, uncertainty weight `w=0.35`, concrete
  failed-action deduplication, and resource-error isolation.
- Planner SHA-256: `ca1b494c778ca1dbc3ab3839dac4ec10cb12787d7706ac6d0dfd1afbe61c7d8a`.
- Shared execution-shell SHA-256:
  `1375571b404900447c57d0940729139b442b1052a1fe2ec3fa31cba0578052de`.
- Shared compiler version: `1`; compiler SHA-256:
  `a89d706aaf97b7f2f042c148756dbf31de36a6d71cc6ecde1563ee694cac63e5`.
- Claim payload schema SHA-256:
  `b46328b2f52b50d9708b3b3e7bca2ac49cc07bbdac1eea2852c1b3dd2969c89a`.
- Public claims contract: v2, exactly 11 fields. Frozen LLM
  `extractor_version=96b07e80`.
- Reader: version 5; display name, normalizer family name, then ID query;
  unique page-title follow-up; retrieval `k=10`; at most 60 searches; one MIP
  gap retry. Searches cost zero but their counts are reported.
- Action cap: 90 primary semantic-action cost units per episode.
- Reading modes: `all_public` is primary and admits every public cache claim;
  `llm_only` is a control and admits only `extractor_version=96b07e80`.
- Method matrix SHA-256:
  `6d51d514c835b1771015dfaad1d50915c279f5657f036956442005fc387fa94d`.
- Shared hyperparameter file SHA-256:
  `55f897ac80b10b43532bf3636b392651e0e0fcab9e83b3a513eadee65ca96db4`.

## Frozen ARBITER method

ARBITER uses the context-mixture posterior and decision-driven VOI. The
inference parameters are `alpha=1.2`, `beta=1.2`, `lambda=8.0`,
`tau_s=0.95`, `tau_inf=0.10`, `kappa=0.001`, `delta=0.05`, `N=5`, and
`J=5`. It enumerates five plans, uses `w=0.35`, and allows at most 12 explicit
probes. Only an executable first step of a current candidate plan is probe
eligible.

The frozen binary decision-loss expression is
`min(p, 1-p) * 10 - action_cost`. The value `10` is the decision-error loss
scale referred to as the probe tradeoff cost in the author instruction; an
executed probe is additionally charged its actual frozen primary action cost.
Positive-value candidates may be probed. Probe intent is declared before the
action. Probe outcomes and non-resource execution outcomes update context
belief; resource failures do not. `HTR_commit` excludes declared probes,
whereas `HTR_all`, probe count, probe cost, and OVR retain them.

## Frozen method set

All methods use the shared reader, compiler, planner, executor, inventory
refresh, replanning, failure deduplication, and action limits above. They differ
only as follows.

| Method | Belief | Verification |
|---|---|---|
| `parametric` | Five frozen LLM-prior samples only; no retrieval | None |
| `probe_then_parametric` | Same frozen LLM prior | Every selected first plan dependency is executed as a full-cost probe |
| `naive_rag` | Latest-timestamp retrieved claim per mechanism | None |
| `majority_rag` | Retrieved-claim majority per mechanism | None |
| `truthfinder_rag` | Source-trust iteration | 20 iterations maximum, convergence threshold `1e-6`; no probes |
| `verify_all` | Same latest-timestamp belief as Naive RAG | Every selected first plan dependency is executed as a full-cost probe |
| `arbiter_shell_naive` | Same latest-timestamp belief as Naive RAG | No probes or belief updates; explicit same-shell ablation |
| `arbiter` | Context-mixture posterior | Frozen VOI rule above |
| `oracle` | Evaluator truth only | Evaluator-only upper bound; never enters agent evidence |

Because the fairness audit moved the receding-horizon shell into the shared
pipeline, `naive_rag` and `arbiter_shell_naive` intentionally have identical
runtime behavior. The separate name preserves the prospectively requested
same-shell comparison row and prevents comparison to the historical one-shot
Naive implementation.

The runtime prior bundle contains five samples over 40 compilable mechanisms
and no truth/evaluator fields. Its SHA-256 is
`f02cbdd5ab5dafaf43d1320e21a2765eb3e42607eea650cab67fd8dc281cd2b2`.
Its frozen unconditional source hash is
`d905263736f2572b757e519587d872ac28d4f4c57b46902892646ea37bc82c6a`;
its assertion projection source hash is
`70a45ee211950785ee11e3419a48e8db954f1a230455bcc2ab886a075eef94ec`.
No new LLM call is permitted for the main experiment.

## Development selection disclosure

| Round | Development-only change observed before the final selection |
|---:|---|
| 1 | Initial formal pipeline; fixed query tokenization, VOI counterfactual observation, executable probe parameters, and shared goal-prerequisite retrieval. |
| 2 | Replaced MIP no-good cuts over action multiplicities with mechanism-support cuts. |
| 3 | Added prospective G1/G2 gates, corrected shared drop metadata handling, and introduced demand-driven shared retrieval at `k=5`, 40 searches. |
| 4 | Adopted the 11-field claims-cache v2 contract and V3 cache; no method hyperparameter change. |
| 5 | Froze reader v5 (`k=10`, 60 searches), `all_public`/`llm_only`, and probe-aware `HTR_commit`; no belief change. |
| 6 | Extended the shared compiler to all three public payload forms, display-name normalization, family expansion, shaped layouts, smelting alternatives, and repeated drops. |

The final risk-loss VOI method and shared execution-shell correction were
designed after observing rounds 1-5 and the round-6 engineering result. The
development-set PASS is therefore a post hoc selected hypothesis, not
confirmatory evidence. The 34 frozen evaluation tasks are the only
confirmatory test and were not read or run before this freeze.

The shared development pipeline ceiling is frozen as required/retrieved/
compiled = `369/226/155`: 61.25% retrieved, 42.01% compiled overall, and
68.58% compiled conditional on retrieval (reported as 61%, 42%, and 69%). It
applies equally to every retrieval method.

## Confirmatory experiment and analysis

- Tasks: 34 frozen evaluation tasks; no development task may enter.
- Cells: 11 registered condition/world cells: matched x3,
  mismatched-old-to-new x1, mismatched-new-to-old x1, mixed x3, polluted x3.
- Methods: all nine rows above.
- Seeds: `20261006`, `20261007`.
- Reading modes: `all_public`, `llm_only`.
- Total: `34 x 11 x 9 x 2 x 2 = 13,464` episodes.
- Report `all`, `two_sided`, and `one_sided` separately and report by world.
- Report SR, `HTR_commit`, `HTR_all`, probe count, probe cost, search count,
  OVR, IC, IAA, feasibility F1, and OPR where defined.
- Mechanism-connected cluster bootstrap: 10,000 replicates, seed `20261005`.
- Deterministic duplicate seed results are reported as `seed has no effect`;
  variance is not fabricated.

The two primary all-public rules are frozen verbatim in operational form:

1. `Naive HTR_commit - ARBITER HTR_commit >= 0.15` and ARBITER SR is not
   lower than Naive SR.
2. `arbiter_shell_naive HTR_commit - ARBITER HTR_commit > 0` and its paired,
   mechanism-cluster bootstrap 95% interval excludes zero.

The paired unit is `(condition, world, task_id, seed)`. Every result, including
nonsignificant and adverse results, is reported.

- If both rules hold: the evaluation supports lower committed stale reliance
  versus Naive RAG and attributes a nonzero reduction to ARBITER's belief/VOI
  decision rule under the same execution shell.
- If only rule 1 holds: the evaluation supports an operational reduction
  versus Naive RAG but does not isolate that reduction from the execution
  shell; the method-specific claim is not supported.
- If rule 1 fails: the evaluation does not confirm the preregistered task-level
  ARBITER claim, irrespective of secondary analyses.

`HTR_all` is interpreted alongside the method claim that ARBITER avoids
committing a plan on stale belief. Physical probing of a stale mechanism is
not hidden: probe count, probe cost, `HTR_all`, and OVR are always co-reported.

## Non-access declaration

At this freeze point, no evaluation-task record has been loaded, enumerated,
inspected, or executed by the ARBITER session. All timing before evaluation
uses only the frozen eight-task development set. WebCraft remains read-only,
and no Minecraft, GPU, or concurrent IaP process is changed.

The author-authorized resource threshold for the remaining deterministic
checks and experiment is total effective available memory of 2 GiB, activated
explicitly as `ARB_AUTHORIZED_MEMORY_TOTAL_BYTES=2147483648`. The default
resource rule is unchanged without that value. Freeze verification passed all
72 tests with 403,960 KiB peak RSS; `compileall`, the post-test pin check, and
the formal agent/adapters static scan (zero violations) also passed.

## Post-run baseline addition

Date: 2026-10-09. This section records the author-directed addition of the
amendment-v5 `probe_then_rag` baseline after completion of the nine-method main
experiment. It was omitted from the P7 method enumeration. The baseline is
therefore labeled post-run in every table and is not used to change either
preregistered ARBITER decision. No existing method, parameter, task, episode,
or result is changed or rerun.

`probe_then_rag` uses the same reader-v5 evidence bundle, public claims cache,
shared claim compiler, latest-timestamp Naive-RAG aggregation, MIP planner,
receding-horizon execution shell, 90-action total budget, tasks, cells, reading
modes, and seeds as the main run. It differs only by the following frozen
prelude and temporal filter:

1. Probe separator `cf_f222b76ffee74fa3` by mining one stone, crafting the
   concrete cobblestone stone-pickaxe layout, and mining iron ore. All three
   semantic actions are declared `intent=probe`. Producing `raw_iron` selects
   the post-1.16 branch; any other public product selects `1.16.5` and stops
   version probing.
2. On the post-1.16 branch, probe separator `cf_c8f3718a4637369f` with
   `check_name("turtle_scute")`, also declared `intent=probe`. A resolvable name
   selects `1.21.11`; an unresolvable name selects `1.19`.
3. The decision tree uses at most two separator points. Prerequisite actions
   remain fully charged, so it uses three semantic probe actions in `1.16.5`
   and four in `1.19`/`1.21.11`. Their cost is deducted from the shared
   90-action episode budget. Probe actions count in `HTR_all`, probe count,
   probe cost, primary cost, and OVR, but not in `HTR_commit`.
4. After identification, the baseline emits one public `report_version` call
   and retains only search rows whose timestamp is no later than the identified
   version's frozen `corpus_cutoff` in `webcraft/corpus/release_times.json`:
   `2021-02-13T16:05:32+00:00` for `1.16.5`,
   `2022-07-07T09:42:18+00:00` for `1.19`, and
   `2026-01-08T12:23:30+00:00` for `1.21.11`. Claims whose `chunk_id` is not in
   a retained search row are removed before the unchanged shared compiler.
5. If a prerequisite separator action fails or the public separator result is
   malformed, the episode stops as an execution failure. It does not inspect
   simulator private state or evaluator truth and does not fall back to the
   execution-world label.

The supplementary matrix is `34 x 11 x 2 = 748` episodes per reading mode,
written to an independent artifact directory. It must make zero LLM calls and
must verify exact equality of the two deterministic seed records after removing
only the seed field.
