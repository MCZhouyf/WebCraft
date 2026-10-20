# A21 registered control-phase continuation

Recorded after formal K completion, before any control-condition model request.
The author asks to proceed according to the experiment requirements after the
gate. The original A21 instruction specifies all four conditions once formal
K meets the inclusive [0.40, 0.80] feasible-success gate. That gate passed at
8/20 = 0.40; all 32 K records and exports are archived in commit `1f4caaa`.
The previous K-only launch was a reporting checkpoint, not completion of A21.

## Scope and execution

Resume the same frozen 32 instances under K+S, K+N and empty: exactly 96 new
episodes. Reuse the 32 completed K records, with no K or development reruns.
No tasks, facts, inventories, prompts, decoding, model, score definitions or
numerical gate are changed in response to the K results. The formal manifest
SHA256 remains
`0b31fb38861d1152c8059c8bba3dcd90ff566e42978e58ec6eefbce2038f5e78`.

Use `glm-5.2`, six authorized workers, the existing shared 20-second HTTP-start
spacing, 40 decisions and 90 semantic action cost per episode. Returned model
identity and canaries remain enforced. HTTP 429 waits and retries the same
request under the existing shared cooldown; protected identity/content errors
stop the run. Credentials remain in process memory/environment only.

Command:

```bash
PYTHONPATH=src:.:scripts python scripts/continue_a21_mds.py --detach --through all
```

WebCraft is read only, server.lock is shared, process priority is nice 15,
available-memory minimum is 1.8 GiB, and no GPU or unrelated process is used.
Nothing is pushed remotely. The K-only exports remain frozen; the controller
skips their regeneration on continuation, preventing control costs from being
added to the K-only ledger summary. Combined exports go to the existing
parent A21 report directory after all 128 episodes complete.

## Registered analysis

- K+S minus K+N: SR, stale-specific contact (query/action split), v3-d propagation.
- Empty minus K: SR, with feasible-task SR reported separately.
- K+S versus K: first semantic action divergence, descriptive only.
- Paired mechanism-family bootstrap: 10,000 draws, seed 20261018, seven actual
  frozen family IDs as already corrected before formal execution.

All outcomes are retained regardless of direction. Existing flattened-fact,
initial-only knowledge-retention and structural-chain limitations continue to
apply. No claim that K is continuously knowledge-sufficient is introduced.
