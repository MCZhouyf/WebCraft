# Placebo Invariance Preregistration

Registered: 2026-10-11 UTC, before inspecting any placebo-run outcome.

## Scope

This is the only new execution permitted in the final closeout. It is a deterministic,
zero-LLM replay of every executable pipeline method under `all_public` only:
`parametric`, `probe_then_parametric`, `naive_rag`, `majority_rag`,
`truthfinder_rag`, `verify_all`, `arbiter_shell_naive`, `oracle`, `arbiter`, and
`probe_then_rag`. The matrix is the frozen 34 evaluation tasks, 11 registered
condition-world cells, and seeds 20261006 and 20261007 (748 episodes per method).
`llm_only` is excluded because the proposed perturbations concern public retrieval;
`oracle_reading` is excluded because it directly supplies gold-path claims rather than
retrieving ranked public chunks. No model endpoint or fallback extractor may be called.

## Allowed Perturbations

The perturbation seed is `20261011`. Only these three irrelevant-field classes may
change:

1. Source site names or URL domains are replaced by a deterministic bijection. Equality
   classes are preserved, so source-count and source-agreement logic see the same
   partition.
2. Exact BM25 score ties are ordered by a deterministic hash of the seed, query, and
   chunk ID instead of lexical chunk ID order. Non-tied scores are not reordered.
3. Every chunk receives the same version-independent decorative token
   `zxqv_placebo_ornament`; BM25 document lengths and average length are recomputed.

The timestamp, revision ID, page title, chunk ID, claim cache, claim payload, task,
truth, cost model, prior bundle, and all explicit or implicit version-identifying fields
must remain byte-for-byte or value-for-value unchanged. In particular, timestamps and
version markers must never be perturbed because Probe-then-RAG uses temporal structure.

## Expected Invariance

For every method-instance pair, the terminal class, SR, `HTR_commit`, `HTR_all`, and
used mechanism sequence are expected to remain unchanged. The report must also show
terminal distributions and metric deltas by method. A nonzero difference is retained
and reported as retrieval sensitivity; the first divergent instance is traced to the
shortest available difference in retrieved chunk order, admitted claims, selected
mechanisms, actions, and terminal state.

## Hard Stop And Resources

The design is executed once. It must not be edited or retried in response to the
result. The runner uses the deterministic simulator, `nice -n 15`, one process, no GPU,
and at most 2 GiB additional resident memory. Before execution it records available
memory and hashes this preregistration and every source index. Original corpus indexes,
claim caches, ARBITER artifacts, and all frozen WebCraft files are read-only.

## Outputs

Outputs go to `reports/paperA/placebo_invariance/`: the executable audit script,
perturbation manifest and hashes, per-method terminal comparison, per-method metric
deltas, per-instance comparison, first-divergence trace when applicable, resource
record, and a summary. The copied perturbed indexes are temporary audit inputs and are
removed after the run; no original index or cache is modified.
