# A21 saved-decision recovery after minute-limit interruptions

Registered before reusing any interrupted saved response. Four development
episodes are complete. Two interrupted development episodes contain 35 and
36 successful action-generation responses; these are recoverable prefixes,
not task failures. All four remaining instances stay in scope.

For each incomplete instance, choose the longest saved sequence of paired
external_react prompts/responses; ties use the lexicographically last path.
Canary records are excluded from that sequence. Every response must identify
glm-5.2. Reconstruct a clean deterministic public environment with the exact
registered material and replay saved decisions. Each reconstructed prompt,
public observation, and decoding parameter must equal its saved request.
The pending unanswered request, when present, must also match. Any mismatch
stops recovery; it is never repaired by changing task, knowledge, or prompt.

First verify the saved prefixes entirely offline, making zero model calls.
Only verified prefixes may proceed to live completion. A fresh live canary
is still required. The 40-call budget includes all saved decisions; recovery
does not reset it or the 90-action budget. Audit records identify replayed
responses and new generations separately, with source path and SHA256.
Replayed provider usage is historical and is not billed a second time in
ledger totals. Previously completed episodes are retained without replay.

The initial three-worker attempt remains archived as STOPPED_INFRASTRUCTURE.
New recovery attempts use the author-authorized maximum of three workers and
the shared pending queue. Only recently canary-verified primary slots are
selected; minute-limit failures retain the existing protected stop behavior.
