# Review v13 A6 audit

Date: 2026-10-15

Status: preregistered zero-call audit. All rules below were formed at
2026-10-15T06:22:00Z before this audit reads a result. The audit reads only
saved episode/action/prompt logs, previously exported tables, frozen public
corpus cache rows, and evaluator exports. It starts no episode, makes no LLM
call, uses no GPU, leaves WebCraft read-only, changes no frozen method or
parameter, and does not replace a previously reported number.

## 1. Observable stale-specific action

An executed action is a *stale-specific action* iff it occurs in at least one
legal plan licensed by a catalogued stale assertion and in no legal plan that
uses only target-world-valid assertions for the same normalized subject and
mechanism family. The fixed observable implementation is A3's saved
`discriminative` field: its intrinsic branch compares the action against every
target-world valid candidate, and its downstream branch permits a shared drop
`mine` action only when a later action references the stale predicted product.
Thus old recipe grids, old name identifiers, and old-only inputs qualify;
shared drop mining without a downstream stale-product reference does not.

The audit records every difference between this implementation and the stated
plan definition. The sole pre-registered possible difference is that A3 uses
candidate assertion/action matching rather than enumerating all legal plans;
the report must say whether the candidate universe is extensionally identical
on the saved records. It may not declare an unobserved alternative plan absent.

`check_name` is query contact; `mine`, `craft_grid`, and `smelt` are execution
contact. A round may be in both classes. All class counts report 110 total
episodes and the frozen feasible denominator of 72 separately. Differences
against Pipeline Naive use 10,000 task-cluster percentile bootstrap draws,
seed 20261014, matching `table_5_effects_v3`; the resampling unit is task ID
with all five registered cells carried jointly.

## 2. Rebuttal and propagation denominators

A primary rebuttal episode contains a stale-specific action whose predicted
outcome is contradicted by public world feedback. A secondary rebuttal episode
contains any catalogued stale-mechanism action whose prediction is contradicted.
For each method, report episode counts, v3-d propagation lower/upper episode
counts, unconditional rate (lower propagation / 110), and conditional rate
(lower propagation / primary-rebuttal episodes). A zero denominator is
`not_applicable`, never zero. Mechanism-family opportunity counts are action
counts. Conditional percentages are explicitly not ranking statistics.

## 3. Causal witnesses and post-hoc iii' check

Each saved v3-d positive event is exported with the stale action, public
feedback, later witness action, terminal, and all applicable trigger rules.
The audit searches all public logs for: stale name/recipe query, contradictory
feedback, later different action advancing the target, and a success or
correct-infeasible terminal. These are `query_corrected_route` cases.

If any such case is marked propagated solely or partly through v3-d iii', a
parallel post-hoc iii'-narrow column is computed. It retains iii' only when
the affirmative terminal's stated feasibility/completion would require the
stale assertion's predicted product under FIFO public inventory flow. The
original v3-d result remains unchanged. Any trajectory lacking a positive
causal witness stays `undetermined`.

## 4. Blinded scoring sample

The audit sheet is stratified with RNG seed 20261015. S1 includes every v3-d
positive event. S2 samples up to 30 query and 30 execution stale-specific
contacts. S3 samples up to 40 non-specific stale-mechanism contacts. S4
samples up to 20 no-contact abstentions. Required motif quotas are checked in
this priority order: query-corrected-route, contradicted-repeat, repeated
shared-drop mining, and direct-abstention; each target is five rows where the
saved corpus contains that motif. A shortfall is reported, never filled with
another motif. `audit_sheet.csv` hides labels and `audit_key.csv` stores them.
The sheet contains only public action/result text and pre-action returned
search snippets.

## 5. Cost and gold-control audit

Costs use saved public fields only: semantic action count, summed semantic
primary cost, search call count, and LLM call count. Pipeline LLM calls are
zero by definition. Probe is reported solely as an existing intent-label
count. Pipeline matrix costs cover every completed method/episode in all three
reading modes; common-110 costs cover all methods. The two preregistered
paired comparisons are Naive/Verify-All and Parametric/Probe-then-Parametric.

Gold-control source audit treats an oracle-knowledge result row as sufficient
only when its public search return covers every required oracle-path mechanism,
the returned text appears in the saved prompt before action, and its saved
oracle cost fits the fixed 90-action/40-call limits. Gold source is classified
only from saved public path/provenance fields as template gold library, truth
file, or other/undetermined; no hidden source is opened.

## 6. Statistics and IAA

The behavior table lists every saved unified-110 pair whose SR paired-bootstrap
interval includes zero, both with and without analytic Always-Abstain. It
reports saved effect values, raw p-values, Holm adjustment across that fixed
table, and the registered test family. No new significance rule is created.
IAA uses saved response logs. For each method/subset, report true/false
composition, the majority-class baseline, coverage, conditional accuracy minus
baseline, Wilson interval for accuracy, and task-cluster bootstrap interval for
the difference. Missing responses reduce coverage only and never become wrong
answers.

## Outputs

All output is written to `reports/main/post_review/review_v13_audit/`:
`contact_definition.md`, `contact_split.csv`, `rebuttal_propagation.csv`,
`propagation_event_audit.csv`, `audit_sheet.csv`, `audit_key.csv`,
`real_cost.csv`, `gold_control_audit.csv`, `behavior_pairwise.csv`,
`iaa_v13.csv`, `REPORT.md`, and provenance. A synthetic calibration-only test
file is `tests/test_scorer_review_cases.py`. Rows are deterministically sorted;
undefined and unavailable values are explicit. No rule, input, or missing-data
treatment may change after this commit in response to a result.
