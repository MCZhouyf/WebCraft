# Alias-locked subset replication, 2026-10-11

This is a post-review replication. It adds a new table and does not change any
first-round result, frozen method, prompt, parameter, task, or scoring rule.
This file, the exact instance list, the public runtime sample, and all run and
analysis code are committed before the first model request.

## Frozen scope

- ReAct: the 47 episodes in its first-round minority returned-identifier
  stratum.
- Reflexion: the 10 episodes in its first-round minority returned-identifier
  stratum.
- Total: 57 episodes. Seed remains 20261006.
- Exact identities are in `prereg/alias_locked_instances_20261011.csv`,
  SHA-256 `ad986cf24b343fca798f2419e405ce1147f54ae4527b5b1c2db717bc422bcdc1`.
- The identity source is the already exported
  `reports/main/post_review/alias_stratified/rerun_instances.csv`, SHA-256
  `9b851191d3723ed208b297224d68e2ae1c3197fd8662e58de1d53501dfcd6b72`.
- The runtime receives only public goals and initial inventories in
  `prereg/alias_locked_sample_20261011.json`, SHA-256
  `0d8f9b203133bc4fa1fc9cbfd65fa4f9b80c70ca5117937e2cabb513d55b0c89`.
  It contains no feasibility, oracle plan or cost, conflict truth, hidden
  state, or candidate-version list.

The first-round minority identifier was `zai-org/GLM-5.2`. The first-round
majority identifier was `glm-5.2`. This replication requests `glm-5.2`, and
every successful call, including canaries, must return exactly `glm-5.2`.
Any other returned identifier is a non-retryable protocol stop. Therefore a
second identifier stops that method immediately and no mixed-identifier
result may be assembled.

## Frozen method and runtime

Everything below is byte-for-byte or value-for-value identical to the first
round:

- Shared implementation: `src/arbiter/evaluation/external_agents.py`, SHA-256
  `37c96dbb0eeefb749d38153ccf9c5916c6441f6412b264a92bccfe43c6064734`.
- ReAct system prompt: 881 UTF-8 bytes, SHA-256
  `5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84`.
- Reflexion system prompt: 882 UTF-8 bytes, SHA-256
  `e2ea63c01a48323f49bb29fb21a746184ae34641cdc2373012b774f858223a6e`.
- Temperature 0.0, JSON-object output, thinking disabled, maximum 4096
  completion tokens, and an eight-step recent trace.
- Maximum 40 model calls and 90 charged semantic actions per episode.
- The raw public `search` snippets from frozen `corpus/indexes/formal_p1` are
  the only reading interface; no claims cache or compiler is used.
- The same `run_external_episode` action parser, tool descriptions, temporary
  Reflexion lesson semantics, simulator API, and seed are used.

Only network, timeout, HTTP 408/429, and provider 5xx failures may retry, at
most twice after the first attempt. Authentication, canary, budget,
model-identity, protocol, refusal, and model-output failures are not
infrastructure retries. A canary runs before every shard and after each 200
non-canary calls. Credentials are supplied only through the process
environment and are never persisted.

The ARBITER WebCraft pin is commit
`166d48a317fc3a8de72a5329ed45412826c29ad2`; `config/webcraft_pin.json` has
SHA-256 `697b81a8d33b27ecef36cffcff082a9ea20eed0548799306a8cd98741199389d`.
Every pinned file is verified before a shard starts. WebCraft is read-only.
No GPU is used. The authorized memory reserve is 1.8 GiB plus each worker's
planned allocation, and no non-task process is modified.

## Frozen scoring and judgments

Gold scoring and terminal priority are exactly
`prereg/external_rescore_20261010.md`, SHA-256
`25dd3d8f386b7d9be711a93c1ba51ec76e8f5393d88cfc2d170f9e5cbfebd4e4`.
Scoring reads evaluator data only after all runtime episodes have finished.

The first-round majority-layer reference intervals use 10,000 independent
episode-bootstrap draws. A sampled feasible-task ratio is retained only when
its sampled denominator is positive.

- ReAct, seed 20261011: majority layer n=63; overall SR=0.269841; bootstrap
  95% CI [0.158730, 0.380952]. Judgment: whether the locked-replication
  overall SR lies inside this interval.
- Reflexion, seed 20261012: majority layer n=100 with 65 feasible episodes;
  feasible-task SR=0.061538; bootstrap 95% CI [0.014286, 0.126761]. Judgment:
  whether the locked-replication feasible-task SR lies inside this interval.

For each method, the two family-level threshold statements are also evaluated
without alteration: feasible-task SR <= 0.10, and HTR_all > the frozen
pipeline reference 0.155. A statement is called unchanged only when its
boolean value matches the corresponding first-round majority layer.

The locked results are reported as an independent "identifier-locked subset
replication" beside `first_round_all_identifiers` and
`first_round_majority_identifier`. They never replace a first-round Table 8
number. Scope is not enlarged or reduced after observing results.

Outputs are written under
`reports/main/post_review/alias_locked_replication/`: `main_table.csv`,
`episode_metrics.csv`, `judgments.csv`, `family_conclusions.csv`, and
`REPORT.md`. Actual provider cost is reported only if supplied; otherwise it
is unknown, never zero.

## Frozen code

| File | SHA-256 |
|---|---|
| `scripts/select_alias_locked_replication.py` | `16e7e7cdbed7e142b8fcda462aeabd653bc42fc189f8afbffe73f469e13b723d` |
| `scripts/run_alias_locked_replication.py` | `d052dd1841c7f00604ff05299b948130250f7e3544d095f650af38c3d9f9396c` |
| `scripts/assemble_alias_locked_replication.py` | `be65343218b5c02887d995b7b110946199b2e066197fe06ff5b50352ecc31ee6` |
| `scripts/score_alias_locked_replication.py` | `3a17bad7543eb057c9e09f2b8f43b75fb507eeb1feffebc9cba2f3a1f8879042` |

No model request for this replication existed when this preregistration was
written.
