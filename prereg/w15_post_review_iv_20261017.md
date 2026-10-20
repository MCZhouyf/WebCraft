# W15: zero-call reanalysis package, post-review IV

Registered before any W15 calculations. This work makes no LLM, network, or
new-simulator calls. It reads frozen episode/action exports and writes only
new W15 scripts and `reports/paperA/w15/`. Frozen files are not modified;
ARBITER is read-only; no remote is pushed. `server.lock` is held for the
analysis. Inputs that do not have the fields required by a registered analysis
are reported as `not_reported`, rather than being substituted with another
source or pooled with a different model family.

## Two-pass reasoning and conservative conventions

Pass one fixes the unit and denominator before looking at estimates: one
observed episode per method, condition, task and world; paired contrasts retain
only keys observed in both sides. EXT-v2 uses its independent family as the
cluster; CDS uses its supplied independent family. GLM and Qwen never pool.
Pass two checks that an observed contact has a traceable event and mechanism
identifier. Missing action-level provenance is not inferred from an episode
summary; it is recorded as unavailable. These checks distinguish a lack of
evidence from a zero event.

## 1. Leave-one-family-out

For EXT-v2 Web cells, retain the eleven families with all five single-world
condition cells needed to form matched and the two mismatched directions. For
each method, calculate its within-family mismatch-minus-matched SR and
action-contact difference, then calculate the pooled episode-weighted contrast
after excluding each family in turn. The reported LOO range is the minimum and
maximum over the eleven exclusions. The worst case is the excluded family whose
estimate is closest to zero (ties sort by `family_id`). A contrast crosses zero
when its registered 10,000-draw percentile family-bootstrap 95% interval
contains zero. If any LOO interval crosses zero, list its family ID(s).

EXT methods are frozen `naive_rag`, `direct_llm`, `react`, and `reflexion`.
They are not pooled. Action contact is the suite's recorded action-contact
episode indicator, not an inferred event count.

For CDS, calculate the K+S-minus-K+N action-contact contrast and the
empty-minus-K SR contrast independently for each model/export family. A model
is reportable only when the two conditions cover the same supplied instance
keys and each key has a nonmissing requested metric. Use family-cluster
bootstrap and the same zero-crossing rule. If no action-level rows exist, use
the frozen episode-level discriminative contact indicator and label this
substitution explicitly.

## 2. Contact consequence decomposition

The unit is a positive contact episode, not each individual stale event. The
three categories are mutually exclusive with this priority:

1. **Propagation after denial**: at least one contacted stale event has the
   frozen v3-d propagation label/event.
2. **Stale action executed**: an action-contact event executes an operator
   permitted only by the stale assertion before the target-world feedback
   denies it. This applies only if category 1 is absent.
3. **Abandoned after checking**: a query or action contact has no later action
   that depends on that contacted mechanism, no category 1 event, and no
   category 2 event.

An episode positive for contact which cannot be placed by these traceable
rules is retained as `unresolved`, never forced into a category. The analysis
cross-checks the existing 78-query audit (67 abandoned / 10 executed / 1
propagated) only where exact episode keys overlap; disagreement is reported,
not reconciled by altering either source. Main 110, EXT-v2 180, and CDS
GLM/Qwen sources remain separate and include only logged methods with
traceable event provenance.

## 3. Failure attribution

The unit is a frozen failed pipeline episode with a reproducible key. Apply
the following ordered, mutually exclusive rules using only the W13 203-query
retrieval census, W14 recall-upper callback rows, and Naive's 113 recorded
pre-action abstentions:

1. **initial_retrieval_coverage**: the required target-world mechanism has no
   positive-score corpus counterpart in W13's census.
2. **candidate_compilation**: a positive counterpart exists but W13 records it
   as uncompileable after full recall.
3. **mechanism_inference**: a new compilable candidate is returned by the W14
   recall-upper callback, yet the episode retains the failed terminal outcome
   and makes no executable target-world corrective operator.
4. **planning_utilization**: a target-world executable corrective operator is
   present in the logged candidate/plan trace, but the terminal still fails.

Rows that cannot satisfy one rule, including summaries without an exact query
or episode key, are `unresolved`. The four labels are an accounting of the
available diagnostic evidence; they are not causal claims and do not turn a
callback opportunity into a successful plan.

## 4. Descriptive family tables

For EXT-v2 and CDS, publish family-by-method SR and action-contact numerator,
denominator, and rate. These are descriptive: no inter-family hypothesis test
or aggregate rank is assigned. All random procedures use NumPy PCG64 seed
`20261017`, 10,000 draws, percentile 2.5/97.5 intervals. Results are reported
regardless of direction.
