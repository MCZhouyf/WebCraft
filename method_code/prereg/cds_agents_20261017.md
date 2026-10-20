# A8 controlled diagnostic suite agents

Rule registration date: 2026-10-17. This document is committed before any A8
model call or outcome inspection. A8 is a post-review diagnostic and does not
modify any frozen method, prompt, main-table result, or prior diagnostic result.
WebCraft is read-only. The only permitted model is the configured `glm-5.2`
gateway. Every successful response must report server model identifier exactly
`glm-5.2`; observing any other or second identifier stops the affected run.
Credentials and endpoints remain environment-only and are never logged or
committed. The optional Qwen run is excluded unless separately authorized.

## Frozen inputs and instances

WebCraft commit observed at registration is
`a18d643f8fcc2b912e78a014b14346833fb0384f`. The authoritative W7a freeze is
`config/ext_v2/freeze_manifest.json`; its CDS material manifest is
`cds_materials/cds_manifest.json` with SHA-256
`0c17870198d1fbbaeaedbb3a3b2ba6ce87b119f13ec8dff01d44a84b6a511838`.
Before each shard, all files named by that freeze manifest are hash-checked.
Dirty, unrelated WebCraft files do not enter the run.

The complete fixed instance list is:

```text
cds_0001_extv2_001_1_16_5_vs_1_21_11
cds_0002_extv2_001_1_21_11_vs_1_16_5
cds_0003_extv2_001_1_19_vs_1_21_11
cds_0004_extv2_001_1_21_11_vs_1_19
cds_0005_extv2_002_1_16_5_vs_1_21_11
cds_0006_extv2_002_1_21_11_vs_1_16_5
cds_0007_extv2_002_1_19_vs_1_21_11
cds_0008_extv2_002_1_21_11_vs_1_19
cds_0009_extv2_003_1_16_5_vs_1_19
cds_0010_extv2_003_1_19_vs_1_16_5
cds_0011_extv2_003_1_16_5_vs_1_21_11
cds_0012_extv2_003_1_21_11_vs_1_16_5
cds_0013_extv2_004_1_16_5_vs_1_19
cds_0014_extv2_004_1_19_vs_1_16_5
cds_0015_extv2_004_1_16_5_vs_1_21_11
cds_0016_extv2_004_1_21_11_vs_1_16_5
cds_0017_extv2_005_1_16_5_vs_1_21_11
cds_0018_extv2_005_1_21_11_vs_1_16_5
cds_0019_extv2_005_1_19_vs_1_21_11
cds_0020_extv2_005_1_21_11_vs_1_19
cds_0021_extv2_006_1_16_5_vs_1_21_11
cds_0022_extv2_006_1_21_11_vs_1_16_5
cds_0023_extv2_006_1_19_vs_1_21_11
cds_0024_extv2_006_1_21_11_vs_1_19
cds_0025_extv2_007_1_16_5_vs_1_21_11
cds_0026_extv2_007_1_21_11_vs_1_16_5
cds_0027_extv2_007_1_19_vs_1_21_11
cds_0028_extv2_007_1_21_11_vs_1_19
cds_0029_extv2_008_1_16_5_vs_1_21_11
cds_0030_extv2_008_1_21_11_vs_1_16_5
cds_0031_extv2_008_1_19_vs_1_21_11
cds_0032_extv2_008_1_21_11_vs_1_19
cds_0033_extv2_009_1_16_5_vs_1_21_11
cds_0034_extv2_009_1_21_11_vs_1_16_5
cds_0035_extv2_009_1_19_vs_1_21_11
cds_0036_extv2_009_1_21_11_vs_1_19
cds_0037_extv2_010_1_16_5_vs_1_19
cds_0038_extv2_010_1_19_vs_1_16_5
cds_0039_extv2_010_1_16_5_vs_1_21_11
cds_0040_extv2_010_1_21_11_vs_1_16_5
cds_0041_extv2_011_1_16_5_vs_1_21_11
cds_0042_extv2_011_1_21_11_vs_1_16_5
cds_0043_extv2_011_1_19_vs_1_21_11
cds_0044_extv2_011_1_21_11_vs_1_19
cds_0045_extv2_012_1_16_5_vs_1_21_11
cds_0046_extv2_012_1_21_11_vs_1_16_5
cds_0047_extv2_012_1_19_vs_1_21_11
cds_0048_extv2_012_1_21_11_vs_1_19
cds_0049_extv2_013_1_16_5_vs_1_21_11
cds_0050_extv2_013_1_21_11_vs_1_16_5
cds_0051_extv2_013_1_19_vs_1_21_11
cds_0052_extv2_013_1_21_11_vs_1_19
```

The material manifest fixes task, target world, comparison world, visible-file
hash, evaluator-file hash, and focal candidate count for every ID. No instance
may be replaced or removed for model behavior.

## Matrix and public information

Run ReAct on `K`, `K+S`, `S`, `empty`, and `K+N`; run Direct LLM on `K`,
`K+S`, and `empty`, for 416 primary episodes total. Material condition `empty`
is reported as `empty` in machine output and as `empty`/`null` in prose. Seed is
`20261017`.

Each episode receives only its selected visible `cds_materials` rows, whose
fields are `mechanism fact`, `source`, and `timestamp`. They are placed in the
initial observation as `diagnostic_mechanism_facts`. Search calls execute the
public simulator method for logging, but their returned rows are deterministic
lexical matches from that same condition's visible rows only. Evaluator files,
roles, truth labels, target/comparison version names, candidate-version lists,
oracle plans, and private assertions never enter model input.

The existing Direct/ReAct system prompts and action loop are unchanged. The
limits are 90 semantic-action cost and 40 action-selection model calls per
episode, temperature 0, maximum 4096 completion tokens, JSON-object mode, and
disabled thinking. Full message arrays and responses are saved for every call
to audit evidence injection and truncation. Network/service errors may retry
the byte-identical request at most twice; model refusals or malformed outputs
retain the existing terminal treatment.

## Boundary question

After the episode terminal, make exactly one additional model call. It does not
change the environment, terminal, 90-action budget, or 40-call action-loop
limit, but is included in total calls/tokens/cost. The model receives the task,
its complete observed episode trace, and the frozen visible
`boundary_question` candidates in their existing shuffled order. The exact
instruction is:

```text
For each candidate mechanism fact below, judge whether it is valid in the current world.
Use only the task evidence and tool observations from this episode.
Return exactly one JSON object with this schema:
{"judgments":[{"index":1,"label":"在当前世界有效|无效|不确定"}]}
Include every candidate index exactly once and no other fields.
```

Invalid JSON, duplicate/missing indices, or an out-of-vocabulary label scores
every unparseable or missing item as `不确定`; no repair call is made. BIA is
correct answered (`有效` or `无效`) items divided by all answered non-uncertain
items; uncertain rate uses all candidate items. If no item is answered
non-uncertain, BIA is not applicable. The newest-timestamp heuristic marks all
candidates at the maximum candidate timestamp valid and all older candidates
invalid. The majority-class baseline predicts, for every candidate in an
instance, the more frequent gold class within that instance; ties predict
invalid. Both baselines abstain on no item. BIA is also stratified by whether
the action trace contains a discriminative probe under the existing A5/A6
observable-action definition.

## Outcomes and comparisons

Report SR, feasible-only SR, overconservatism among feasible instances,
terminal execution failure, target-world agreement of the first focal
conflict decision, BIA, uncertainty, stale-specific query/action contact,
the zero-call query-follow-up class below, v3-d propagation and conditional
propagation, semantic actions, action-selection calls, boundary calls, and
tokens. Existing scorers and terminal priority are reused without alteration.

Primary paired contrasts are `K+S - K+N`, `S - K`, `empty - K`, and
`S - empty`. Point differences average instances within each independent
mechanism family first. Percentile intervals use 10,000 family-cluster
bootstrap draws with NumPy seed `20261017`. Missing infrastructure episodes are
excluded from metrics and listed, never scored as cognitive failures.

The gate is fixed: if ReAct K feasible-task SR is below 0.6, conclude that CDS
remains limited by basic capability; report without expanding the matrix,
changing tasks, or changing any method or prompt.

## Zero-call query-follow-up classification

Before A8 model calls, rescore all query-type stale-specific contacts in the
first-run 110-instance logs for Direct, ReAct, Reflexion, ADAM, and XENON.
Query-type means an executed `check_name` or other existing A5 query-only
semantic action that does not change inventory. A query is world-denied only
when its public result contradicts the stale candidate. The first subsequent
action linked to the same focal item/mechanism determines exactly one class:

1. `denied_then_target_valid`: denied, then a target-world-valid route or claim;
2. `denied_then_stale`: denied, then an action requiring the stale claim;
3. `not_denied`: the query result does not contradict the stale claim;
4. `no_related_followup`: denied, with no later related semantic action.

If both later route types occur, the first related action controls. Counts and
proportions are by method, with up to two earliest trace excerpts per observed
class. This is a deterministic reanalysis and makes no new model call.

## Cost estimate

Cost is explicitly an estimate. First aggregate input/output tokens and actual
charged currency from same-gateway claim-cache construction and motivation
study ledgers that contain both token usage and billed cost. Estimate separate
input/output unit prices by least squares through the origin when at least two
non-collinear observations exist; otherwise use aggregate charged currency per
total token and report that limitation. Report sample size, date range,
currency as recorded, fit residual or aggregate uncertainty, and never infer a
currency not present in the ledger. Apply the resulting rate to saved external
agent and A7 tokens and to actual A8 tokens after completion. Also provide an
A8 pre-run range using the A7 per-episode 10th/90th token percentiles. Provider
discounts, cache billing, hidden tokens, and exchange rates remain explicit
sources of uncertainty.
