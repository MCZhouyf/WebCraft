# A21 author-authorized three-worker transport

The author explicitly authorized three concurrent credential workers in the
current conversation on 2026-10-18, replacing A21's earlier maximum of two.
This entry is frozen before launching any three-worker attempt.

The six already-canary-checked slots are partitioned into three disjoint
two-slot pools, in round-robin order. Each worker starts with a fifteen-second
minimum gap after a completed request; model identity remains exactly
glm-5.2 and every slot switch must pass a canary. Pool exhaustion or canary
failure stops that attempt without scoring the interrupted episode.

The current single-worker episode is allowed to reach a durable checkpoint.
Only owned A21 worker processes are then stopped at that boundary, with any
subsequent partial trace retained as a scheduling interruption. Resume skips
all completed, input-matched checkpoints. Tasks, knowledge, prompts, decoding,
episode budgets, gates, scorer, and statistical rules are unchanged.
