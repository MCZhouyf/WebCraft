# Amendment: same-model robustness replication, 2026-10-11

This amendment was authorized after commit `74bf5a9` and after the preregistered
Anthropic canary stopped with HTTP 402. That attempt produced zero accepted
model calls, zero task episodes, zero tokens, and zero cost. It remains in the
audit trail and is not scored.

The author cancels the different-supplier requirement. The following terms
replace conflicting model/transport language in
`external_second_model_probe_20261011.md`; all scope, prompts, budgets, sample,
gold scoring, bootstrap, judgments, and gate thresholds remain unchanged.

## Replacement model and interpretation

- Exact requested model ID: `glm-5.2`.
- Endpoint family: the previously validated OpenAI-compatible Sensenova
  endpoint, supplied at runtime through `WC_LLM_BASE_URL`.
- Credential: exactly one key supplied only through `WC_LLM_API_KEY`. It is
  never persisted in code, configuration, manifests, logs, or reports.
- Temperature 0.0, JSON-object mode, thinking disabled, and 4096 maximum
  completion tokens remain identical to the first round.
- The server-returned model identifier must equal `glm-5.2`; any mismatch is a
  non-retryable protocol stop. Consequently mixed identifiers cannot enter a
  method's result.

Because this is the same requested model as the first round, this run is an
independent same-model robustness replication. It cannot support a claim of
cross-supplier or cross-model-family generalization. "Second model" in output
paths is retained only for compatibility with the already committed scripts.

## Frozen sample and runtime

The selection algorithm and 150 identities are unchanged. The manifest model
label is updated to `glm-5.2`:

- `prereg/external_second_model_sample_20261011.json`
- SHA-256: `d5e4e3e8ea652f8ce1d76e62abffd24f366fdf5ba04c6365e50e5b0082625d13`

The prompt implementation remains unchanged at SHA-256
`37c96dbb0eeefb749d38153ccf9c5916c6441f6412b264a92bccfe43c6064734`.
The OpenAI-compatible transport uses the repository's existing safety checks,
plus single-credential accounting. Only transport, timeout, HTTP 408/429, and
provider 5xx failures may consume the two retry opportunities per episode.
Authentication, budget, canary, model-identity, protocol, refusal, and model
output failures are not retried as infrastructure.

Actual monetary cost remains unknown unless the runtime provider supplies
prices; unknown cost must not be recorded as zero. Calls and tokens, including
canaries, are always reported.

No task result existed when this amendment was written. No frozen method,
prompt, action budget, call budget, scoring rule, judgment threshold, or gate
threshold is changed.
