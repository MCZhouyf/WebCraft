# A18: author-authorized remote Qwen service switch

Recorded before any request in this remote segment. The author replaces the
local 14B backend with the existing remote RTX 4090 Qwen 27B service and permits
execution/concurrency changes needed to run it reliably. Two cards are an
author-provided description, not independently verified service metadata.

This is a new 208-episode run, never a continuation or pooled analysis of the
seven completed local NF4 14B episodes. All original results remain intact.
The exact served model identifier comes from private configuration and must
match every response. An alias does not establish a weight architecture,
precision, GPU count, or tensor-parallel layout. Do not label the returned
identifier as a verified 27B weight identity without supporting metadata.

Use the unchanged 52 CDS materials, four conditions, ReAct prompt, A8 loop,
40 action-selection calls, 90 semantic-action cost, JSON-object output,
temperature zero, disabled thinking, and 4096-token output budget. Retain the
post-terminal boundary question and the known A8 knowledge-retention limitation.
No frozen method/source/result or WebCraft file changes. Reuse the A8 scorer
and the separate frozen GLM reference. No remote push.

Start with two concurrent independent episodes, with one synchronous request
per episode. A command-line ceiling of six is available only for an explicitly
selected later run; two GPUs do not imply two independent model replicas.
Do not change parallelism from task outcomes. Full prompts, responses, actual
tokens, durations, and returned identifiers are saved without connection data.
One global canary runs before jobs and after each 200 task request attempts;
new requests wait until in-flight requests finish at each canary boundary.
The counter tracks logical task calls; byte-identical transport retries have
separate ledger entries rather than consume new model-decision slots.

Timeouts, canary failures and model mismatches latch a global stop. Other
transient network/service errors permit at most two byte-identical retries;
three consecutive errors globally also latch a stop. No retry of refusals or
malformed cognitive output. Interrupted episodes are not cognitive failures.

Only connect to the existing service. An optional owned local SSH forwarding
process may be created and cleaned up; no remote shell, GPU query, model load,
service restart, or process mutation is allowed. Server-lock access is shared
and nonblocking because the deterministic simulator starts no game server.
Endpoint and credentials are read from a private, owner-only JSON configuration,
never printed, serialized into artifacts, or committed. No credentials are
created or copied by this runner. CPU clients use nice 15 and no local GPU.

Output root: `artifacts/post_review/a18_qwen_cds_20261017/remote_service/`;
reports use the corresponding new `remote_service/` directory. Stop status and
missingness are explicit. The 10/20 cutoff remains. Scores and family-cluster
contrasts use the original A18/A8 rules, seed 20261017 and 10,000 draws.

At registration no current connection configuration is available. The historical
WebCraft response alias was `coder390`, but it is not silently assumed to be the
current endpoint. Generation remains blocked until configuration, metadata and
the canary pass. Historical concurrency benchmarks are not current throughput
measurements and cannot supply a completion-time guarantee.

## Supplied connection record (before any remote request)

The author has now provided the missing existing-service SSH connection and key.
Use the pre-existing read-only WebCraft memory-only author-message loader; it
creates a 0600 anonymous memfd, never a private-key file. Pass that descriptor
only to this task's owned local SSH forward and close it on exit; clear its
task environment values. Preserve known-host verification. No remote shell,
jump-side tunnel start, or model/service operation is authorized by this runner.
The required advertised/returned alias is `coder390`, supplied by the author.

Before experimental jobs, benchmark fixed synthetic prompts at concurrency
1, 2, and 4 (four calls per level, disjoint prefixes). Each must return the
expected JSON and the same identifier. Select the smallest tested concurrency
within 10% of the highest passing measured throughput. An error or model mismatch
stops the benchmark; do not infer topology or benchmark speed from outcomes.
The selected count is written before the 208 new jobs start. The synthetic
transport calls are disclosed separately, not counted as task episodes.
