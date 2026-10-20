# Preregistration Amendment V6: Probe-Aware Historical-Truth Reliance

Date: 2026-10-08. This amendment is frozen before P6 round 5 and before any
round-5 result is observed. It is explicitly post hoc with respect to round 4:
the round-4 evaluator audit showed that 7 of ARBITER's 12 stale episodes arose
from VOI-selected probe actions, while 5 arose from committed plan actions.
No round-4 result is relabeled or discarded.

## Formal metric definition (§3.8)

Let an episode action be stale when its evaluated mechanism is false in the
active world and true in at least one other benchmark world. Define:

- `HTR_all = 1` iff at least one stale semantic action occurs in the episode.
  This is exactly the original HTR definition; the legacy `HTR` key remains an
  alias of `HTR_all` for old consumers.
- `HTR_commit = 1` iff at least one stale semantic action remains after removing
  action-log records whose agent-declared `intent` is exactly `probe`.
- `probe_count` is the number of action-log records with `intent=probe`.
- `probe_cost` is the sum of their registered primary semantic-action costs.

Missing `intent`, and every value other than the exact string `probe`, is
treated as committed. The agent writes `intent`; the evaluator only reads it
and does not validate whether the declaration is truthful. This limitation is
reported rather than hidden. Probe actions continue to count in total primary
cost and OVR, and `HTR_all` is always reported beside `HTR_commit`. Consequently,
declaring every action a probe can reduce `HTR_commit` to zero but cannot reduce
`HTR_all`, total cost, probe cost, or the OVR burden.

All formal tables must report `SR`, `HTR_commit`, `HTR_all`, `probe_count`, and
`probe_cost` in separate columns. Existing logs without `intent` remain valid
and reproduce the old HTR through `HTR_all`. These metrics are computed and
reported separately for each frozen reading mode below; rows from different
reading modes are never pooled.

## P6 round-5 gate

The primary P6 decision is `PASS` exactly when the Naive-RAG minus ARBITER
`HTR_commit` gap is at least 0.15 (15 percentage points) and ARBITER SR is not
lower than Naive-RAG SR. The former mean-probe upper bound is no longer part of
the pass condition because probe use is now charged and reported directly.
The same 15-point/SR rule is also evaluated with `HTR_all` and emitted as a
separate comparison decision; it does not replace the `HTR_commit` decision.

This gate change is motivated solely by the round-4 action-role audit above. It
is made after observing round-4 results and before round 5, and must be described
as a post-hoc protocol amendment in every report. Tasks, worlds, corpus
conditions, seeds, reader-v5 settings, inference, planner, costs, OVR, and all
other frozen evaluation rules remain unchanged.

## Reading modes

The main experiment defaults to `all_public`: the agent reader receives every
claim exposed by the public cache contract, without filtering on
`extractor_version` or `payload.extractor`. The control mode is `llm_only`,
which admits only claims carrying the frozen LLM extractor version. Every
method-condition result is reported as two separate table suites, one per
reading mode, using identical tasks, worlds, seeds, costs, and evaluators.

The P6 gate is evaluated only on `all_public`. The complete `llm_only` table
suite is a reading-source control and cannot pass, fail, qualify, or disqualify
P6. The `HTR_all` comparison decision remains descriptive within each suite.

The corpus-session v6.2 solvability audit in
`reports/corpus_stats/solvability_formal.csv` and
`reports/corpus_stats/solvability_formal_p1.csv` reports the following frozen
support:

| Corpus | Reading mode | Matched mechanisms | Matched closed plans | Mixed mechanisms | Mixed closed plans |
|---|---|---:|---:|---:|---:|
| P0 | `all_public` | 527/531 | 88/92 | 531/531 | 92/92 |
| P0 | `llm_only` | 381/531 | 16/92 | 460/531 | 39/92 |
| P1 | `all_public` | 527/531 | 88/92 | 531/531 | 92/92 |
| P1 | `llm_only` | 383/531 | 16/92 | 460/531 | 39/92 |

These are corpus-solvability diagnostics, not agent results. `all_public`
includes the source-derived template and deterministic-prose routes as well as
LLM-extracted claims; `llm_only` does not.
