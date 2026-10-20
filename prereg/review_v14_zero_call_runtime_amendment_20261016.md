# W7b runtime-order amendment

**Recorded:** 2026-10-16 UTC, after observing the first O replay check and
before obtaining any V, C, V+C, CP-RAG evaluation, split-placebo, cost, or
reconstruction result.

The registered `PYTHONHASHSEED=0` replay produced the same terminal, cost, and
mechanism multiset as the frozen main episode
`mismatched_old2new/con_006/naive_rag`, but exchanged two independent
prerequisite actions (`cf_229bff9cd093ef08` and `cf_d331d6ca1212c208`). This
failed the preregistered exact-sequence O gate. A bounded diagnostic over seeds
1--10 found that seed 2 restores the frozen order for the first failing
episode. No intervention outcome was available during this diagnostic.

For the validity-by-coverage component only, the runtime hash seed is therefore
amended from 0 to 2. Every O episode across both directions, both cells, 34
tasks, and ten methods must still match the frozen terminal and complete
mechanism sequence. The component stops on the next mismatch. Bootstrap RNG,
task ordering, intervention construction, metrics, and every other registered
rule remain unchanged. CP-RAG and the split placebo retain
`PYTHONHASHSEED=0`.

This is a post-registration runtime-compatibility amendment and must be
reported as such; it is not evidence that seed 2 is generally canonical.
