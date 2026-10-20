# Preregistration Amendment V2: Fixed-Station Teleport Execution

Author decision: 2026-10-05. Implemented prospectively on 2026-10-06 UTC, before any LLM-agent or evaluated-method result. This amendment changes only execution transport inside the harness. Task definitions, oracle plans, semantic actions, observations, return values, and primary-cost accounting remain frozen.

## Reason For Amendment

The frozen pathfinder failed broadly during post-registration oracle validation despite complete static arena reachability:

- Development oracle: 23/24 task-world pairs passed; one pair failed both attempts.
- Evaluation oracle: 71/102 task-world pairs passed; 31 pairs across 16 tasks failed both attempts.
- B4: five of 14 attempted tasks failed current-world oracle navigation twice.

Failures appeared in all three Minecraft versions and during both `mine` and workstation-bound actions. The evidence therefore supersedes the earlier bounded inference that `fea_009 / 1.16.5` was task-specific. The pre-amendment evidence is frozen in commit `78cd19b7e7c3c8767dca6af173f21162225a6bd3` and archived under `reports/archive/pre_teleport_amendment_20261006/`.

## No Task Replacement

No task is replaced. The previously proposed `fea_009 -> raw_iron` task-replacement amendment is withdrawn before any LLM-agent result. Its text and generated data are retained for audit under:

- `prereg/archive/amendment_v2_task_replacement_withdrawn_20261006.md`
- `data/archive/withdrawn_task_replacement_20261006/`

The active corpus is restored to the original A6 hashes:

| File | Active SHA-256 |
|---|---|
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` |
| `data/stale_agent_expectations.jsonl` | `2bd2f75ddbdbd6302595cabff29c91e7072d0f97e0e711decfe1ead4e1f4c832` |

`fea_009` therefore retains its original `iron_ore` target and `100` feasibility vector.

## Frozen Teleport Transport

The arena configuration contains one deterministic interaction station for each of 95 resource blocks, the workbench, and the furnace. Every resource also contains a deterministic pickup point at the center of the block after it is mined. Stations are adjacent to their target, have the common stone floor below them, contain air at feet and head after reset, and have direct line of sight to the target. The same coordinates are used in all three versions.

Immediately before `mine`, `craft_grid`, or `smelt` interacts with its target, the evaluator-owned harness sends an RCON `/tp` command for the bot to the corresponding fixed station and waits for client position synchronization. After a successful dig, the harness teleports the bot to that resource's fixed pickup point and waits 20 game ticks for item collection. These actions no longer invoke Mineflayer path planning.

The public eight-method agent interface is unchanged. Teleport commands, stations, pickup points, and coordinates are never returned to the agent. The action-log `net_displacement` field and the nested mining `travel_distance` field are recorded as `not_applicable`. Primary cost remains the preregistered semantic-action count: one for `mine` and `craft_grid`, `n` for `smelt(n)`, and zero for the existing zero-cost methods.

Arena SHA-256 changes from `e796daacbe1639b2630941bc0a13615cfb77fd39245a42dc162a3bc3d87ef4e6` to `c53cd4325cc4b6f5d4e352303ff266564e39168645d0a58af27c9001dc38eb87`. Resource blocks, workstation blocks, floor, bounds, spawn, and starting inventory are unchanged; only fixed internal station/pickup metadata is added.

## Infrastructure-Failure Rule

An episode ending because of teleport synchronization, item pickup, connection loss, or server failure is automatically retried exactly once with the same task, seed, world, method state, interface, and configuration. A second infrastructure failure is reported separately and excluded from SR and HTR denominators. Both attempt logs and hashes are retained.

## Prospective Validation And Stop Rule

Before any pilot, this single repair must pass:

1. 90 public-interface leakage probes and three reset hashes in each of three versions.
2. The 46 Python tests and Node public-contract/arena tests.
3. All eight development and 34 evaluation oracle tasks in all three worlds.
4. B4 current-oracle, stale-policy, and offline-replay validation.

The already-passed B3 audit (90/90 recipes and 30/30 drops) is not rerun. If any navigation or pickup failure remains, validation stops and no alternative repair is attempted. Only complete oracle validation, or isolated non-navigation failures handled by the frozen exclusion rule, permits `STAGE1_READY_FOR_PILOT`.

Implementation commit: `6d9a3a43b156a5daa2d0afbd26fe21f60b72e46f`, created `2026-10-06T08:36:46+08:00` before live validation.

## Post-Amendment Validation Outcome

B1 passed all 90 leakage probes and all nine reset determinism checks. Development validation attempted all 24 task-world pairs and produced 23 complete summaries: 18 passed and five had non-navigation container/driver failures. The final pair, `eff_007 / 1.21.11`, triggered the frozen stop rule. On attempt 1, five successful iron-block digs each produced an empty inventory delta; on the identical retry, the first iron-block dig again produced an empty inventory delta. The run was stopped immediately without completing that episode, starting evaluation validation, running B4, changing the implementation, or trying another scheme. No LLM-agent result was produced.
