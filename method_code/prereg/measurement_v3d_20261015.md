# Measurement reanalysis v3-d (A5)

Date: 2026-10-15

Status: preregistered zero-call reanalysis. This analysis reads completed
episode logs, existing exports, frozen conflict data, public search records,
and the public claims cache only. It runs no environment episode and no LLM
call, does not modify a frozen method, parameter, or prior result, and treats
WebCraft as read-only. Its columns and tables are supplementary to v3 and do
not replace any v3 number.

## Fixed inputs, common keys, and inference

The external scope is the five first-run methods (`direct_llm`, `react`,
`reflexion`, `adam`, and `xenon`) on the registered common 110 keys: 22
conflict tasks in each of five single-world cells. The two gold-knowledge
controls are the completed Direct and ReAct oracle-knowledge runs on the same
keys. Pipeline rows use the seed-deduplicated all-public records on exactly
those keys. The existing 20261006 pipeline seed is retained after the saved
equality check; neither seed is averaged or treated as an independent sample.

All paired intervals use 10,000 task-cluster percentile bootstrap draws, seed
20261015. A draw resamples the 22 task IDs jointly across every registered
cell and compared method. The reported interval is the 2.5/97.5 percentile
interval. Conditional-rate draws with a zero denominator are omitted and
their count is recorded. The fixed primary paper wording is **discriminative
stale contact**, not causation or propagation.

Semantic actions are `mine`, `craft_grid`, `smelt`, and `check_name`. A stale
contact and its discriminative status are exactly those defined in
`measurement_v3_20261014.md`; no new staleness matching is introduced here.
`check_name` is an inquiry action. All other semantic stale contacts are
action contacts. In particular, `mine` remains discriminative only if the
A3 downstream-reference condition holds; its inventory-changing nature never
overrides that requirement.

## 1. v3-d external propagation

This is a registered decomposition of v3 commitment, not a replacement for
the primary contact metric. Consider only a world-denied, discriminative stale
action. Its v3-d lower-bound propagation event requires one of the following
positive witnesses.

1. A later semantic action references a stale predicted product and the
   reference consumes the FIFO inventory lot produced by the stale action, or
   the referenced item is absent from the immediately preceding public
   inventory snapshot. A same-named item from initial inventory or another
   producing action is not a witness.
2. The same stale action is repeated after mismatch and that repeated action is
   discriminative. A repeat of a non-discriminative action is tabulated as
   `repeat_nondiscriminative`, never as v3-d propagation.
3. The episode has v3's affirmative-submit witness after this action and the
   action is discriminative.

The episode lower bound is one iff it contains such an event. The upper bound
is one iff it contains such an event or a world-denied discriminative stale
action with v3 label `undetermined`. The conditional propagation rate divides
v3-d events by world-denied discriminative stale actions. For every external
method and each oracle-knowledge control, report lower/upper bounds, this
conditional rate, matched-three-cells versus mismatched-two-cells rates, and
the mismatched-minus-matched difference. External-method differences versus
Naive use the same task-paired bootstrap.

When a repeat follows an action which failed because a whitelist missing-input,
missing-tool, missing-fuel, or tool-too-weak error is corroborated by the
public pre-action inventory, it is additionally tabulated as a
`prerequisite_missing_loop`. It remains a v3-d event only if it independently
satisfies the discriminative repeat rule above. Trigger categories are not
mutually exclusive; rows preserve every applicable trigger.

## 2. Discriminative contact decomposition

For the external five and every available pipeline method on the common 110
keys, report episode-level total discriminative-contact rate, inquiry-contact
rate, and action-contact rate; semantic actions per episode; and each contact
count per 100 semantic actions. An episode can contribute to both inquiry and
action contact. Report each rate's difference versus Naive with the registered
paired interval.

The fixed condition split is matched (the three matched cells),
mismatched-old-to-new, and mismatched-new-to-old. For each contact type report
the three rates and each mismatched-minus-matched paired interval. These are
descriptive decompositions of the fixed primary metric, not substitutes for it.

## 3. Source attribution

For every discriminative stale external action, scan only search rows returned
before that action in the same episode. Under the strict rule it is
`corpus_induced` iff a returned chunk's public-cache claim matches the stale
mechanism ID and stale payload. It is otherwise `not_detected_corpus_source`.
Under the loose rule it is `possibly_corpus_induced` iff returned public text
contains a normalized subject display name or identifier and its fixed family
keyword (`drop`, `recipe`, `smelting`, or `name`); otherwise it is
`not_detected_loose_source`. The same rules are applied to the two
oracle-knowledge controls. Pipeline rows use their saved evidence bundles;
absent retrieval is explicitly `parameter_prior_or_no_retrieval`.

Claims-cache recall is incomplete. Therefore either not-detected category
means only that this audit did not detect a corpus source; it does not mean
that the action came from a model prior. This limitation is reported with the
tables and is not repaired by reading raw corpus files or hidden annotations.

## 4. Behavior-only distinguishability supplement

Use the completed A4 unified-110 behavior-only pair table unchanged. Report
its fixed counts both with analytic Always-Abstain included and after simply
filtering all pairs containing Always-Abstain. No pairwise statistic is
recomputed, and no threshold changes. The values are: total method pairs,
SR-indistinguishable pairs, behavior-separated pairs among them, and the
unique separated method pairs.

## Outputs and immutability

All output is written to `reports/main/post_review/measurement_v3d/`:

- `propagation_v3d.csv` plus event, condition, and bootstrap tables;
- `discriminative_contact_v3d.csv` plus condition and bootstrap tables;
- `stale_action_provenance.csv` plus summary;
- behavior-only distinguishability summary with and without Always-Abstain;
- `REPORT.md` and `provenance.json` recording input hashes and zero-call use.

Rows are deterministic by setting, reading mode, method, condition, world,
task ID, seed, and action index. Missing data, ambiguous FIFO matches, absent
cache entries, and undefined ratios are explicit. These definitions, inputs,
and missing-data rules must not be changed after this commit in response to a
result.
