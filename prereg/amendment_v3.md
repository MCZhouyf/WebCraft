# Preregistration Amendment V3: Entity-Grounded Layered Pickup

Author decision: 2026-10-05. V3 work began at `2026-10-06T01:52:11Z` and must stop no later than `2026-10-06T04:52:11Z`. This amendment is prospective: no LLM-agent or evaluated-method result exists.

## Frozen Failure Classification

The six V2 development failures are classified before implementation in `reports/amendment_v3_failure_classification.md` and `.json`: one pickup failure, five other infrastructure failures, zero oracle-plan problems, and zero truth problems. V3 addresses only pickup. It does not modify tasks, oracle plans, truth, public actions, cost accounting, container interaction, or driver timeouts.

## Single Authorized Pickup Scheme

After each successful dig, the harness waits at least 15 game ticks and identifies item entities that actually spawned near the mined block after the dig began. It teleports the bot to the live entity position and polls client inventory for up to three seconds.

If the item is still not collected, the evaluator-owned harness targets that same observed entity with a unique server tag, reads its `Item` NBT through RCON `/data get entity`, parses the item identifier and quantity, kills that entity, and gives exactly that identifier and quantity to the bot. A fallback is forbidden when no actual post-dig entity is observed or its NBT cannot be read. No truth file, expected plan output, recipe table, or block-to-drop mapping may supply the fallback item.

Every fallback is written only to a private action-log field with the entity-derived item identifier and count. The public method parameters, result, item flow, and eight-method API remain unchanged and expose no coordinates, entity identifiers, RCON commands, or fallback metadata. Primary cost remains the semantic-action count.

## Validation And Decision

V3 must rerun 90 leakage checks, three reset hashes in each version, all Python and Node tests, and all 24 development task-world pairs. Evaluation (102 pairs) and B4 may run only if development passes at least 23/24 with no navigation or pickup failure. B3 remains accepted at 90/90 recipes and 30/30 drops.

If the complete validation passes, status becomes `STAGE1_READY_FOR_PILOT`. If it does not pass by the deadline, no second repair is permitted; status becomes `STAGE1_SWITCH_TO_SIMULATOR_CANDIDATE`, accompanied by a one-page simulator-backend assessment.

Pre-live implementation hashes:

| File | SHA-256 |
|---|---|
| `harness/src/semantic_agent.mjs` | `7e05079a5c4c6b794b515fb01e38d199b5e9e06d44ac551da614aa8971ec1be2` |
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` |
| `data/stale_agent_expectations.jsonl` | `2bd2f75ddbdbd6302595cabff29c91e7072d0f97e0e711decfe1ead4e1f4c832` |
| `harness/config/arena.json` | `c53cd4325cc4b6f5d4e352303ff266564e39168645d0a58af27c9001dc38eb87` |

Implementation commit: `05b289b86354bbe6bc97ba896c2a55e4aea523a8`, created `2026-10-06T09:56:01+08:00` before V3 live validation.

## Post-Validation Outcome

B1 passed all 90 leakage probes and all nine reset-hash checks. All 46 Python tests and the Node parser, public-contract, arena-identity, and syntax checks passed before live validation. The targeted `eff_007 / 1.21.11` oracle probe passed all 16 planned actions, collected all five iron drops, and reached the `chest_minecart` goal at the frozen primary cost of 20. No truth-derived pickup fallback was required.

The full development rerun completed 12 task-world pairs: 10 passed and two failed both identical attempts. The failures were `pla_009` in 1.16.5 and 1.19, each at the final `craft_grid` action after successful mining, pickup, and smelting. They are container-interaction infrastructure failures, not navigation or pickup failures. Once both failures were recorded, the maximum possible result was 22/24, below the required 23/24, so the run was stopped immediately. An in-progress `pla_011 / 1.19` attempt was interrupted and is not counted as a completed pair.

Per the prospective decision rule, evaluation and B4 were not started, no second repair was attempted, and the status is `STAGE1_SWITCH_TO_SIMULATOR_CANDIDATE`. The simulator requirements and work estimate are in `reports/SIMULATOR_BACKEND_ASSESSMENT.md`. No LLM-agent result was produced before or during V3.
