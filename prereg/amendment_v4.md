# Preregistration Amendment V4: Deterministic Simulator Backend

Author decision: 2026-10-06. This amendment is prospective. No LLM-agent result exists before or during this amendment.

## Backend Switch And Frozen Live Backend

All subsequent oracle, stale-policy, method, and baseline results are produced by the deterministic simulator backend. The live Minecraft backend is frozen at commit `25779fde6df48dd612d1b87aed38fbe55b4efe6b`, is designated an optional backend, and will not be modified for this study.

Live Minecraft remains evidence in two limited roles:

1. Version truth was extracted from the three JARs and independently checked in game by B3: 90 recipe probes and 30 block-drop probes all agreed with frozen truth.
2. Every previously collected successful schema-v2 live oracle episode is an S1.5 simulator-parity input. It is never replaced or edited.

## Declared Simulator Differences

Only the following differences from live Minecraft are allowed. Any other semantic difference is a simulator bug and must be reported without changing truth, tasks, plans, expected stale behavior, or cost accounting.

- D1: mining returns the frozen truth table's guaranteed minimum named yield. Where loot quantity is random, including redstone and lapis ores, the simulator returns the minimum. This matches the oracle and `prereg/subset_rule.md` and does not change task feasibility.
- D2: inventory-slot capacity is not modeled. S1.5 must establish from the successful live set that no episode exceeds 36 occupied stacks. A contrary result invalidates D2 and requires explicit slot modeling before further results.
- D3: tool durability is initialized from minecraft-data `maxDurability`, decreases by one per mine, and consumes the tool at zero. Enchantments and repair are not modeled.
- D4: navigation, entity pickup, and container interaction do not exist in the simulator. Consequently no infrastructure-failure category exists. A simulated oracle-plan failure is a truth or oracle problem and is investigated and reported; no task is excluded.

## Frozen Semantic Budget And Acceptance Rule

The prior 420-second live wall-clock limit is replaced by a fixed 90 semantic-action budget. It is computed before simulator execution as `ceil_to_10(3 * 29)`: 29 is the longest frozen oracle plan action count over all task/world plans. The budget cannot be adjusted after execution.

The live rule permitting 23/24 development passes and paired-retry exclusions is superseded. Simulator acceptance requires all of the following:

- Development oracle: 24/24 task-world pairs pass.
- Evaluation oracle: 102/102 task-world pairs pass.
- Feasible pairs produce the goal and equal the frozen oracle semantic cost; infeasible pairs correctly report infeasibility.
- Repeating identical input produces byte-identical schema-v2 logs.

All other preregistration provisions remain frozen, including the 42-task corpus, task split, seeds, metrics, clustered bootstrap, gateway canary requirements, and frozen-file hashes.

## S1.5 Live-Parity Gate

All schema-v2 live oracle logs from the repository and archives are deduplicated by `(task, world, attempt)`. Only episodes with `success=true` and valid oracle status form the successful parity set. The simulator replays each action sequence unchanged and compares every action's success/failure, allowed public error, item flow, and primary cost, then final inventory, terminal judgment, and total cost. Frozen 1.16.5 stale-policy traces are included; their failed actions must fail with the same allowed error and charge one primary unit.

The sole allowed mismatch is D1: a random-drop simulator quantity may equal the guaranteed minimum and be no greater than the live quantity, with inventories compared after normalizing those quantities. Normalized agreement must be 100 percent. `reports/sim_parity.json` records episode and action counts, mismatch classes, D1 items, and the D2 stack-capacity check. Failed live episodes are replayed separately as S1.6 evidence; they are not silently discarded.

## Stop Rule

S1.5 must pass by 2026-10-08 12:00 UTC+8. If it does not, work stops without another simulator design, `reports/STAGE1_REPORT.md` records the blocker, and the author decides the next step.

## Frozen Inputs

| File | SHA-256 |
|---|---|
| `config/cost_model.json` | `10b481041b5489a11eb580a968ff8d15fb62aeb25179cbe43ee02ce910a4e80b` |
| `config/resource_model.json` | `c8b533f8922d42a38194922f68b164e453a627407840ff5351cf8edd1092b312` |
| `config/recipe_type_policy.json` | `b36749ef2a4a63ad40ddd9e17ff6e02cad027dd388a850c2cb46de0ff59cf732` |
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` |
| `data/stale_agent_expectations.jsonl` | `2bd2f75ddbdbd6302595cabff29c91e7072d0f97e0e711decfe1ead4e1f4c832` |
| `prereg/subset_rule.md` | `b067b3d3705bc193375e98b15a10be2fb5997ae9e6218adbea59dc2becd17111` |
