# Qwen second-family Direct segment

Date: 2026-10-15 UTC. Committed after the motivation segment export and before
the first Direct study request. This is segment 2 of the parent preregistration;
it does not alter its D1/D2 thresholds or expand the 110-instance matrix.

## Frozen public matrix

The source is the same 110 Direct jobs in ARBITER's frozen first-round sample,
SHA-256 `d5e4e3e8ea652f8ce1d76e62abffd24f366fdf5ba04c6365e50e5b0082625d13`.
The public runtime projection has SHA-256
`7660f89216afa31b97f2076154f7f1f60830a2329b4b395137dc1a077445cf44`.
It replaces task IDs with opaque instance IDs. It contains the hidden world
backend selector, search-condition selector, and only the model-visible public
target and initial inventory. It contains no task ID/type, feasibility, truth,
conflict label, oracle plan/cost, claims cache, or candidate-version list.

The model sees only `public_system_prompt("direct_llm")`, the public task,
public inventory/tool observations, the retained two-step trace, and six-field
search rows. The prompt SHA-256 is
`eb39e819b15453aa8b2d8a811b627a1a0d6bc3e3b93307b87637bba59f94a352`
(873 bytes), identical to the first round. The unchanged action loop enforces
40 model calls, 90 charged semantic actions, temperature zero, JSON-object
responses, and disabled thinking. No candidate versions are named.

## Execution and stopping

One temporary local SSH forward reuses the existing service; no remote command
or model/tunnel service operation is allowed. A fixed external-agent canary runs
before episode 1 and after every 200 ordinary model calls. Every request records
the returned model ID and usage. Any endpoint failure, absent usage, malformed
response envelope, canary failure, or returned model ID other than `coder390`
stops the segment without retry. Invalid model-authored action JSON follows the
unchanged first-round loop: a public schema error is returned, and three invalid
outputs end that episode. This is method behavior, not transport retry.

The deterministic simulator loads its frozen private state during harness setup;
the model-facing adapter never receives it. After setup, a process audit hook
blocks opens of truth, task/conflict metadata, private corpus, scoring map, and
conversation history. Runtime opens only the public job package, public indexes,
simulator API/logs, and model transport. Gold scoring and the opaque-ID mapping
are not loaded until the tunnel is closed and model execution has ended.

The five required index conditions comprise 15 files, frozen by
`direct_index_manifest.json` SHA-256
`c758d14485c0f3f0d46c32102da52ac156005b1b2ec315245a6fd84809c6ed6f`.
The execution lock is `direct_execution_lock.json`. CPU nice 15, CUDA disabled,
1 GiB planned memory plus 8 GiB reserve. Output is a new immutable `direct/`
directory. D1 is feasible-task SR <=10%. D2 reports HTR_all and consequential
HTR_commit^c under the already registered gold rescore/reanalysis protocol.
No method or cross-family ranking is authorized.

