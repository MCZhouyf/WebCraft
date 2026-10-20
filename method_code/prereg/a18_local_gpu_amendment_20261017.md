# A18 local in-process inference amendment

2026-10-17, before model loading or generation. The author explicitly
authorizes this task to use the local GPU. This supersedes only the original
A18 endpoint-only/no-GPU restriction. No remote service or HTTP API is used;
no other process/service is stopped, modified, or restarted. WebCraft and all
prior experiment results stay read-only; no remote push.

## Identity and numeric backend

Use local `Qwen/Qwen3-14B`, revision
`40c069824f4251a91eefaf281ebe4c544efd3e18`, not the remote 27B alias. Freeze
all eight weight-shard SHA256 values, config/tokenizer/generation-config
hashes, package versions, public A18 inputs and hardware metadata in
`prereg/a18_local_model_lock_20261017.json` and commit before loading.
Require Qwen3ForCausalLM architecture. This is a local-model identity, not a
fabricated server-returned identifier; server-model identifiers are NA.

Observed hardware: RTX 3080 Ti, 12,288 MiB total, no compute process at
admission. Use NF4 4-bit weights, double quantization, BF16 compute and
non-quantized parameters, SDPA attention, inference/eval mode, one model copy.
Existing torch 2.1.2+cu121, transformers 4.51.3, bitsandbytes 0.45.5 and
accelerate 1.7.0 are not upgraded. Quantization is an explicitly disclosed
backend difference, not a result-equivalent full-precision model claim.

## Prompt and output contract

The 208 precommitted jobs, 52 instances, visible facts, task projection,
ReAct system/user messages, original bounded-history/knowledge-retention
behavior, 40 action-call and 90 semantic-cost limits are unchanged. Preserve
the A8 independent post-terminal boundary question for call parity; do not
claim repaired BIA. Use the model's frozen chat template with
`enable_thinking=False`, greedy decoding (`do_sample=False`, one beam),
maximum 4,096 new tokens, no prompt truncation, no cross-request KV memory.
Seed 20261017. Full message strings and token counts are persisted.

JSON-object mode is implemented locally with lm-format-enforcer 0.11.3,
interegular 0.3.3, installed into a task-owned vendor directory without
changing shared dependencies. Use a generic object parser, **not** an action-
schema/answer-constraining grammar, via the documented Transformers prefix
callback. Cache only tokenizer prefix data, not episode/model state. Preserve
Unicode labels, filter illegal raw JSON control characters, and validate
outputs with the existing parser. Disclose library-default whitespace/array
heuristics (12 consecutive whitespace characters, 20 array entries) as
transport differences from opaque provider JSON mode. No semantic repair,
extra hint, or changed prompt is introduced.

References: https://huggingface.co/Qwen/Qwen3-14B and
https://github.com/noamgat/lm-format-enforcer . These are public documentation,
not private inference endpoints.

## Resource and stopping contract

Run one sequential inference stream (within the registered concurrency <=2),
nice 15, single CPU threads, and a task-owned GPU lock. Do not launch a server.
Admission requires at least 11 GiB free GPU memory, no foreign compute process
on the selected GPU, and at least 1.8 GiB effective free host memory. Limit
this process's CUDA allocator to 90% of physical memory. Check foreign GPU
occupancy at request boundaries; if contention appears, stop at the boundary
and unload only this task's model. Do not evict another process or model.

An exact-JSON harmless inventory canary precedes episodes and repeats after
every 200 task calls. A failed canary, OOM, changed identity/hash, context
overflow, foreign GPU contention, or generation timeout stops the run and
records an infrastructure abort, never a cognitive failure. No automatic
precision/model/context/token-budget downgrade. Per-generation wall cap is
600 seconds, checked by the generation stopping criterion; a timeout does
not yield a scored answer. No retries of GPU/identity/canary failures.
No request queue exists in this single-stream backend; the prior queue-timeout
rule is inapplicable. No cloud credentials are read or created.

Only previously completed episodes may be used as checkpoints; do not rerun a
saved episode. An explicit infrastructure continuation must record the same
identity and preserve incomplete logs outside the completed metric set.
The original A18 hard cutoff (2026-10-20 00:00 UTC) remains unchanged.

## Analysis and cleanup

Keep A18's registered contrasts and A8 scorers unchanged as the primary
GLM/Qwen comparison. A20 positive-case corrections, if applied later, must
remain separately labeled and symmetric across models, never silently replace
A18's primary metric. No pooling of model families. Report actual successful
local generations, canaries, action/boundary calls, tokens, elapsed time,
client RSS and own CUDA peaks; provider charge is not applicable and hardware
currency cost is unknown. Forecast remains approximately 2,344 task calls;
measure timing on the first registered episodes without choosing tasks by
outcome. No performance gate or task replacement.
Job order is a single Python Random(20261017) shuffle of the entire frozen
208-job list, performed before any task outcome is observed.

On exit release only the task-owned lock/model/temporary state, clear task
environment variables, and record cleanup. Keep immutable model weights and
public experiment logs. No endpoint or credential belongs in any output.
