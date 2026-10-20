# A22 pre-evaluation parameter and source freeze

This file and config/a22_pf_rag_frozen.json are committed before any A22
held-out task or evidence access. Only synthetic tests and the original eight
development tasks have been used. No held-out evaluation has yet run.

Frozen B=1, fallback=false. The registered selection criterion is highest
development SR, then lowest semantic cost, then smallest B. On the complete
fixed-interpreter grid, every B in {1,3,5} scores 34/72; their total semantic
costs are 228, 362 and 500. Naive scores 34/72, cost 159. These are development
outcomes only. Selected B=1 repeats identically on all 72 instances with the
second episode seed; the pilot also matches serial and four-worker execution.

Full method/shared-dependency hashes are in
artifacts/post_review/a22_pf_rag/development/grid_fixedhash_20261020/manifest.json,
SHA256 f0b397e2d754da55f7d44e4a0841dbbb07f0dcc2afdc7c96e28b77e280ee847e.
Verify these hashes before evaluation. Do not change PF, shared reader,
compiler, planner, public adapter or selected parameters after this commit.
Evaluation orchestration and offline export code may still be implemented;
they must not change any method rule or score definition.

Execution uses PYTHONHASHSEED=20261020 for both independent episode seeds,
20261020/20261021. The first unfixed-interpreter trial failed replay on 3/72;
it remains archived, including its failure status. The complete B grid and
complete second-seed repeat were rerun, not selected failures only. See
reports/main/post_review/a22_pf_rag/development/DETERMINISM_DIAGNOSIS.md.

The stabilized grid plus repeat took 120.585 seconds at four workers. This
does not establish a held-out runtime estimate. There are zero no-conflict
bundles among the 24 real development bundles. Synthetic no-conflict equality
tests pass, but real-instance no-conflict equality is not established here;
report the evaluation denominator explicitly and fail QA on any mismatch.

Zero LLM calls throughout. Public version mentions are permitted only by the
explicit author clarification; PF does not parse them or use version mappings.
No post-freeze modification based on held-out results is authorized.
