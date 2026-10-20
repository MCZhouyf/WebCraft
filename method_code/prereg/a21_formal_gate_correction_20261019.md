# A21 author-approved correction: gate belongs to formal K

Formation: 2026-10-19 UTC, after the author explicitly selected:
"按原指令更正门槛位置，再执行正式 K（推荐）".
Both development rounds (2/8 each) are known; no formal model episode has
run. This amendment is committed before any formal request. It does not
rewrite previous registrations, results, gate files, or the discrepancy report.

## Correction

The original author instruction requires development executability checks,
then the numerical [0.40, 0.80] gate on formal K before controls. The earlier
implementation mistakenly used the same interval as a development entry
requirement. That additional requirement is withdrawn by explicit author
adjudication, not silently bypassed or represented as having passed.

Formal entry now requires eight completed, input-matched original development
episodes, valid public API log boundaries and submissions, valid JSON request
audits, the exact model identifier, unchanged prompt and material inputs, and
the original call/action budgets. No SR threshold is applied to development.
The technical proof is stored separately in development_executability.json.
The development shortening run remains a separate historical diagnostic and
is not relabelled as a formal shortening experiment.

## Fixed Formal Scope

Use exactly the existing 32 frozen instances. Manifest SHA256:
`0b31fb38861d1152c8059c8bba3dcd90ff566e42978e58ec6eefbce2038f5e78`.
No task, quantity, inventory, fact, source world, or comparison world changes.
The existing oracle-budget90 check found zero label/cost disagreements:
20 target worlds feasible, 12 infeasible. Recorded feasible costs are 5--10.
The actual frozen family IDs define seven clusters, as already corrected.

Run only K in the present continuation scope. All 32 episodes must complete
before calculating the unchanged gate: feasible-task SR in [0.40, 0.80],
denominator 20 (8--16 feasible successes inclusive). Correct infeasible
submissions do not inflate that denominator. No early stopping from partial
performance, and no control results are generated or inspected before the
gate. Shared HTTP-429 waiting, six-worker authorization, exact model lock,
90-action and 40-decision budgets, prompt, decoding, scoring and bootstrap
settings remain unchanged. No active unrelated process or GPU is touched.

This launch explicitly uses controller scope formal_K, exports the completed
K results and gate, and stops for reporting. If a later scope authorizes
controls, they still require a passed formal K gate. A failed formal K gate
does not launch controls or silently recycle a development retry as a formal
retry; any required formal shortening needs its own fixed material definition
before model requests. No automatic task replacement or third development run.

## Limitations Retained

This amendment corrects eligibility placement only. It does not certify that
oracle-cost validation proves every structural mechanism-chain requirement,
nor fix flattened recipe facts or initial-only knowledge retention. The
frozen bundle and these limitations must be reported with the K results;
the development-only data do not establish a formal MDS failure. The old
closeout documents describe the pre-amendment state and remain unchanged.
