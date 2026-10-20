# Final ARBITER development method

This method was finalized after engineering selection on the frozen eight-task
development set. Its results are post-selection development measurements, not
held-out or confirmatory estimates. No evaluation task was read or executed.

The final agent uses public cached claims only. It compiles claims through the
shared schema compiler, maintains the context-mixture belief, enumerates five
candidate plans, and replans after every physical action. Only an executable
first step can be selected as a probe. Probe value combines the original plan
VOI with binary decision-loss reduction `min(p, 1-p) * 10 - action_cost`.
Failed concrete actions are blocked by their action signature, while explicit
resource failures do not update context belief. Ingredient-family resolution
is inventory-aware for every method using the shared planner.

Frozen execution parameters:

- candidate plans: 5
- uncertainty weight: 0.35
- decision-error cost: 10
- explicit-probe cap: 12 per episode
- physical-action/primary-cost cap: 90
- retrieval: reader v5, k=10, at most 60 searches

The primary development criterion remains `HTR_commit`; `HTR_all` is always
reported to expose probe actions that exercise stale mechanisms. Probe intent
is fixed before execution and every probe retains its full primary cost.
