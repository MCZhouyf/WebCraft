# External-agent offline rescore, 2026-10-10

Post-review measurement correction, not a new confirmatory experiment. The
author requested an audit of the completed 550 external and 770 frozen
comparison episodes. No model calls, simulator runs, method changes, parameter
changes, task selection or replacement are authorized by this rescore.
Original files and originally reported numbers remain intact.

## Recoverable measurements

Use frozen task feasibility, oracle plan/cost metadata, final inventories,
submissions, schema-v2 costs and complete public action logs. Check all 110 keys
for each method, and all task/world labels against the gold metadata.
Report overall SR separately from feasible-task completion and correct
infeasibility detection. Always-Abstain is an analytic baseline, not a run.
False abstention is divided by the number of gold-feasible instances.

Terminal precedence: observed goal with affirmative submission; correct
infeasibility submission; false infeasibility submission; observed call/action
budget stop; last semantic action failed; otherwise plan_not_found. The last
two are *derived terminal categories*, not unrecorded internal planner reasons.
Report goal attainment without affirmative submission separately. Report
any-action-failure incidence and failed/attempted semantic actions separately.

Probe labels on free search/inventory calls are retained in a legacy-labelled
column, but world-probe counts use mine/craft_grid/smelt/check_name only.
Recompute each charge under the frozen cost model, including failed actions.
Costs remain in semantic units, tokens and calls; unknown currency is not zero.

## Behavioral HTR identifiability

The old external scorer has a circular dependence update: an empty depended
set never becomes nonempty, so every DV action is labelled a probe. The frozen
comparison exporter also hardcodes behavioral_HTR_commit=0. Neither is valid
evidence of an absence of stale commitments.

Do NOT repair this by substituting intent. The external runner retains no
complete decision/plan-dependency record, and overwrites intent with commit for
all external methods except ADAM. Multiple contradictory drop/name assertions
can match the same public action. Public action matching therefore establishes
candidate contact, not the agent's intended assertion or its probe purpose.

Keep three distinct outputs: original/self-reported HTR; independently matched
action-contact candidates; and behavioral commit identifiability. Frozen
used_mechanism_ids can disambiguate claims, but cannot alone establish the
counterfactual probe-vs-plan dependency test. Without an independently recorded
dependency witness, a stale contact has a conservative commit interval [0,1],
not a point estimate. Episodes with no candidate stale contact have [0,0]
within the catalogue. Report unknowns and event-level supporting evidence.
No cross-method formal behavioral ranking can be certified from these logs.

## Statistics and disclosure

Resample the same 22 task IDs jointly, carrying all five cells and both methods
together. Use 10,000 bootstrap draws, seed 20261005, percentile 95% intervals.
Conditional rates are ratios of resampled sums; zero-denominator draws are
excluded and counted. Also report within-world matched/mismatched paired
contrasts. These are exploratory analyses, not new frozen decision gates.

Audit model identifiers, run manifests, budgets, pin hashes, per-call token
usage, context retention and label handling. Five implemented external
adaptations are reported; Voyager is absent, not a zero-performing sixth run.
The old all-zero behavioral metric and old false-abstention denominator are
superseded for interpretation by this audit, without deleting old tables.
