# Second-family Qwen replication: protocol draft

Author instruction date: 2026-10-14. Recorded: 2026-10-15 UTC.
Status: MOTIVATION_RELEASED_AFTER_THIS_COMMIT; DIRECT/CONTROL INPUT LOCKS PENDING.

At the initial check, the inherited environment and two project environment files were checked
without printing configuration values. No dedicated Qwen configuration was
found; the existing WebCraft configuration selects GLM. No endpoint was called.
At that time, the server model identifier was UNOBSERVED. The transport/model identity lock
must be completed and committed before any generation. This draft is not a
claim that the identity-dependent preregistration is complete.

## Read-only identity discovery, 2026-10-15

Following the author's instruction to use the supplied jump-host access, an
SSH direct TCP channel (no remote shell command) reached the existing service.
Both `/v1/models` and `/get_model_info` returned HTTP 200. The advertised model
ID and `served_model_name` are `coder390`; `model_type` is `qwen3_5` and
`architectures` is `["Qwen3_5ForConditionalGeneration"]`. These are service
metadata, not a generation response or verification of underlying weights.
The 27B size is author-provided; it was not exposed by these API responses.
Future requests must use `coder390` and require the same response model ID.
No need for another copy of the author's access credentials was established.

Credentials were consumed from the already supplied message via memory-only
storage, not copied into code, persistent key files, or reports. Connections
were closed after the read-only checks. No service startup, remote shell
operation, model-directory access, or generation occurred. Endpoint addresses
and credentials are intentionally omitted. Runtime package and prompt checks
below remain required before the first generation; no experiment is released
by these metadata checks alone.

## Safety and release gate

Use only the author's existing endpoint, configured through environment
variables. Do not persist its address or credentials. Do not run the supplied
tunnel bootstrap, access model-service directories, or start/stop/restart any
service. Unreachable endpoint means stop and report. No local GPU computation.
Protect all non-task processes. Perform a read-only model-list check only once
the dedicated configuration is identified. Record the actual returned model
identifier, not an inferred family from an alias, and commit the identity lock
before generation. Every response records its model identifier; a missing,
unexpected, or mixed identifier stops the segment. Do not substitute a model.

At agent runtime, only existing public manifests, prompts, observations, and
approved reading rows are available: no truth files, private task metadata, or
candidate-version list. Gold scoring is separate from agent execution. The
motivation study uses the exact registered prompts (including the single
explicit version in each version-conditioned question), not a candidate list.
The gold-knowledge control uses the existing preprojected public input package,
not runtime access to truth or evaluator metadata.

## Fixed scope and interpretations

All judgments are family-level replication, never cross-model ranking. No
expansion or tuning on evaluation tasks. Run these segments sequentially and
commit each segment's exports before proceeding.

1. Motivation protocol II: 51 assertions, four independent questions each,
   temperature 0, byte-identical GLM messages, 204 ordinary requests and the
   existing integrity canary. **M1:** stale default replicates iff the stale
   count is greater than half of 51 (GLM reference 26/51). **M2:** report exact
   fully correct three-version conditionalization count / 51 alongside GLM
   10/51; no additional pass threshold is invented.
2. Direct LLM: the existing 110-instance submatrix, identical prompts and
   reading interface, 90 semantic actions and at most 40 model calls/episode.
   **D1:** feasible-task SR <= 10% (GLM 0/72). **D2:** report HTR_all and
   consequential HTR_commit^c separately; compare HTR_all to the GLM
   .155-.209 historical range without treating the two metrics as equivalent.
3. Direct LLM oracle_knowledge: identical external_controls setting a, 110
   instances and the same budgets. **K1:** feasible-task SR < 50% (GLM 1.4%).
   **K2:** report HTR_all alongside GLM .382; do not infer an internal cognitive
   cause from that rate alone or invent an extra numerical threshold.

Optional ReAct is not scheduled while configuration is blocked. Report it as
not run unless explicitly scheduled before its first call. All historical
numbers above are author-specified comparators, not newly verified Qwen data.
The approximate call estimates in the instruction are not completed counts.

## Existing protocol provenance

Paths prefixed `arbiter/` refer to the adjacent read-only repository.

| Source | SHA-256 |
|---|---|
| prereg/motivation_multimodel_20261011.md | cb5d7cb5b5dd92f74bedf47b0f4ff37042200f77977cbc78becffb3b7bec0a97 |
| corpus/motivation_multimodel/common.py | 084af5f7d98129d49853284c18fdfb2b925ca053623283effdf921f80136b349 |
| corpus/motivation_multimodel/score.py | 2c535c8a9d0e4cb9bd494ba577a2d2e2d360e575baf7ffc8e4fa565cd7673cd7 |
| motivation scoring-rule canonical hash | c0af5749ddd9cb3c5a183d40d3dbbee1b7a6cb9893222afcac34a94748244716 |
| arbiter/prereg/external_rescore_20261010.md | 25dd3d8f386b7d9be711a93c1ba51ec76e8f5393d88cfc2d170f9e5cbfebd4e4 |
| arbiter/prereg/external_controls_20261013.md | 457caa7920ffa6d95211c1f4c146c668939ccc5187606066026890845fe26932 |
| arbiter/src/arbiter/evaluation/external_agents.py | 37c96dbb0eeefb749d38153ccf9c5916c6441f6412b264a92bccfe43c6064734 |

The direct_llm system-prompt hash registered in the earlier external protocol
is eb39e819b15453aa8b2d8a811b627a1a0d6bc3e3b93307b87637bba59f94a352
(873 bytes). Before release, additionally verify complete serialized messages,
public runtime packages, and the consequential-rescore implementation against
their original artifacts. These runtime checks have not been performed here.

## Zero-call attachment and outputs

Group existing corrected-success and execution-failure transition exports by
task-ID prefix con/eff/fea/pla. Report degraded, retained, recovered separately;
do not count recovery as part of the original 106 successes. Require unique
task-condition-world keys and the instructed totals 54/52/6, otherwise fail.
This attachment requires no model endpoint and does not read truth/task files.

Outputs: reports/main/post_review/second_family_qwen/. Until release, REPORT.md
must explicitly mark M1/M2/D1/D2/K1/K2 unassessed, model identifier unobserved,
and model calls zero; do not manufacture a populated model-results main table.

## Prospective execution lock, 2026-10-15

This section supersedes the initial configuration block for segment 1 only.
No Qwen generation response has been observed when this section is committed.
The required request and returned model ID is `coder390`, reporting the Qwen3.5
architecture; 27B remains author-provided, not weight-verified. Endpoints and
credentials are configured only in the launch process environment from the
author's already supplied connection data. Each request uses SSH direct TCP
forwarding to the existing service, with no remote command or service restart.

The public motivation input contains exactly 204 message pairs. Reconstructing
each historical GLM request (including its original disabled-thinking field)
matches all 204 stored request hashes. Only the requested model identifier and
provider-specific thinking-control field change. Temperature remains zero,
ordinary max_tokens is 160 and canary max_tokens is 64. SGLang non-thinking
transport uses `chat_template_kwargs: {enable_thinking: false}`, documented at
https://github.com/sgl-project/sglang/blob/main/docs_new/cookbook/autoregressive/Qwen/Qwen3.5.mdx.

Run the existing fixed motivation canary once before study calls to validate
this newly accessed service and once after ordinary call 200. This adds one
explicit non-study preflight to the historical protocol: 204 study calls plus
2 canaries, 206 successful calls if completed. All attempted requests and all
returned model identifiers are recorded, including a failed check. No transport
retry, prompt repair, endpoint substitution, or model substitution is allowed.
The hard stop applies to unavailable endpoints, wrong/missing model identifier,
missing usage, or canary failure. Malformed ordinary study answers remain scored
as unknown/manual-review candidates under the existing scoring rules, not retried.

The SHA-256 execution lock is
`reports/main/post_review/second_family_qwen/execution_lock.json`. The runtime
reads only the projected prompt file; an audit hook forbids truth, private task,
conflict, motivation-label, and conversation-history file access after transport
initialization. The key exists only in anonymous RAM-backed storage and is
closed on exit. The CPU runner uses nice 15, CUDA disabled, and requires 512 MiB
planned plus 8 GiB effective free memory. No other process is modified.
Direct/control reuse remains gated on separate
public-package and prompt locks before their first calls. Optional ReAct is not
scheduled; complete the three mandatory segments first without expanding scope.
