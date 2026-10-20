# A21 task scheduling after three-worker authorization

For new attempts, complete pending episode jobs are offered in their frozen
manifest order through one shared process queue. Each available worker claims
one next job and records it under that worker's own attempt directory. The
six credentials remain in three disjoint two-slot worker pools; no credential
is used concurrently by different workers. This prevents long episodes from
leaving other workers idle due to fixed shards.

Already-running development processes retain their existing assignments.
Accepted complete checkpoints are never replayed. Queue scheduling affects
only which worker owns an episode; agent memory is reset for every episode,
and all scientific settings, materials, prompts, gates, and scorer stay the
same. A worker infrastructure/model-identity failure stops newly launched
sibling workers and records incomplete traces separately from completed data.
