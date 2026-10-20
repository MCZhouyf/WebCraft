# A21 author-authorized six-worker transport

Rule formed on 2026-10-18, after the author's explicit instruction to permit
six workers, before any six-worker A21 attempt. This supersedes only the
concurrency ceiling in the earlier two- and three-worker transport entries.

The six supplied credential slots are partitioned into six disjoint singleton
pools. Workers share the existing pending queue; no episode is assigned twice.
Actual work is bounded by the number of pending episodes. Existing live
workers are not duplicated or interrupted merely to increase the ceiling.

The shared HTTP start clock, request spacing, canary checks, exact glm-5.2
server identity lock, and fail-fast infrastructure handling remain enabled.
An individual singleton pool has no alternative credential: exhaustion stops
the attempt, preserving complete checkpoints and auditable partial prefixes.
Six authorized workers do not assert six independent provider rate limits.

No task, material, prompt, model, decoding setting, episode budget, gate,
scoring rule, or statistical rule changes. Credentials remain memory-only;
WebCraft is read only, no GPU is used, and unrelated processes are untouched.
