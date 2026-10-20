# Preregistration Amendment V2: Feasibility-Task Replacement

Amendment time: 2026-10-05, before any LLM-agent result. The author authorized a bounded diagnosis and deterministic replacement for the `fea_009 / 1.16.5` infrastructure failure. No evaluated method or LLM-agent result existed when this amendment was made.

## Failure And Diagnosis

`fea_009` originally targeted `iron_ore`. Its 1.16.5 oracle mined `stone_1`, then timed out while navigating back to the workbench for `craft_grid`. A frozen-parameter reproduction retained the original 15-second timeout and reproduced the failure once.

The detailed trace found a valid seven-node path from the mined resource to the workbench. The bot stopped at `(10.153, 4, -2.300)` in a one-cell corridor, with a complete stone floor, air at feet and head, and resource blocks immediately north and south. Pathfinder emitted three `stuck` resets and timed out after 15.002 seconds. The bot neither fell into a hole nor became fully enclosed.

A conservative four-neighbor static check covered each of the three versions in the initial arena and after the mining actions of every one of the 42 oracle plans. The workbench, furnace, and all 95 resource nodes were reachable in all 129 checked states. Across the six previously passing development tasks in 1.16.5, 40 semantic actions had median wall time 2.661 seconds; excluding two furnace actions, 38 navigation-plus-interaction actions had median 2.659 seconds and maximum 7.180 seconds. Historical logs do not separate navigation from interaction, so these are conservative upper bounds on navigation time.

Conclusion: the observed failure is task-path-specific under the frozen layout/pathfinder combination. Other task actions remain far below 15 seconds and static reachability is complete. Under the author's 2026-10-05 decision, branch 2(a) applies: replace the task rather than alter the arena, pathfinder configuration, or timeout.

Evidence:

- `reports/d1_reproduction.json`
- `reports/d1_reproduction_actions.jsonl`, SHA-256 `05ae8ddd7cd50955ad687a12ca2d413cc68e2c14dc50a9c32beee71dc23aee69`
- `reports/d1_reproduction_trace.jsonl`, SHA-256 `8fa68039680b35efad4a767d8a86bf7d3e9a3641507d483cce1b44079be11343`
- `reports/d1_static_reachability.json`
- `reports/d1_diagnosis.json`

## Replacement Rule And Result

Replacement seed: `20261005`.

The eligible pool was the sorted set of unselected feasibility candidates sharing at least one conflict main-type/assertion-subject mechanism family with the old task. The pool was `raw_gold`, `raw_gold_block`, `raw_iron`, and `raw_iron_block`; fallback to unrelated feasibility mechanisms was not needed. Uniform seeded choice selected `raw_iron`. This retains the `fea_009` identifier, `feasibility` type, and `dev` split and tests the paired iron-ore drop-replacement mechanism.

No unselected candidate retained the old `100` feasibility vector. The deterministic replacement has vector `011`, so the ten feasibility tasks change from 3 feasible / 7 infeasible to 2 / 8 in 1.16.5 and from 7 / 3 to 8 / 2 in 1.21.11. This 20/80 balance is an explicit amendment consequence; no additional task was changed to improve it.

| Frozen file | Pre-amendment SHA-256 | Amendment-v2 SHA-256 |
|---|---|---|
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` | `3a83ccb55f40d32153c3250cbe78c8b245ef4218bef37c02252dc441ec22ab76` |
| `data/stale_agent_expectations.jsonl` | `2bd2f75ddbdbd6302595cabff29c91e7072d0f97e0e711decfe1ead4e1f4c832` | `d36d06478a393ade747bee496eee0f31111888195f449a172d319f04fe1e4291` |

All other preregistered data and configuration hashes remain unchanged. The pre-amendment files are archived under `data/archive/amendment_v2_pre_replace_20261005/`.

## Infrastructure-Failure Rule

During development and evaluated agent experiments, an episode ending because of navigation timeout, connection loss, or server crash is reset and rerun exactly once with the same task, seed, world, method state, interface, and configuration. A second infrastructure failure is reported separately as `infrastructure_failure`; it is excluded from SR and HTR denominators and may not be replaced based on method performance. Both attempt-log hashes are retained.

## Author-Authorized Resource Exception

Immediately before the successful B2 startup window on 2026-10-05 (approximately 09:29 UTC; the following B2 preflight was recorded at 09:34:39 UTC), the author explicitly prioritized SCOPE and authorized closing other processes and releasing non-SCOPE memory. SCOPE PID 31529, its parent process chain, normal scheduling priority, and the `schema2weights/Qwen3-14B` cache were protected.

After confirming no process had an open descriptor or mapping under `qwen_models/Qwen3-8B`, five clean model-shard cache files received targeted `POSIX_FADV_DONTNEED`; no file was deleted or modified. Resident cache fell by 9.217 GiB and cgroup headroom rose from 3.961 GiB to 13.197 GiB. The unrelated Sparkle GUI process tree was then terminated, increasing observed headroom to 13.608 GiB. No global cache drop was performed. This is an author-authorized exception; after it, no non-WebCraft process may be modified without a new author instruction.

Immutable amendment commit: `866649a5f2c865c4cb8b27d3ec8aacd806334334`, created `2026-10-05T18:27:09+08:00`.
