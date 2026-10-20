# Review Supplements W2 and W3-ii

Date: 2026-10-13 UTC. This protocol is committed before implementing either
supplement and before the supplement runner reads any evaluation task record or
evaluation outcome. It authorizes deterministic, zero-LLM re-execution only. Frozen
truth, tasks, corpus indexes, public claim caches, costs, metrics, simulator, seeds,
and existing methods are read-only.

Frozen code/data anchors at registration:

- WebCraft commit: `78c2e5161c8bfd85c5eb6723bbe970b7520913ee`.
- ARBITER commit: `b6fccb7e8039e448235140856b036d6821192e3f`.
- Evaluation cells: the 11 tuples in ARBITER `MAIN_CELLS`.
- Seeds: 20261006 and 20261007. Byte-identical duplicate-seed results are retained
  in raw outputs and deduplicated only in summary tables, as in the main study.
- Evaluation tasks: exactly the 34 frozen evaluation-split tasks. They may be loaded
  only after implementation and tests are complete, immediately before execution.

All new code, transformed claims, manifests, episodes, and reports must live under
new supplement-specific directories. No existing artifact may be overwritten. Every
input and output receives a SHA-256 manifest.

## S1: `all_public_corrected`

This is a reading-quality intervention, not a retrieval or truth intervention. Search
queries, BM25 indexes, top-k, chunk order, source metadata, timestamps, task matrix,
planner, and execution policy remain unchanged.

For every retrieved chunk, the correction layer processes only claims already returned
by the frozen `all_public` cache:

1. The chunk's exact `(source_site, page_title, revid)` identifies the matching private
   gold revision. A public claim can map only to gold claims with the same
   `(subject_item, claim_type, polarity, tense)` in that revision. The public claim's
   presence in the retrieved chunk is the chunk-level support anchor.
2. If one or more gold claims match that key, the public group is replaced by the
   complete, sorted, deduplicated set of those gold payloads. Public provenance fields
   remain tied to the original chunk. Private `truth_vector` and source fields are never
   exposed to the agent or logs. New claim IDs are deterministic hashes of the mode,
   chunk, matching key, and corrected public payload.
3. If no gold claim matches, the public claim is treated as extraction hallucination and
   removed. The layer never creates a subject/type group that was absent from that
   chunk's public cache. Parent/normalized expansion rows are regenerated only from an
   admitted corrected parent; an orphan is removed.
4. Gold payloads remain revision-matched. Thus stale pages retain stale gold mechanisms;
   no payload is replaced by current-world truth. Alternative routes, one-sided silence,
   source availability, and erroneous source assertions remain exactly as represented
   in the revision-matched gold. The intervention corrects extraction only.

For polluted-only pages, the source-faithful gold record is the frozen `core_claim` in
`corpus/private/pollution/instances.jsonl`, joined to the exact frozen document by page
title, timestamp, and page text after the indexer's deterministic whitespace
normalization (runs of whitespace become one space). No lexical token may differ. This
preserves the deliberately false source assertion
while correcting only its extraction. The `truth_vector`, `synthetic`, model, prompt,
and generation fields are never copied into the corrected cache, evidence, or episode
logs. Polluted-only claims without an exact frozen source record are removed rather than
matched to a real Wiki revision.

The transformed cache is materialized at
`reports/paperA/all_public_corrected/cache/<condition>/chunk_claims.jsonl`; formal BM25
indexes are referenced read-only and are not copied or changed. A manifest must report
claims before/after, corrected groups, removed unmatched claims, and source/output
hashes per condition. Contract and forbidden-field tests are mandatory.

Run the ten executable pipeline methods
`parametric`, `probe_then_parametric`, `naive_rag`, `majority_rag`,
`truthfinder_rag`, `verify_all`, `arbiter_shell_naive`, `oracle`, `arbiter`, and
`probe_then_rag` over all 34 tasks, 11 cells, and two seeds. Non-reading methods are
expected to remain identical but are rerun as controls. Produce an episode export and
the same terminal, plan-source, world, subset, secondary-metric, IAA, and IC table
schemas used for `all_public`.

For Naive RAG, join every original `all_public` execution-failure instance to the
corrected instance by `(task_id, condition, world, seed)` and report its original and
corrected terminal, SR, and action outcome. The prompt-provided count 65 is an assertion
to verify, not a filter target. The number of corrected Naive execution failures is
reported verbatim as failures attributable to knowledge validity rather than extraction
noise under this intervention. No threshold or success claim is registered.

## S2: Probe-then-VersionScoped

This new method uses the frozen Probe-then-RAG probe and version-identification procedure
without alteration. The agent receives only the single identified version result; it
does not receive a candidate-version list. Probe costs, intent labels, execution policy,
planner, and action budget remain unchanged.

Every public claim receives a deterministic applicability interval over semantic Java
versions. Interval derivation uses only public `tense`, `span_text`, the retrieved chunk
text and timestamp; private truth is forbidden.

### Fixed release epochs

- 1.16.5: 2021-01-15 UTC
- 1.19: 2022-06-07 UTC
- 1.21.11: 2025-12-09 UTC

A revision timestamp maps to the greatest epoch not later than that timestamp. A date
before the first epoch maps to a pre-1.16.5 open epoch. Semantic versions are compared
as zero-padded integer tuples; prereleases and snapshots are not inferred.

### Fixed version parser

Case-insensitive patterns, in priority order, are:

1. `before\s+(?:Java Edition\s+)?(\d+\.\d+(?:\.\d+)?)`
2. `(?:as of|since)\s+(?:Java Edition\s+)?(\d+\.\d+(?:\.\d+)?)`
3. `Java Edition\s+(\d+\.\d+(?:\.\d+)?)`

Search `span_text` first. If it has no match, search the complete retrieved chunk. When
several matches have the same priority, use the first textual occurrence. A parsed
version is projected to the greatest registered epoch not greater than it; a value below
1.16.5 maps to the pre-1.16.5 epoch and a value above 1.21.11 maps to 1.21.11.

### Fixed interval rules

- `tense=historical`: lower bound is open past. The upper bound is the parsed epoch. For
  an explicit `before` pattern, the upper bound is strictly below the parsed epoch. If no
  mention parses, the upper bound is strictly before the revision epoch.
- `tense=current`: lower bound is the parsed epoch when available, otherwise the revision
  epoch; upper bound is open future.
- `tense=future` or `unknown`: no admissible interval; the claim is filtered out.

An identified version retains a claim exactly when it lies in the interval. Filtering is
claim-level, unlike Probe-then-RAG's chunk timestamp cutoff. Search results and retrieval
logs remain unchanged. The interval and parser provenance are evaluator-private and are
not exposed through the semantic API.

Run Probe-then-VersionScoped over all 34 tasks, 11 cells, and two seeds in each of
`all_public`, `llm_only`, and gold-path claim supply (legacy export name
`oracle_reading`). Reuse the already frozen Naive and Probe-then-RAG exports for
comparison; do not rerun or alter them. Reports under `reports/paperA/version_scoped/`
must provide terminal/SR/HTR/probe tables by mode, condition, world, and subset, plus
paired per-instance comparisons against both reference methods.

The requested primary contrast is the seed-deduplicated `mismatched_new2old` cell:
Probe-then-VersionScoped SR minus Probe-then-RAG SR. It is reported with its signed value
and task-paired 10,000-draw percentile bootstrap interval (seed 20261013). No favorable
direction or minimum effect is preregistered.

## Execution and stopping rules

Implementation tests use synthetic fixtures and development tasks only. After tests
pass, the runner may load the 34 evaluation tasks and execute the fixed matrices once.
Any deterministic infrastructure failure may be diagnosed and fixed only when the fix
does not change these rules; otherwise stop and report. Results are never used to alter
matching, regexes, epochs, intervals, method parameters, or report denominators.

Resource policy: CPU only, no GPU, zero gateway/model calls, `nice -n 15`, and the
existing memory reserve. The final report records commits, timestamps, wall time, peak
RSS, zero-call evidence, all source/output hashes, and any deviation.
