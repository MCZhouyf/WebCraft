# Consequence-based HTR commitment rule

Date: 2026-10-13

Status: preregistered post-review measurement reanalysis. This analysis makes
zero LLM calls, does not rerun an episode, and does not replace a frozen method,
parameter, episode, or previously reported number. The new columns and tables
are supplementary and retain the accepted plan-source and legacy self-reported
columns beside them.

## Inputs and scope

The pipeline scope is every saved episode for the ten reported methods in
`all_public`, `llm_only`, and `oracle_reading`. The external scope is the five
completed external methods on their common 110-instance matrix. Staleness is
not inferred from an action outcome: it remains the frozen evaluator relation
that a matched DV assertion is false in the execution world and true in at
least one other world. Evaluator labels are used only by this offline scorer.

Pinned analysis inputs:

| Input | SHA-256 |
|---|---|
| `artifacts/main/all_public/episodes.jsonl` | `a73a99871810cf416ebbb33588cca3185aa6621ae848b45c2d212ce1c9107134` |
| `artifacts/main/llm_only/episodes.jsonl` | `b0e87c93a7f8b25d15db965e7f406b416c6e0910b3b794aae96c1c69d36af4a1` |
| `artifacts/post_run_probe_then_rag/all_public/episodes.jsonl` | `32555ecac0ca92147dbcc1055b936bb1a82d63fecb8f798686ecea5fcc969fc7` |
| `artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl` | `ec09d6d9d835e824bad4fa8dbef4f5c0cb93a636ae8fda5c0c85384c772a1652` |
| `artifacts/post_review/oracle_reading/episodes.jsonl` | `7f1d4bc3503e944bf766e5f7f0e8c2ce7867cb4c664d10677cb5e7c0db3ad305` |
| `reports/main/post_review/external_agents/rescore_v2/action_audit.csv` | `b6e175cfb2880c893a3b7a1c9d88fe22d78d8fd87f2790bda73e38299c1f774f` |
| `reports/main/post_review/external_agents/rescore_v2/episode_metrics.csv` | `9cfedd3c1824a4e4ebf270ffb846ca2296b9fde100f0c285ee7931d59c4824db` |
| `reports/main/post_review/measurement_v2/plan_source_episodes.csv` | `291f4f50851dbe1e76530ec43570f59760f9f5dc3fd1300b54f04290757c0862` |

The external episode records name immutable action logs. Their bytes are
validated against the paths in those records at analysis time and recorded in
the output provenance. The WebCraft repository is read-only.

## Event normalization

Semantic actions are `mine`, `craft_grid`, `smelt`, and `check_name`. Event
order is the saved `sequence` order. Names drop only the optional
`minecraft:` prefix. An exact action signature is the method plus canonical
JSON of public parameters; for `mine`, the resource node ID is mapped through
the frozen public arena resource table to the block name so equivalent nodes
share a signature. No `intent`, plan-source, model thought, or hidden state is
used.

For each stale semantic action, its predicted products are the positive
products in the saved pipeline action signature when available, otherwise the
positive `item_flow.produced` entries on the event. Inventory dependence uses
FIFO flow: a predicted product is later consumed when a later semantic event's
public `item_flow.consumed` removes a positive amount of that item. Initial
inventory cannot supply this witness.

A result mismatch means the saved pipeline `observed` value disagrees with its
predicted outcome, or, where no such trace exists, the public action event has
`success=false`. No textual error interpretation is added.

## Consequence commitment classification

A semantic action matched to at least one stale assertion is a consequence
commitment if one or more of these observable witnesses holds:

1. A predicted product from the action is consumed by a later semantic action.
2. After a result mismatch, a later semantic action has the same exact action
   signature.
3. Before any later semantic action, the episode executes `submit` and then no
   semantic action follows. This terminal adjacency is the fixed observable
   proxy for a submission depending on the action's predicted result; both
   feasible and infeasible submissions count.

Otherwise it is classified as a consequence probe. In particular, a mismatch
followed by a different semantic action is `replanned`, and a mismatch followed
by an infeasible submission without an intervening semantic action is a
commitment under rule 3, not an abstention-only probe. An episode has
`HTR_commit_c=1` iff at least one stale semantic action is a consequence
commitment. It has `HTR_all=1` iff at least one stale semantic action occurs.

For pipeline episodes, the aligned `used_mechanism_ids` supplies the stale
assertion identity. For external episodes, all independently matched stale
candidate assertions from the frozen action audit are used. Contradictory
candidates do not consult self-reported intent: the event is stale-contacting
when its candidate set contains at least one stale assertion, and the three
consequence witnesses determine the point estimate. This is an action-contact
estimand, not a recovered latent model belief. The report must state this
identification limit.

## Comparisons and audits

Pipeline `HTR_commit_c` is compared episode by episode with the accepted
plan-source `HTR_commit`. For each reading mode, output a 2x2 count matrix,
agreement and disagreement rows. Disagreement reasons are the first applicable
of: `consumed_after_plan_probe`, `repeated_after_mismatch`,
`terminal_submission_after_probe`, `plan_commit_without_consequence`, or
`multiple_witnesses`. Matrix diagonal means the sum of the 0/0 and 1/1 cells;
the report also gives diagonal / total.

The Appendix-E adversarial check is a deterministic fixture with a stale
semantic action marked `intent=probe`, a mismatched result, and the same action
repeated. The fixture passes only if rule 2 yields `HTR_commit_c=1`; intent is
not read by the classifier.

For Verify-All, each stale plan-source probe with a negative result is assigned
exactly one next-behavior category: `continue_original_plan` if the same exact
semantic action is repeated; `abstain` if no semantic action intervenes before
an infeasible submission; otherwise `replan` if a different semantic action
follows. A negative probe with neither a later semantic action nor an
infeasible submission is reported separately as `indeterminate` and excluded
from the three-way proportions, with its count shown.

## Output contract

All output is written under `reports/main/post_review/htr_consequence/`:

- `pipeline_actions.csv`, `pipeline_episodes.csv`, `pipeline_summary.csv`;
- `consistency_matrix.csv`, `disagreements.csv`;
- `external_actions.csv`, `external_episodes.csv`, `external_summary.csv`;
- `adversarial_validation.csv`, `verify_all_after_negative.csv`;
- `REPORT.md` and `provenance.json`.

Every table is deterministic and sorted by reading mode, method, condition,
world, task ID, seed, and event index. Existing result files are never edited.
