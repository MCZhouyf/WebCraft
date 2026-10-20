# A14: A8 knowledge retention and deterministic-CDS equivalence gate

Registered before new retention/prefix/outcome calculations. Results in either
direction will be reported; these definitions will not be selected or changed
after seeing them. Existing methods, prompts, frozen results and WebCraft files
are unchanged. Zero LLM calls, zero new episodes, no simulator/agent rerun,
no GPU, no unrelated process changes, no remote push. This offline analysis
does not acquire or alter server.lock, consistent with previous offline audits.
Endpoints and credentials must never enter outputs, code or commits.

## Cohorts and fail-fast checks

A: the original 416 completed A8 records, 52 directed instances, Direct in
K/K+S/empty and ReAct in K/K+S/S/empty/K+N. The author's empty condition is
written empty in logs. Every supported method/condition has exactly 52 keys.
No hypothetical additional 2,600 LLM records are substituted. Require matching
materials, saved prompts, action logs, boundary trace, existing episode metrics
and existing stale-action annotations. Snapshot hashes; reject duplicates,
missing records or unexplained prompt/action alignment differences and stop.
Do not read human-audit scorer keys.

B: the existing WebCraft W7a cds_run/episodes.jsonl, 52 x 5 x 10 = 2,600.
Read only. Do A first, then B0. A failed B0 stops B1/B2; report the evidence
and the missing equivalence, not substitute a different cohort or metric.

## A1: requests, complete retention and semantic traces

An action step is a saved full_prompt whose call_type is external_direct_llm
or external_react, numbered from 1, including invalid-output requests. It is
not an inventory/search/action-event index. Boundary calls are separate.
Total episode length R is the number of these requests (registered max 40).
Also report executed tool-event and semantic-event counts, without using them
as the prefix denominator.

Reconstruct each request with the frozen runner's public task, latest observed
result, and previous response/action observations (last 2 entries for Direct,
last 8 for ReAct). Use the saved boundary API trace and raw action log as the
independent result sequence. Reuse only the frozen pure JSON/action-validation
functions, never its execution loop or a gateway. Require reconstructed payload
and messages to equal saved payload/messages, including failed model outputs,
and record hashes and source indices. Do not truncate prompts to make them fit.

Expected entries are the condition's frozen list of three-field dictionaries:
mechanism fact (exact original text), source and timestamp. Preserve duplicate
entries (multiset accounting, consistent with A11); also report unique-triple
counts separately. Full retention requires an identical three-field dictionary
anywhere in decoded request messages, not only under the initial-injection key.
A search result can restore a fact. Merely seeing candidate/material metadata
outside an actual request does not count. Source/time without text or text
without source/time is not full retention.

Classify each expected entry on each request into exactly one class:
1. full: identical text/source/timestamp triple present, with multiplicities;
2. semantic_trace_only: no full copy, but exact fact text remains in an
   observation/trace string, OR a single observation/trace entry contains the
   mechanism subject plus at least one different ingredient/drop/result ID and
   a mechanism verb (craft/recipe/make/made/smelt/drop/mine/name/exists), OR an
   explicit corresponding tool action plus subject and a corresponding input
   or output in that entry. Names may use subject plus check_name/identifier.
   Match identifiers case-insensitively with underscores/spaces normalized;
   exclude task/initial-inventory-only and generic system-prompt text from this
   proxy. This measures lexical/structural semantic traces, not faithful belief
   retention or human semantic inference. Report the matching reason;
3. no_trace: neither criterion. Trace-only is never full retention.

P is the largest contiguous prefix of requests starting at 1 on which every
expected entry is fully retained. Later recovery does not lengthen P. If the
first request is incomplete, P=0. In empty, expected count is zero: retention
fractions are NA, absence is by design, and P=R only as a vacuous bookkeeping
convention, never evidence that empty retains knowledge better.

Export per-request and per-fact counts; prefix and recovery per episode;
condition x family x R for both families, all five conditions, R=1..40, including
zero cells and unsupported Direct/S, Direct/K+N cells. Summarize P and R by
family and condition, plus Direct/ReAct paired P and R differences on shared
conditions. Episode length and family associations are descriptive, not causal.

## A2: outcomes and paired estimands (frozen before calculation)

Targets: ReAct K+S minus K+N discriminative stale-contact (52 paired instances);
empty minus K feasible-task success separately for Direct and ReAct (30 feasible
instances each); ReAct/K feasible-task success (30 feasible instances).

Three scopes:
1. full: original per-episode indicator and denominator. Copy the already frozen
   effect, denominator and CI into separate frozen_* columns without alteration;
   require reproduced full-episode points to agree before reporting.
2. prefix: contact requires its action was executed from a request numbered <=P.
   Keep A8's frozen per-event discriminative flag, including its recorded later
   reference criterion; also disclose how many selected flags use references
   after P. This is timing-censored contact under the frozen classification,
   not a claim that future dependence was observable at the prefix boundary.
   Prefix feasible success requires both an affirmative model-origin submit and
   enough goal items observed by that submit, all within requests <=P. Follow
   recorded inventory and item_flow, not a rerun. No auto-submit after budget or
   parser termination is assigned to a model request. If the episode has not
   terminated by P, label it censored/not-yet-observed separately; its prefix
   success indicator is zero, not an inferred eventual failure. Also report
   prefix goal attainment without submission as a secondary diagnostic.
3. half_retained: original full-episode outcome restricted to P>=ceil(R/2).
   Paired contrasts require both members satisfy the rule; no one-sided
   denominator substitutions. This is a selected descriptive subset, not a
   causal correction for censoring. Report losses of pairs/feasible instances.

Bootstrap: 10,000 draws, seed 20261017. A resamples independent family clusters
with replacement and pools all eligible instance pairs in each draw; point
estimates are instance-weighted. This pooled-ratio implementation differs from
the original A8 equal-family-mean bootstrap: retain the original CI verbatim in
frozen_* columns and label new CIs, never replace old numbers silently. Use the
same draws for paired effects. CI eligibility requires >=10 eligible instances,
>=5 independent families, and >=5 informative events: for a contrast, discordant
binary pairs; for a level, both >=5 positives and >=5 negatives. Otherwise give
the point/counts, blank CI, and the exact sparse reason; do not invent variance
or treat a degenerate interval as precision. Pooled-empty cells are NA.

## A3: boundary protocol statement only

Use the original saved cds_boundary request. Remove the candidates field before
checking injection retention, so copied question candidates cannot masquerade
as retained knowledge. Count complete retention and any missing entry in the
remaining task/episode_trace context. Empty contributes to the all-416 count
but has no required knowledge; separately report the nonempty denominator.
Boundary questions may have a full trace even if action requests had bounded
traces; do not infer boundary loss from action loss. No new BIA scores or calls.

## B0: prospective equivalence gate and measurement audit

For each of five conditions, require the same 52 directed instance IDs and
material SHA256 as A8. Compare multisets of public text/source/timestamp as
fed to the two paths, not only file names. Audit the shared material constructor
for K coverage, K+S stale additions, S-only, empty and K+N neutral/noise content;
do not assume an equal name means an equal intervention.

Distinguish public-material identity from semantic input equivalence: examine
cds_bundle -> raw_claim -> _claims -> aggregator -> persistent operators/belief.
If private assertion payload supplies additional factual layout, input-family,
quantity, execution/tool information not stated in the LLM-visible facts, or
source metadata exposes correct/stale roles unavailable to A8, strict same-input
semantic equivalence fails, even if the visible-fact multisets match. Record a
concrete field-level example per discrepancy; do not read episode outcomes to
choose a more permissive gate. Parametric/prior and Oracle bypasses are explicit
controls, not evidence that all ten methods consume the knowledge intervention.
If any condition fails, B0=NOT_EQUIVALENT, B1/B2 not executed; deterministic CDS
can only be described as related evidence, not the requested causal replacement.

Independently trace whether original deterministic contact uses
classify_actions_v3/intrinsic_discriminative or catalog/used-mechanism labels.
Report differences. Never relabel a catalog contact as discriminative contact.

## B1/B2 only if B0 passes

Use the same frozen classify_actions_v3/intrinsic_discriminative definitions as
A8/main, applied offline to saved events with catalog stale matches; no engine.
Report K+S-K+N contact, empty-K feasible SR, K feasible SR per method plus an
equal-method pooled summary. The bootstrap unit is 52 directed instance pairs,
not 2,600 episodes: sample instance keys with replacement, retaining all methods
for each sampled key. Use 10,000 draws, seed 20261017. Suppress contact CIs if a
method has fewer than 5 contact-positive episodes in the two compared cells;
also require >=10 eligible pairs. Feasibility comes from frozen task/record
labels. Never merge deterministic and LLM estimates; place them in parallel
tables with their own method, denominator, scope and scoring provenance.

## New outputs and local commits

reports/main/post_review/a14_cds_retention/ contains request/fact/episode data,
cross-products, prefix contrast tables, boundary coverage only, B0 provenance
and REPORT.md. Hash source files before/after. Commit this preregistration first;
then implementation, tests and results in a separate local commit. No push.
