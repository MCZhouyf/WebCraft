# W12: cross-method public-action identity and label functionality

Registered after W11 (050a142), before population calculations. This is a
post-review observational reanalysis, not a new experiment. Zero LLM calls,
zero new episodes, no network, no remote push. Frozen inputs stay read-only.
Hold the existing shared server.lock exclusively; protect all other processes.
CPU only, CUDA disabled, nice 19, one worker, 2 GiB planned memory plus 8 GiB
available headroom. Write only new W12 scripts, tests and report paths.

## Population and completeness gate

Primary source: release/package/assets/frozen/main.jsonl.gz, the existing
11,220 seed-deduplicated records: ten executable methods x three modes x
374 identical task/configuration keys. No analytic Always-Abstain row and no
external-agent subset. Compare only within the same (reading_mode, condition,
world, task_id); 45 unordered method pairs per mode, denominator 374 each.
Never pool percentages across reading modes. Preserve all eleven exact
condition/world cells. Also group the three matched cells as matched (102),
the two mismatches separately (34 each), mixed (102), polluted (102), and
the union of the two mismatches (68); union rows are explicitly overlapping.

Join W11 episode_attribution_sensitivity.csv on all five keys, require one
row per record and the same complete population. Check original labels against
W9 main_comparison.csv. Require eight recognized public API method names,
dict params, contiguous event sequence numbers, and stored schema-v2 events.
Require equal per-method cell sets and all 374 keys in each mode. The entire
stored events array is used; an inventory call after submit is not truncated.
Episodes need not end with submit: an exhausted/aborted episode can be a
complete stored trace. Report missing-submit counts separately, not exclusions.
Any missing keys, malformed/truncated event structure, unsupported method,
baseline-label discrepancy, input hash change, resource or lock failure stops
the audit without changing the rules. Do not silently drop records or invent
denominators. Document limits of archive completeness and source instrumentation.

## Three frozen identity projections

Use all eight logged public methods: inventory, mine, craft_grid, smelt,
check_name, report_version, submit, search. Include submit with its complete
stored params/result payload (the submitted result inside params), even when
its verdict differs. Include inventory and search calls if present. No
semantic-action-only replacement for this primary definition.

(a) Strict: each action is {method: event.method, params: event.params};
serialize the ordered array as UTF-8 JSON with sort_keys=True,
ensure_ascii=False, separators=(',', ':'), allow_nan=False. Compare actual
serialized bytes, not hashes alone. Do not normalize values or strip any
parameter. Object-key order and archive whitespace are serialization artifacts,
so this defines reproducible projection bytes, NOT original HTTP/wire bytes
(which are not retained). report_version exposes only the logged guess_sha256;
do not claim recovery of the unhashed guess. Raw intent is not a parameter.

(b) Normalized: same projection, then apply only source-supported aliases:
in a valid nine-cell flat craft_grid layout replace a cell {name: string,
count: 1} (or omitted count) by its name, and strip an optional minecraft:
prefix from valid cell item strings. Preserve the nine positions; no rotation,
mirroring, trimming, sorting ingredients, nested-grid flattening or remapping
resource-node IDs. Strip the same namespace prefix on string item identifiers
in smelt.input, smelt.fuel, check_name.name. Preserve invalid cells/values and
extra cell fields rather than silently erasing information. Normalize numeric
cell count 1.0 to 1 only when this valid cell is replaced: the executor accepts
it via ==1. Other numeric parameters retain type/value, especially smelt.n
(1.0 and True are NOT valid integer quantities). Do not normalize strings
"1", invent quantities, or change submit/search/report_version payloads.
JSON serialization is otherwise identical to (a).

(c) Method-name sequence: ordered list of all logged method names; params may
differ. Strict equality must imply normalized equality, which must imply name
equality. No prefix or subsequence counts as full equality.

For every pair and all three definitions, store longest common prefix (LCP)
as a number of public calls. Report exact-length histograms, min/median/max,
and mean LCP divided by the shorter trace length (empty/empty = 1). A trace
ending earlier is not a full match unless both lengths are equal. Keep full
per-cell numerators/denominators and differences; no new significance claims.

## Labels and W11 membership

Plan-level adoption is frozen metrics.HTR_commit, a declared plan-source
annotation, not a claim about hidden intention or an independent consequence
measure. Compare each pair's binary labels on identical cells separately for
each of the three projections. Also compare frozen W11 specific_action and
specific_contact (query OR action), retaining the original catalogue attribution,
not the W11 canonical repair. Denominator = identical cells for that pair/mode/
scope/projection; also give disagreements divided by all paired cells.
Zero identical cells means an undefined conditional rate, not zero.

W11-sensitive episode = action_attribution_sensitive OR
contact_attribution_sensitive == 1, expected 39 unique records. Label pair
strata as neither / A_only / B_only / both and also any-sensitive. Pair
incidences are not unique episodes; report both where applicable. An observed
label disagreement outside the W11 set is a result, not grounds to stop or
change the selection. The W11 set concerns within-method tied attribution;
cross-method source/role differences need not belong to that set.

## Function-of-trajectory falsification and two reasoning passes

Pass 1 distinguishes four hypotheses: same commands with different declared
probe intent; same commands with different public observations; same public
history with different private plan/operator provenance; genuinely different
actions. Do not infer method equivalence from equal commands.

Supplement strict commands with (i) per-event intent, absent = null; and (ii)
intent plus recorded public result, success, cost and item_flow. Remove no
returned fields, except do not add unrelated timestamp/sequence bookkeeping
to this projection. Hash the complete values, including search text if present,
but never export raw retrieved/private page text in new audit artifacts.
Compare stored used_mechanism_ids separately, not as a public input.

Pass 2 tests equality nesting, exact pair/cell totals, hash-vs-byte comparisons,
LCP bounds, W9/W11 label matches, symmetry/identical-group consistency and the
full-history strata of each counterexample. A same-public-history/different-
label counterexample disproves that label being solely a function of this
recorded public projection within the same evaluation context. No collisions
does NOT prove global functionality. Do not call command-only collisions
"identical complete trajectories". Report metadata/attribution differences
without claiming a uniquely established causal explanation from association.

## Outputs and interpretation

Write reports/paperA/w12_trajectory_identity/: full pair-cell CSV, 135-row
overall matrix (45 x 3) with all three numerators and denominator 374,
condition matrices, Naive-focused tables, LCP histograms, conditional label
disagreements with W11 strata, exact identity groups and counterexample
metadata/hashes, completeness/provenance and two-pass report. Report all tied
lowest-equality pairs and the nonidentical remainder, not only favorable pairs.
Describe different behaviors' scale; this is not evidence of method equivalence.
The supplied 80-row audit is stratified/nonrandom; never extrapolate 17/47.
Check the named pla_010 example descriptively, without tuning definitions to
force 38 calls. File/source SHA-256 inventories and separate prereg/result
commits accompany all outputs. If a required gate fails, save diagnostics,
stop and report; no alternate rule or new episode is permitted.
