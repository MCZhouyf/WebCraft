# Qwen second-family concurrency amendment

Date: 2026-10-15 UTC. This operational amendment was written after three
serial Direct episodes had completed and before their outcomes were scored or
inspected. The only inspected serial quantities were request counts, latency,
returned model IDs, and completion status. The author explicitly authorized
archiving and abandoning the incomplete serial segment, logging into the RTX
4090 host, inspecting and modifying the SGLang configuration, restarting the
service, and running a concurrency benchmark.

## Reason and preserved evidence

The existing API reported `tp_size=2`, `dp_size=1`,
`max_running_requests=1`, and disabled DP attention. The first 112 ordinary
Direct calls averaged 15.71 seconds, so client-side concurrency against that
configuration could only queue requests. The raw partial run is preserved in
`reports/main/post_review/second_family_qwen/direct_serial_partial_20261015/`
with a SHA-256 manifest and is excluded from formal analysis. The concurrent
Direct segment will restart all 110 instances from the beginning; serial and
concurrent observations will never be combined.

## Permitted service change

Inspection is limited to service topology, launch configuration, model memory
format, GPU memory, and health. Credentials, endpoint addresses, model paths,
and process command lines must not enter committed files or study logs. Model
weights, quantization, tokenizer, chat template, reasoning setting, prompts,
temperature, action/call budgets, and served model alias remain unchanged.

The service configuration is selected before the benchmark:

1. Use two data-parallel replicas only if the unchanged model demonstrably
   fits on one RTX 4090 per replica. Each replica receives six concurrent
   requests through one router or two fixed worker pools.
2. Otherwise retain tensor parallelism over both GPUs and raise only the
   running-request limit to 12 so SGLang continuous batching can operate.

No alternative model, lower precision, changed context limit, or modified
sampling behavior is allowed merely to meet the time target.

## Fixed benchmark and gate

After restart, health and returned model ID are checked first. The benchmark
uses only fixed synthetic canary prompts, never task text, task metadata,
truth, candidate versions, or study outputs. It tests concurrency 1, 2, 4, 8,
and 12 in that order with the same request set and no retries. For each level it
records completed/error counts, aggregate requests per second, latency
quantiles, usage-envelope validity, and returned model IDs. Any error, model ID
other than `coder390`, malformed usage, or mixed model ID stops the benchmark.

The five-hour execution plan is accepted only if concurrency 12 sustains at
least 0.60 completed requests/second with zero errors. Otherwise the measured
throughput determines the revised ETA and no unsupported five-hour claim is
made.

Before the first benchmark request, the fixed load is specified as 24 requests
at each concurrency level: six requests in each of four input-size bands based
only on the archived ledger's token-count distribution (approximately 800,
3,500, 5,600, and 8,700 input tokens). Deterministic seed 20261015 generates
unique, semantically irrelevant filler after a common neutral instruction, so
the test neither copies study text nor consists entirely of cache-identical
prompts. Each request asks for a valid JSON object with approximately 80 output
tokens, matching the archived mean output length of 78 tokens, and caps output
at 256 tokens. Every concurrency level uses the identical ordered request set.

## Prospective concurrent rerun

If the gate passes, Direct is rerun from all 110 instances using fixed,
disjoint episode shards. An episode remains sequential and stateful; only
different episodes execute concurrently. Outputs are written per worker and
merged by original public-job order. One coordinator enforces the registered
canary schedule, returned-model consistency, global stop-on-error behavior,
and duplicate/missing-instance checks. Direct is scored and committed before
the oracle-knowledge segment begins. The oracle-knowledge segment uses the
same selected concurrency and receives its own preregistration and immutable
output directory.

## Post-attempt service-benchmark amendment

The first benchmark attempt stopped at concurrency 1 because a model-authored
JSON string was not closed. It never reached a concurrent level, and its
in-memory level accumulator preserved no per-call rows. This outcome tests
instruction/format adherence rather than serving concurrency, so it cannot
answer the operational question. The failure is permanently recorded and the
same protocol is not retried.

Before any second-attempt request, a separate serving-throughput benchmark is
registered. It retains seed 20261015, the same 24 unique synthetic inputs, the
same four input-size bands, the same concurrency levels, temperature zero,
disabled thinking, and a 256-token cap. The output instruction changes to
plain text with approximately 80 tokens; content formatting is not scored.
Each completed call is appended and fsynced immediately. The validity checks
are HTTP success, nonempty text, complete integer usage, finish reason, and the
exact returned model ID `coder390`. Transport, envelope, usage, model-ID, or
finish-reason failure stops the attempt without retry. The acceptance gate
remains zero errors and at least 0.60 completed requests/second at concurrency
12.

## Post-attempt finish-reason amendment

The second attempt completed concurrency 1 and received all 24 responses at
concurrency 2, but stopped because one response had `finish_reason=length`.
There were no transport, model-ID, usage, OOM, or service errors. The frozen
Direct runner does not reject a task response solely because it reaches the
token cap, so the service benchmark's `stop`-only check was stricter than the
runtime it was intended to model. This failure is retained and attempt 2 is not
retried.

Before any third-attempt request, attempt 3 is registered with the identical
seed, synthetic inputs, plain-text output instruction, token cap, and request
count. It tests concurrency 2, 4, 8, and 12; concurrency 1 is not rerun because
attempt 2 already completed that level. The only changed validity rule accepts
`finish_reason` in `{stop, length}`, matching the frozen Direct task-call
behavior. All other stop rules and the 0.60 requests/second gate remain fixed.

## Formal-shape concurrency correction

The first formal 12-worker Direct attempt stopped with zero complete episodes:
35 calls succeeded and 5 in-flight calls reported endpoint-unreachable while
the service remained healthy and was not OOM-killed. The prior acceptance
benchmark used a 256-token cap without JSON response mode, whereas the frozen
Direct runtime uses a 4,096-token cap and JSON-object response mode. The
acceptance gate therefore did not characterize the formal request shape. This
is an operational benchmark-design error, not a study result; the failed run
is archived and excluded, and no outcome content was inspected.

Before another formal call, a fourth service benchmark is registered. It uses
only deterministic synthetic text, JSON-object response mode, a 4,096-token
cap, disabled thinking, temperature zero, and a 900-second diagnostic timeout.
It runs 12 requests at each client concurrency in the fixed order 2, 4, 6, 8,
10, 12. Inputs comprise three requests in each of approximately 500, 3,500,
6,000, and 12,000-token bands. Content formatting is not scored; valid service
responses require HTTP success, nonempty content, integer usage, and exact
model ID. Both `stop` and `length` finish reasons are accepted. Each call is
fsynced immediately and no failed level is retried.

The selected formal concurrency is the highest tested level with zero errors
and P95 latency below 200 seconds, leaving margin under the frozen 240-second
transport timeout. If no level qualifies, the concurrent formal rerun does not
restart. This correction changes no task, prompt, model, sampling setting,
action/call budget, transport timeout, or scoring rule.

## Disjoint-input concurrency correction

Benchmark 4 completed all levels without a service error, but it reused the
same 12 prompts at every level. Its cold concurrency-2 level took 228.40
seconds (0.0525 requests/second), whereas the immediately following levels
returned the same long inputs in 2.8--3.7 seconds per request. Repeated
response hashes and unchanged input-token totals confirm that the later
levels benefited from SGLang prefix/radix caching. Benchmark 4 is retained as
an operational observation but cannot select the formal concurrency.

Before another formal task call, benchmark 5 is registered. It preserves the
formal request envelope from benchmark 4: JSON-object response mode, a
4,096-token cap, disabled thinking, temperature zero, the same four input-size
bands, 12 requests per level, and a 900-second diagnostic timeout. It tests
client concurrency 2, 4, 6, 8, 10, and 12 in that order. Each level receives a
disjoint, deterministically generated request set: the level and request
identifier occur at the start of the user message and the neutral filler is
generated from a level-specific seed. No complete user-message prefix or
filler sequence is reused across levels. The common system instruction is
retained because that shared prefix is also present in formal calls.

Each call is appended and fsynced immediately, with no retry. HTTP, envelope,
usage, finish-reason, or returned-model failure stops the benchmark. No server
cache is flushed or otherwise modified. The selected formal concurrency is
the highest tested level with zero errors and P95 latency below 200 seconds;
ties in admissibility are broken by aggregate throughput. Benchmark 5 results
replace benchmark 4 only for this operational selection and do not alter any
study method or outcome.

## Selected formal concurrency

Benchmark 5 completed all 72 disjoint synthetic calls with no error and only
the required model ID. Concurrency 10 is selected prospectively for the next
formal Direct attempt: it completed 12/12 calls at 0.0547 requests/second with
P95 latency 139.03 seconds. Concurrency 12 is rejected by the registered gate
because its P95 latency was 217.88 seconds, leaving insufficient margin under
the frozen 240-second task transport timeout. The formal run restarts all 110
instances with ten fixed episode shards; no prior partial episode or response
is reused.
