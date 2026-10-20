# W10 replay root causes and attribution sensitivity (W11)

Registered before new calculations. Author request follows W10 result cec4835;
these are post-review diagnostics, not new experimental outcomes. Zero LLM
calls, zero new episodes, no network/model endpoint access, no remote push.
All frozen sources, exports, manuscript and releases remain read-only. New
artifacts go to reports/paperA/w11_attribution/. Preserve unrelated changes.

## Coordination and resources

No server.lock existed in the workspace, ARBITER, /tmp or common lock locations
at entry. Hold the new shared /root/autodl-tmp/server.lock using a nonblocking
exclusive flock throughout analysis. Do not operate any non-task process.
CPU only, CUDA disabled, nice 19, one BLAS/CBC worker. Planned memory 2 GiB plus
8 GiB headroom; check available host/cgroup memory before substantive phases.
Use Python 3.10.8 and PYTHONHASHSEED=0 except explicitly labelled source-level
hash-seed diagnostics. Never record environment values, credentials or endpoints.

## Two reasoning passes and source investigation

Pass 1 records competing hypotheses before classification: unequal inputs/code,
equivalent operators, unordered set/dict or filesystem traversal, solver
alternative optima/row ordering, floating-point effects and hash-seed coverage.
Pass 2 attempts to falsify the leading explanation using frozen evidence,
source/line hashes, executable pure-function diagnostics and trace differences.
Existence of a risky line alone does not establish a nine-case cause.

Inspect the five identical-action pla_010 cases first, then the four cost cases.
Quote actual selection code/sorting keys and identify the stage that chooses
the logged operator versus the stage that schedules an action. Compare frozen
method source and release source, evidence and runtime provenance. Captured
state input reconstruction, compilation, plan solving, LP serialization and
bounded isolated source-function tests are permitted; executing a world action
or calling the full agent/episode runner is not. Hash-seed contrasts are pure
function tests, not episode reruns. Do not alter source to make a hypothesis fit.

Report exactly one category per case: (1) established unfixed tie-break with
exact source; (2) established other deterministic cause explaining both runs;
(3) not established, with exclusions and specific missing evidence. Separate
reproduced causal mechanisms from unrecorded historical process state. If
input/candidate reconstruction fails a required frozen-baseline assertion,
stop and report that failure; do not relax the definition or select a new rule.

## Attribution-sensitivity population and definition

Population: exactly 11,220 frozen seed-deduplicated main records = 10 methods x
3 reading modes x 374 task/configuration instances. Do not pool reading modes
for rates/comparisons. Preserve exact frozen evidence per condition/task/mode.

For each logged semantic action, reconstruct its eligible operator pool from
frozen public evidence/prior or the frozen Oracle operator pool, including
recorded filtering/feedback state. An action-equivalent pair must yield the
same method, canonical public parameters, output ID/count, semantic cost and
required resources for the logged action; distinct catalogue IDs alone are not
enough. For the primary tie-based sensitivity require equal registered planning
objective coefficient (absolute tolerance 1e-12) and feasibility for the captured
state. Record unequal/unknown-objective aliases separately, not as proven ties.
No alternative future plan or changed behavior is imputed.

For every tied eligible attribution compute the existing W9 specific-action
predicate on the unchanged frozen event stream using the frozen classifier.
An affected episode is one for which attribution choice can change the OR of
specific-action labels, or the OR of specific-query/action labels; report both.
Retain unaffected positive actions when forming a lower bound. Upper bound is
the OR over eligible alternatives, lower bound the OR of unavoidable positives.
These are fixed-behavior attribution-sensitivity bounds, not new performance
results. Report affected counts/proportions and method x mode x condition/world
distribution, including zero cells. If faithful pool reconstruction is not
possible, do not fabricate full-population bounds: stop with the missing inputs.

## Conditional deterministic repair

Only after conclusion (1), define a new, non-frozen canonical selector for
equal-objective action-equivalent operators. Full ascending key is:
(method, output, output_count, action_cost, canonical resolved public params,
canonical sorted requires, canonical input_options, mechanism_id), with JSON
sort_keys=True and compact separators for canonical structured values.
It must not inspect catalogue membership, truth labels, world identity, method
performance or reported values. Remove duplicate identical keys first. This is
a new annotation/selection specification, not a modification of frozen policy
or a claim that sorting alone makes all MILP optima unique. Re-annotate unchanged
logs only where the registered equivalence applies; compare all rate/cell
changes to the frozen version. No full-policy replay or new world observation.

## Full-matrix discriminative-contact table and intervals

Use the frozen W9 predicate: specific_action, specific_query and their OR.
Validate recomputed original values against every row of W9 main_comparison.csv
before making alternative-attribution results. Tables separate each reading
mode and the eleven exact condition/world cells, plus a within-mode overall
summary (374 unique instances per method). Include exact numerator/denominator.
Never mix denominators from the common-110 subset with the full matrix.

For all 45 pairs of the ten executable methods, calculate rate differences
(method B minus method A) and 95% percentile intervals by paired task bootstrap:
aggregate paired configuration instances within each task first; sample the
34 task IDs 10,000 times, fixed seed 20261017. Use the same resampling indices
for all methods/metrics within a scope. State the task-averaged estimand (equal
configuration counts within a scope); no significance claims from zero events.
Provide both specific_action and query/action OR, exact discordant counts and
event scarcity flags (0: no events, 1--4: sparse). Modes and scopes stay separate.

## Stop/report policy and provenance

Unexpected source/input changes, missing evidence, a failed baseline invariant,
population/key/denominator mismatch or resource/lock failure means stop,
preserve diagnostics and report; no silent retries with different rules.
Conclusion (3) itself is an allowed root-cause outcome, not permission to
invent candidate pools. Do not modify the rules after seeing results.
Outputs include source quotes, two-pass hypothesis/exclusion ledger, case roots,
candidate/action evidence, sensitivity counts/bounds (only if reconstructible),
conditional repair, full-matrix counts/paired intervals and SHA-256 inventories.
Commit this registration before implementation/calculation, then commit only
new W11 paths and report the result hash and any unmet stage explicitly.
