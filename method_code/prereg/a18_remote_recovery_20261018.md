# Author-authorized infrastructure recovery

The original four-slot remote run stopped after 182 complete episodes and one
240-second action-request timeout. Its artifacts and statistics are preserved.
Four interrupted episodes have respectively 18, 2, 7 and 35 saved successful
replies; the last also has an unanswered timed-out request. No infrastructure
interruption is a cognitive failure. No completed episode will be rerun.

Under the author's instruction to prioritize reliable execution over the earlier
transport restrictions, finish only the 26 missing registered keys in a new
`remote_service_resume1` segment. Preserve the model alias and all method/material/
prompt/40-call/90-action/4096-token settings. Extend only the client read timeout
to 900 seconds for this segment; keep four slots and global canaries.

Before any new generation, replay the four saved prefixes offline through fresh
deterministic simulators, requiring every reconstructed model request to match
its saved full messages, decoding parameters and SHA256. Continue from the first
unanswered request, not from a new model decision at the start. Cached responses
consume their original per-episode decision/token accounting but are explicitly
marked replayed and are not counted as new generations. Deterministic simulator
replay is state restoration only; it is not a new model episode or an extra
experimental observation. No original action/prompt/result file is overwritten.

If any prefix diverges, do not silently fall back to rerunning it. Report it as
missing infrastructure data. Aggregate original and recovery segments by unique
condition/instance key, preserving the 182 original completed rows. Count actual
network generations/errors from both ledgers; never double-count replayed replies.
The local 14B run remains separate. No endpoint, key, remote shell/service/GPU
operation, frozen method change or remote push is introduced.
