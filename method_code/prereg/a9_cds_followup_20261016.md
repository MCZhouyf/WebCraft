# A9 CDS zero-call follow-up

Registration date: 2026-10-16. This follow-up is a deterministic, post-A8
analysis of existing A8 exports and immutable action logs. It makes no model
call, runs no new environment episode, changes no frozen method or prompt, and
uses `/root/autodl-tmp/webcraft` read-only. All prior A8 metrics and rules stay
unchanged; this document only registers additional columns and tables.

## Inputs and scope

The only CDS episode input is
`reports/main/post_review/cds_agents_20261017/episode_metrics.csv` together
with its matching `stale_action_audit.csv`, saved A8 action logs, and saved
boundary predictions/truth. The expected matrix is 416 unique episodes: Direct
LLM x `{K, K+S, empty}` and ReAct x `{K, K+S, S, empty, K+N}`, each with 52
fixed CDS instances. A9 verifies this coverage before reporting any result.

All paired estimates first average within the pre-existing `family_id`, then
use 10,000 independent-family percentile bootstrap draws with NumPy seed
`20261017`, the A8 seed. Missing or non-applicable items are reported as such,
never recoded as model failures.

## 1. Boundary-identification analysis

For each method, condition, and discriminative-probe stratum, report: the gold
valid/invalid counts and proportions among answered boundary items; valid and
invalid recall; balanced accuracy (mean of those recalls when both gold classes
are represented among answered items); and uncertainty rate over all candidate
items. If either gold class has no answered item, balanced accuracy is `NA`.

Three deterministic comparators score every boundary candidate: (a) always
invalid; (b) newest timestamp valid, all older candidates invalid, using the
frozen candidate timestamps; and (c) context-present valid, otherwise invalid.
For (c), context presence means that the exact normalized candidate mechanism
fact occurs in the episode's frozen initial `diagnostic_mechanism_facts` or in
the text returned by an already executed public `search` action before the
boundary question. No evaluator or hidden-world field is read for that rule.

The required K+S significance tests compare episode-level answered-item
balanced accuracy for Direct and ReAct separately against each comparator. An
episode contributes only when both gold classes are represented among its
answered items; the output records the eligible pair count. The reported
difference is agent minus comparator.

## 2. Contact and propagation overlap

For each A8 method in K+S and K+N, define the contact set as episodes with
`discriminative_stale_contact=1`, and the propagation set as episodes with
`v3d_lower=1`. Report intersection, union, contact-only, propagation-only,
and their instance IDs. A propagation event is every action row with
`v3d_propagation=1`; its trigger rules are reconstructed deterministically
from existing A8 fields: predicted-product reference (i), exact stale-action
repeat (ii), and affirmative submission (iii'). Ten examples are sampled
without replacement from propagation events using seed `20261016`, sorted by
`method, condition, instance_id, event_index` before sampling. Each example
contains the stale action, immediate world feedback, next relevant action, and
terminal record. The report distinguishes empirical overlap from the shared
definition: propagation is necessarily a subset of contact because v3-d is
defined only for a denied discriminative stale action.

## 3. Additional paired contrasts

Report all requested contrasts: ReAct `S-K`, `S-empty`, and `K+S-K`; Direct
`K+S-K` and `empty-K`; plus Direct minus ReAct in `K`. Metrics are SR,
feasible-task SR, overconservatism among feasible tasks, terminal execution
failure, first-conflict target-world agreement (only episodes with such a
decision), discriminative stale contact, and v3-d lower propagation. The
bootstrap rule above applies to every metric. Conditions use the unchanged A8
names, including `empty` for the null-knowledge condition.

## 4. First-run query follow-up

Reuse the already registered A8 zero-call four-class scoring exactly:
`denied_then_target_valid`, `denied_then_stale`, `not_denied`, and
`no_related_followup`. Report each count and its per-method denominator for
Direct, ReAct, Reflexion, ADAM, and XENON; no action trace is rescored under a
new rule.

## 5. Cost estimates

Reuse the A8 least-squares same-gateway rate fit: 8 CNY/M uncached input,
2 CNY/M cached input, and 28 CNY/M output. Episode exports without cached-token
counts receive an uncached-input estimate and an all-input-cached mechanical
lower bound. Report scopes for the first-run external five methods, same-model
rerun, alias-locked replication, and A7. State that cache allocation,
provider-side discounts, hidden tokens, and absent historical billing ledgers
remain uncertainty sources.

## 6. Additional blind audit sheet

From A8 K+S episodes, form three disjoint strata: propagation, contact without
propagation, and no contact. Within each stratum, sample 10 unique episode IDs
without replacement after lexical sort using seed `20261016`. The visible
`audit_sheet_cds.csv` has the existing 147-sheet fields and blank scorer-label
columns. The separate `audit_key_cds.csv` stores contact and propagation labels
and is not included in the blind sheet. The action/feedback and search-snippet
rendering are deterministic truncations of saved logs; no labels are embedded
in the visible text.

## Completion constraints

Results are reported regardless of direction. A9 creates no new outcome claim
and does not replace prior A8 reports.
