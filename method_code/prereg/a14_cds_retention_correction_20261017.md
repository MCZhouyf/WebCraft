# A14 calculation-before-results correction and continuation

Author confirmation: use each frozen material's actual condition-specific fact
list. Complete A1-A3 and B0-B2 in this round without further specification stops.
This separate correction precedes all new retention, prefix and outcome results.
Original registration 4764388 and stop record 04eb090 remain unchanged.

## Authorized corrections

1. Every condition, including empty, uses its actual frozen three-field fact
   list as the expected multiset. Only episodes with an actually empty list
   receive NA retention fractions and the vacuous P=R convention. This also
   corrects the A3 denominator: exclude zero-list episodes, not all empty-label
   episodes, when reporting knowledge-bearing boundary calls. The observed
   material-only distribution (44 zero-list and 8 nonzero-list instances under
   empty) is already documented; no retention/outcome calculation preceded this.
2. B0 is an interpretation gate, not a calculation gate: B1/B2 are computed
   even if strict information equivalence fails, labelled related evidence and
   not replacement causal evidence. Show public-material equality separately
   from compiler information and role-metadata differences.
3. Further inconsistencies must be recorded. Continue unaffected items. Use
   the conservative conventions below, or mark an affected item NOT_REPORTED;
   do not redefine outcomes, alter frozen methods, or run new model calls.

## Conservative implementation details fixed before calculation

- Saved full action prompts are the authoritative actual context. Independently
  rebuild their observation/trace from saved responses and the complete boundary
  API trace, and crosscheck raw action-log ordering/results. Request alignment
  failures are reported per episode; retention from saved messages remains
  reportable, but prefix action outcomes needing ambiguous alignment are NA.
  Pair/level tables disclose the resulting exact common-key denominator.
- Use the frozen pure response parser/validator and public-system-prompt
  functions by extracting their AST definitions, not importing/executing an
  agent loop, simulator or gateway. No network or environment action is needed.
- For per-fact semantic traces, a trace entry containing only inventory, with
  no action/thought/mechanism text, is not positive evidence. Exact fact text
  missing its source/time is a trace. Structured craft/smelt/mine/name entries
  count only when subject plus a corresponding other ingredient/result (or
  subject plus explicit name check) occur in the same entry. This is the
  original preregistered conservative lexical/structural proxy, not an LLM
  semantic judgment.
- Prefix success follows observed inventory flows and a model-origin affirmative
  submit within P. Initial inventory may already meet the goal; affirmative
  submission is still required. Track initial and later inventory snapshots,
  consumed and produced counts. Prefix goal attainment without submit is a
  secondary column. Auto-submits at timeout/parse termination are not assigned
  to a model request. Censored episodes are not inferred to eventually fail.
- Reuse A8's saved per-action discriminative labels for A prefix-timing results;
  retain full-trajectory referenced-at evidence and separately disclose any
  selected contact whose reference falls after P. This is not a newly causal
  online classifier. Invalid or missing annotation-event alignment affects that
  episode's contact metric only; do not assume missing labels mean no contact.
- For B, re-score saved raw actions using the same frozen pure catalog matcher,
  classify_actions_v3 and intrinsic_discriminative as A8 (intrinsic OR later
  predicted-product reference). Catalog stale matches use the target world's
  truth vector; do not use runner-created synthetic truth vectors as physical
  truth. Retain original catalog/used-ID contact as a separate legacy column.
  This does not repair any known frozen matcher limitations or alter main data.
- B's valid-task success comes from frozen true_feasible and metrics.SR. Verify
  feasibility against the frozen task file; conflicts are flagged and that
  metric is NA rather than choosing the more favourable label.
- B equal-method pooling averages all ten methods inside each directed instance
  before bootstrapping 52 instance keys (30 for feasible-only statistics).
  Do not count ten methods or duplicated seeds as independent replicates.
- A uses family-cluster pooled-ratio bootstrap; B uses instance-paired bootstrap,
  each 10,000 draws, seed 20261017, percentile 95% intervals. Preserve A8 frozen
  CIs verbatim in frozen_* columns; explain the original equal-family-mean CI
  versus new pooled-ratio CI. A's sparse rules remain: >=10 instances, >=5
  families and >=5 discordant binary pairs for a difference, or >=5 positives
  and >=5 negatives for a binary level. For descriptive continuous P/R family
  differences require >=10 pairs and >=5 families. B contact-difference CIs
  additionally require >=5 positive-contact episodes across the two cells;
  B level/SR CIs require >=10 eligible instances. Always report counts and
  sparse reasons, never fabricate precision for a suppressed CI.
- Cross-products cover conditions K/K+S/S/empty/K+N, Direct/ReAct, request
  lengths 1..40 including unsupported cells and zero counts. If an actual
  length exceeds 40, add its explicit row and flag the protocol discrepancy;
  do not truncate. Any knowledge recovery after the first incomplete request
  does not extend P. Empty expected lists are excluded from retention-weighted
  fractions but included and flagged in prefix descriptions.
- B0 audit compares the actual multiset of text/source/timestamp, not visible
  file names alone. Record deterministic raw_claim's private-derived layout,
  expanded alternatives and quantity, plus page_title derived from role; these
  can break strict same-input equivalence even with identical visible material.
  Never silently identify the deterministic original used-ID metric with the
  newly re-scored discriminative contact metric.

## Deliverables and preservation

New files under reports/main/post_review/a14_cds_retention/final/; the previous
stop report stays in place. Include request/fact/episode data, zero-cell
cross-products, A2 three-scope estimates, boundary coverage (no new BIA), B0
field-level evidence, B1 per-method/pooled estimates, and B2 parallel tables.
Hash inputs before/after, list exceptions, run focused tests, commit locally.
WebCraft, frozen methods/results, original registration, audit keys and
server.lock are unchanged; zero LLM calls and zero new episodes; no remote push.
