# Post-review Addendum

Date: 2026-10-10

Status: **post-review additions. This addendum does not change any reported
number, any frozen method parameter, any accepted episode, or either original
preregistered decision.** All outputs from Part A are supplementary and are
written below `reports/main/post_review/`.

## Scope and common constraints

Part A is deterministic and makes zero LLM calls. It may read the pinned
WebCraft evaluator only for reporting/auditing and may replay saved action
logs, but it does not rerun a previously reported episode. Existing methods,
reader modes, prompts, planner, execution shell, action budget, seeds, and
method parameters remain unchanged. Any new `oracle_reading` result is clearly
marked post-review supplementary and never replaces `all_public` or
`llm_only`.

## A1. Oracle-reading evidence upper bound

`oracle_reading` supplies a contract-v2 public projection of evaluator gold
claims. It removes truth and evaluator-only fields while retaining only the
six public search fields and the eleven permitted claim-cache fields. Each
required mechanism receives all associated claims. For a conflict, both sides
appear only when both are actually present under the specified corpus
condition. The frozen methods run on the existing 11-cell main matrix at seed
`20261006`; no LLM call or fallback extractor is permitted.

The report includes the same SR, HTR_commit, HTR_all, probe count, probe cost,
and search summaries as `all_public`, plus compiler closure and the number of
two-sided tasks. The output is a separate upper-bound result, not an update to
the primary result.

## A2. IAA replay responder

IAA is evaluated by deterministic replay of existing logs from
`all_public`, `llm_only`, and `oracle_reading`. A compiled positive assertion
answers `supported`; an executed falsification or compiled negative assertion
answers `unsupported`; otherwise the answer is `unknown`. Probe-then methods
may additionally use their saved public probe results and saved temporal
filter/report-version events. Reports give accuracy and unknown rate, with
the exact query inventory and denominators.

## A3. Identification cost

For Probe-then methods, the saved `report_version` event identifies the
version-recognition time. IC is recomputed from saved events as cumulative
primary cost at the final correct report; distributions and missing cases are
reported. No episode is rerun.

## A4. Behavioral probe classification

A semantic action is a probe when it adjudicates a DV claim and that claim has
not already been depended upon by any executed plan step. This deterministic
classification is applied to saved action traces for every reported method and
reading mode. It produces behavioral HTR_commit, HTR_all, and a count of
disagreements with saved self-reported `intent=probe` labels. The original
self-reported metric remains unchanged and is reported alongside it.

## A5. Aggregation-rule consistency

The fixed all-public candidate audit compares Naive RAG, Majority RAG, and
TruthFinder selections on every required-mechanism/evidence instance. Candidate
identity, payload divergence, and whether selected candidates agree are
reported without changing any aggregation rule.

## B. External-agent baseline, pending author budget confirmation

Part B cannot start until the author confirms its LLM budget. Its frozen
submatrix is 22 conflict-bearing evaluation tasks x five single-world cells
(`matched_1.16.5`, `matched_1.19`, `matched_1.21.11`,
`mismatched_old2new`, `mismatched_new2old`) x seed `20261006`, using the
`all_public` search index. Agents read returned search text directly and do
not access the claims cache, compiler, version strings, candidate-version
lists, truth, or hidden state. Each episode has a 90-action budget, a 40 LLM
call limit, and records model fields per call; a canary is issued every 200
calls.

The shared loop/tool descriptions and committed prompt templates define the
following order: Direct LLM, ReAct, Reflexion, ADAM adaptation, XENON
adaptation, and optional Voyager adaptation. Direct LLM first runs ten
episodes to estimate calls and cost. Reflexion is within-episode only because
the submatrix has one episode per task. ADAM marks check-name, one-step mine,
and one-step craft interventions as `intent=probe`; XENON maintains a task
dependency graph; Voyager is optional and uses only within-episode macros.

Each completed method exports SR, behavioral HTR_commit, HTR_all, false
abstention rate, execution-failure rate, probe cost, mean LLM calls, and mean
tokens. The seven frozen retrieval/planning methods are sliced from the main
matrix on the same submatrix for comparison. The external sequence stops after
ten failures among its 22 episodes; methods already completed are still
reported.

## External runner repair record (before any accepted Part B result)

The author has authorized an unlimited monetary budget and continued repair
with canary protection. The endpoint/model remain the author-selected
OpenAI-compatible gateway and `glm-5.2`. Unit prices have not been supplied;
currency costs are recorded as null/unpriced with token counts retained.

The preceding phrase "ten failures among its 22 episodes" was a transcription
error: the author's "10/22" is a calendar deadline, not a result-dependent
failure quota. That quota is removed. No completed scientific result is
replaced by this correction. Earlier external runs were interface diagnostics.

The repaired shared transport validates an exact harmless JSON response before
each run and after 200 non-canary requests. Failed validation opens a circuit;
the task request is not sent. Model identity mismatches, transport failures and
canary failures propagate out of the agent loop and stop the whole run. An
explicit new preflight is required to recover. Aborted episodes are saved in
an audit file, not assigned an SR value or included in method failure counts.

External actions have exact parameter names and validated types. Boolean
integers and malformed layouts are rejected before execution. Each attempted
LLM decision, including invalid output, counts toward the 40-call allowance.
Three consecutive invalid outputs stop the diagnostic run; a valid action
resets that counter. The 90-action limit uses semantic primary cost (including
the requested smelting quantity). JSON-object output and a 4096-completion-token
allowance are identical across methods. Error diagnostics contain fixed codes,
finish reason, token counts and visible-content length, never credentials or
raw model responses. Earlier assertions that optional thought fields caused
the failures were not supported by the then-available logs.

Run directories must be empty; old diagnostics are preserved. Each run stores
the exact job list, code commit, pin hash, elapsed time and process peak RSS.
The resource gate is planned memory plus 2 GiB. WebCraft remains read-only;
frozen inference and main-experiment results are unchanged.

The first instrumented diagnostic identified `finish_reason=length`, 4096
reasoning tokens and zero visible characters. The external shared runner now
requests `thinking.type=disabled`, as documented for GLM-5.2 at
https://docs.z.ai/guides/capabilities/thinking-mode . This setting must be
checked against the selected proxy using a canary before any new pilot. It is
recorded in request ledgers and manifests and applied equally to all external
methods. Frozen method call sites retain their original request defaults.

The proxy returned HTTP 429 on the second non-thinking task request. External
requests now have a shared ten-second gap after each completed request,
including canaries. HTTP errors stop the run without automatic retries and
record status plus a numeric Retry-After value when supplied by the provider.

## Author-authorized credential failover

The author subsequently authorized using the supplied alternative credentials
after HTTP 429. `WC_LLM_API_KEYS` is an in-memory JSON credential pool; nothing
persists the credentials. Request ledgers contain only one-based slot numbers.
Only HTTP 429 permits a switch. The affected key is quarantined for the current
run; it is not cycled back. Before resending the unchanged task request through
another key, that key must pass the exact model/JSON canary. The environment is
not reset and no semantic action is replayed. Authentication errors, model
mismatches and malformed canary responses still stop the run. If all keys
receive 429, the run stops. Endpoint and model remain unchanged (`glm-5.2`).
This supersedes the preceding no-retry policy only for the authorized 429
credential failover; all attempts and validation events remain auditable.

The bounded ten-episode Direct pilot exhausted all four credential slots after
seven completed episodes. A fixed `--offset` may select the contiguous
uncompleted suffix in a fresh shard after cooldown; it must not skip or select
jobs based on results. The shard manifests record offset and exact ordered
jobs. The continuation uses a 15-second interval and does not rerun the seven
completed episodes.

Parallel execution is permitted only as deterministic contiguous shards with
one transient credential per worker. Each worker has its own simulator
sessions, output directory, ledger, request interval and startup/periodic
canaries. The parent stores credential slot numbers only. It merges episodes
only when every worker completes with the exact requested cardinality. HTTP
429 is the sole recoverable worker exit: the unfinished episode is excluded,
the accepted fixed prefix is retained, and that credential resumes the exact
contiguous suffix in a fresh simulator after a 600-second cooldown. If another
worker has completed its shard, its now-idle credential may take the suffix;
each attempt records only the credential slot number and must pass a fresh
canary. Other
worker failures terminate the active workers and leave the batch unmerged. Four
workers reserve 256 MiB each plus the author-authorized 2 GiB system reserve.
Workers remain `nice 15`, single-threaded for BLAS and do not use a GPU. Worker
starts are staggered by five seconds to avoid a simultaneous four-canary burst;
each key retains the same 15-second per-request interval.

The author changed the external-baseline resource authorization to a **total
effective-memory launch threshold of 1.8 GiB**. This supersedes the preceding
2 GiB reserve only for tests and the post-review external baseline. The parent
checks the 1.8 GiB threshold once for the four-worker batch; each child then
checks its own 256 MiB plan rather than redundantly reserving another 1.8 GiB.
Peak RSS and effective memory before/after remain mandatory outputs. This
resource-only change does not alter prompts, tasks, model, methods or metrics.

After a non-429 transport stop, a committed JSON chunk plan may resume only
the exact uncompleted suffix of each original contiguous shard. It records the
source run, reason, offsets, counts and SHA-256; chunks must not overlap. The
accepted prefixes remain immutable and are merged only after exact global job
coverage is verified. This is recovery from an infrastructure stop, not task
selection or result-based replacement.

Operational provider addition after external evaluation began: the author
supplied fifth and sixth credentials for SiliconFlow, with the latter assigned
to worker 1 in the next continuation. Its official OpenAI-compatible endpoint
is used only with the exact provider model ID `zai-org/GLM-5.2`, after a zero-
episode canary preflight for each credential. The scheduler records the
endpoint and exact model ID for every credential slot and every episode retains
its returned model field. This can improve availability but does not erase the
serving-provider difference; exports must preserve that provenance. No agent,
prompt, task, budget, action or scoring rule changes.

External metrics are computed only after `finish_episode`, from the immutable
schema-v2 public action log plus the evaluator conflict table. Actions are
matched to DV assertions by public semantic arguments (item name, resource
node type, crafting layout, or smelting input). Behavioral probe labels use
the same A4 first-adjudication rule as the deterministic replay; declared
intent is retained separately. The scorer exports behavioral HTR_commit,
HTR_all, declared-intent HTR_commit, false abstention, execution failure,
probe count/cost, search count, and matched conflict IDs. No evaluator field
is returned to the agent or included in a later prompt.
