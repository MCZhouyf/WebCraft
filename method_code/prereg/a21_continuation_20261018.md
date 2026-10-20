# A21 continuation and transport recovery

This entry is committed before resuming the seven unfinished development
episodes. The sole completed development episode, dev_pla_003 (K, 12 calls,
success), is retained. Interrupted traces and the three earlier attempt
directories are retained and excluded from success denominators.

The continuation uses one worker with at least ten seconds between completed
requests. A persistent worker client retains its throttled-slot quarantine and
budget counters across episode boundaries. No new trial replaces a completed
trial. Resume requires a unique checkpoint, matching public-material SHA256,
matching server model identifier, and extant action/prompt logs. New attempts
have separate directories. Canary or infrastructure failure stops the attempt
and writes a status without scoring the interrupted episode.

The original development gate is evaluated only after all eight registered
episodes finish. Formal MDS, if reached, must also respect the author's
original instruction: run K first, disclose its feasible SR, and read/run
controls only if it falls in [0.40, 0.80]. This clarifies the original author
instruction and does not replace it with the eight-task development gate.
No formal MDS episode has been run. Any subsequent shortening requires its
own material registration before model calls; no automatic task replacement
is authorized by this transport recovery.

Remaining development scope: seven episodes, at most 280 action-generation
calls, plus canaries. Historical ledger prices (8/2/28 CNY per million
uncached/cached input/output tokens) are estimates, not provider invoices.
Actual usage and failed-request counts will be reported from all attempts.
