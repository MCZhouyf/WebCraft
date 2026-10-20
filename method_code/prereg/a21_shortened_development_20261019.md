# A21 single shortened development retry

Rule formation: 2026-10-19 UTC, after the first development gate completed
at 2/8 = 0.25 and after the author instructed continuation. This implements
the single shortening retry already required by the original A21 registration.
It is development adaptation, not an untouched confirmatory result. No formal
model episode has run. Original materials, episodes, and gate remain intact.

## Scope And Deterministic Rule

Use exactly the eight frozen `development_materials/manifest.json` instances,
with the same source tasks, target worlds, targets, and quantities. No task
is replaced, and no success/failure label selects an inventory or route.
Only these development materials and their truth-derived oracle routes are
read to construct the retry; no held-out task is read or run for this purpose.

1. Recompute the existing single-threaded MIP oracle from each original
   public inventory with action_limit=90. Require its optimal cost to match
   the previously frozen development oracle table, otherwise stop construction.
2. Retain the final two semantic actions of that route. Require two distinct
   mechanism signatures and a direct consumed-input or required-tool edge
   from the first action's output to the final action. Require the initial
   quantity of that intermediate to be insufficient for the final action.
3. Obtain the prefix inventory by executing the omitted prefix through the
   read-only simulator's public API. Add only positive differences from that
   inventory to the original starting inventory. These explicit material
   bypasses remove the prefix burden without removing original resources.
   No target item may be supplied, and the target must remain unattained.
4. Canonicalize public inventory fields without duplicate item names. Items
   augmented by a bypass are represented in `task_supplements` at their full
   resulting quantity, and removed from `standard_items`; all other initial
   items retain their original quantity. The evaluator separately records
   the added quantity. This avoids ambiguity in the existing inventory loader
   without altering that loader or the agent.
5. Two source tasks already have one-action oracle routes. Keep those tasks,
   their inventories and knowledge unchanged. Disclose them as one-mechanism
   exceptions; do not invent a prerequisite or replace them.
6. Preserve the complete original K fact list byte-for-byte as structured
   content, including order/source/timestamp. No oracle action layout, route
   order, feasibility label, or comparison-world metadata enters the prompt.
7. Independently replan each modified inventory and replay its oracle route
   through the public API. Require optimal cost and action count 2 for the
   six shortened tasks and 1 for the two unchanged tasks. If any assertion
   fails, stop rather than changing the rule to fit that task.

## Locks And Execution

New artifacts use `development_short_materials/`, `development_short_run/`,
and `development_short_gate.json`. New internal instance IDs have a `dev2_`
prefix; original task IDs are retained. Old prompts or decisions cannot be
reused across the inventory change. Partial prefixes from this retry itself
may be verified and resumed by the existing exact-replay protocol.

All eight retry episodes use ReAct, glm-5.2, temperature 0, the unchanged
system prompt, original K facts, eight-step history, 40 decision calls,
90 action budget, and frozen scoring. Up to six independent workers are
allowed. HTTP 429 waits/retries follow the author-authorized amendment.

The gate remains feasible SR in [0.40, 0.80], denominator eight, with no
rounding or excluded tasks. Below 0.40 after this retry stops development;
above 0.80 is out of range and also stops. No third development attempt is
authorized. Only a passing registered gate permits formal K. The separate
formal K gate must still pass before any formal controls are run.

## Disclosure

The original dev oracle costs were 1, 3, 11, 23, 32, 1, 3, 20; these costs
are not all semantic action counts because batched smelting has quantity cost.
The retry is deliberately shorter and is reported separately. It does not
establish that the original long-chain development tasks were solved.
WebCraft is read only; server.lock and memory protection remain effective.
No GPU, unrelated process, endpoint file, or credential file is touched.
