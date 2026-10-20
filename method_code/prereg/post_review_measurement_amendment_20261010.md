# Post-review measurement amendment, 2026-10-10

This author instruction supersedes A4 of `post_review_addendum.md` only. It
does not modify any frozen method, hyperparameter, accepted episode, task,
condition, seed, or preregistered result.

The earlier behavioral rule ("a claim's first adjudication is a probe until a
prior step has depended on it") is retired and retained only as a column named
`degenerate_first_use_HTR_commit`. Its state update cannot move a claim from
uncommitted to committed on first use and therefore collapses commit HTR to
zero.

For pipeline methods, an action from the selected plan and its compiled
operator is a commitment. An action emitted by the explicit validation branch
is a probe. Saved schema-v2 intent and aligned `used_mechanism_ids` are the
execution provenance; replay must equal the accepted main-experiment
`HTR_commit` exactly. An infeasibility submission without a semantic action is
not an HTR event under the frozen evaluator.

For external agents, `HTR_commit` uses the retained self-reported/executed
intent label. A separate consistency audit reports episodes where a claim is
labelled as probed and a later committed semantic action matches that same
claim as true. This audit is not folded into HTR. The external runner preserved
model-authored intent only for ADAM; for the other four methods it overwrote
intent with `commit`, which must be disclosed.

ADAM probe count is split into all probe-labelled public calls and paid world
probes (`mine`, `craft_grid`, `smelt`, `check_name`). Search and inventory are
free and excluded from corrected world-probe count while remaining visible in
the per-episode audit. Charges continue to follow the frozen cost model.

Oracle-reading adds the omitted post-run `probe_then_rag` pipeline baseline at
seed 20261006. Previously completed oracle-reading episodes are reused; only
missing matrix keys may execute. The operation is deterministic and makes zero
LLM calls.

After the first full-test attempt stopped before collection at the previous
2.5 GiB total gate, the author explicitly waived that threshold. The full
pytest command therefore uses the repository's existing
`ARB_AUTHORIZED_MEMORY_TOTAL_BYTES` override at 1.5 GiB. The committed default
guard remains unchanged. This exception does not apply to experiment execution
and changes no method.
