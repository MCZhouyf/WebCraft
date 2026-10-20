# Preregistration Status

STAGE1_SWITCH_TO_SIMULATOR_CANDIDATE

The original 42-task A6 corpus is active; no task is replaced. Amendment V2 prospectively replaces Mineflayer path planning inside `mine`, `craft_grid`, and `smelt` with evaluator-owned RCON teleportation to frozen interaction and pickup points. Agent-visible methods, outputs, task plans, and primary costs are unchanged.

Pre-amendment failure evidence is frozen in commit `78cd19b7e7c3c8767dca6af173f21162225a6bd3`: development 23/24 task-world pairs, evaluation 71/102, and five B4 navigation failures. No LLM-agent result existed before this amendment.

Live validation must rerun B1, all 24 development and 102 evaluation task-world pairs, and B4. Any remaining navigation or pickup failure stops the stage. B3 remains accepted at 90/90 recipe and 30/30 drop agreement.

Implementation commit: `6d9a3a43b156a5daa2d0afbd26fe21f60b72e46f`, created `2026-10-06T08:36:46+08:00` before live validation.

B1 passed all 90 leakage probes and all nine reset-hash checks. Development validation then stopped on `eff_007 / 1.21.11`: five mined iron blocks yielded no collected item on attempt 1, and the first mined iron block again yielded no collected item on the identical retry. This is the amendment's explicit pickup-failure stop condition. Evaluation validation and B4 were not started, no alternative repair was attempted, and `STAGE1_READY_FOR_PILOT` is not claimed.

The author prospectively authorized Amendment V3 before any LLM-agent result. V3 permits one entity-grounded layered pickup implementation and no alternative. Its three-hour deadline is `2026-10-06T04:52:11Z`.

V3 implementation commit: `05b289b86354bbe6bc97ba896c2a55e4aea523a8`, created `2026-10-06T09:56:01+08:00` before live validation.

V3 B1 passed all 90 leakage probes and all nine reset-hash checks. A targeted `eff_007 / 1.21.11` oracle probe passed, confirming that entity-grounded pickup repaired the V2 pickup failure. In the full development rerun, however, `pla_009` failed its final `craft_grid` action on both identical attempts in both 1.16.5 and 1.19. After 12 completed pairs, two failures made the maximum possible result 22/24, below the required 23/24. The run was stopped; evaluation and B4 were not started, and no second repair or LLM-agent run was attempted.

The required simulator gap and work estimate are recorded in `reports/SIMULATOR_BACKEND_ASSESSMENT.md`. A simulator pilot requires a new prospective amendment.
