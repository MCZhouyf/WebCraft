"""Minimal runtime shell; inference and planning are added in later checkpoints."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable

from arbiter.contracts import Goal, ItemStack, SemanticAPI

from .decision import ContextBelief, Decision, DecisionController
from .planning import (
    BeliefPlanner, CandidatePlan, MechanismOperator, action_signature,
)


@dataclass(frozen=True)
class AblationConfig:
    source_context: bool = True
    decision_voi: bool = True
    llm_source: bool = True
    time_prior: bool = True
    exec_as_probe: bool = True


@dataclass(frozen=True)
class ExecutionPolicy:
    n_plans: int = 5
    uncertainty_weight: float = 0.35
    inventory_aware: bool = True
    max_probes: int = 12
    max_actions: int = 90
    decision_error_cost: float = 10.0


@dataclass(frozen=True)
class ExecutionStrategy:
    name: str
    probe_mode: str = "none"
    update_belief: bool = False

    def __post_init__(self) -> None:
        if self.probe_mode not in {"none", "voi", "all"}:
            raise ValueError(f"unsupported probe mode: {self.probe_mode}")


NAIVE_EXECUTION = ExecutionStrategy("naive")
ARBITER_EXECUTION = ExecutionStrategy("arbiter", probe_mode="voi", update_belief=True)
VERIFY_ALL_EXECUTION = ExecutionStrategy("verify_all", probe_mode="all", update_belief=True)


def classification_information_value(
    probability: float, error_cost: float, probe_cost: float,
) -> float:
    """Expected reduction in minimum binary decision loss."""
    return min(probability, 1.0 - probability) * error_cost - probe_cost


class ArbiterAgent:
    def __init__(self, goal: Goal, kit: list[ItemStack], api: SemanticAPI):
        self.goal = goal
        self.kit = Counter({item.name: item.count for item in kit})
        self.api = api
        self._operators: tuple[MechanismOperator, ...] = ()
        self._belief: ContextBelief | None = None
        self._anchors: tuple[str, ...] = ()
        self._ablations = AblationConfig()
        self._used_mechanisms: list[str] = []
        self._explicit_probes: list[str] = []

    def configure(self, operators: Iterable[MechanismOperator], belief: ContextBelief,
                  time_anchors: Iterable[str] = (),
                  ablations: AblationConfig | None = None) -> None:
        self._operators = tuple(operators)
        self._ablations = ablations or AblationConfig()
        self._belief = belief if self._ablations.source_context else belief.collapsed()
        self._anchors = tuple(time_anchors)

    def _call(self, method: str, params: dict[str, Any], *, intent: str):
        if method == "mine":
            return self.api.mine(params["node_type"], intent=intent)
        if method == "craft_grid":
            return self.api.craft_grid(params["layout"], intent=intent)
        if method == "smelt":
            return self.api.smelt(
                params["input"], params["fuel"], params["n"], intent=intent,
            )
        if method == "check_name":
            return self.api.check_name(params["identifier"], intent=intent)
        raise ValueError(f"unsupported action method: {method}")

    @staticmethod
    def _produced_names(result: Any) -> set[str]:
        if not isinstance(result, dict):
            return set()
        values = []
        if isinstance(result.get("product"), dict):
            values.append(result["product"])
        values.extend(result.get("items", []))
        return {item["name"] for item in values if isinstance(item, dict) and "name" in item}

    def execute_plan(
        self, plan: CandidatePlan, *, exec_as_probe: bool = True,
        intent: str = "commit",
    ) -> bool:
        if self._belief is None:
            raise RuntimeError("agent is not configured")
        if intent not in {"commit", "probe"}:
            raise ValueError("plan intent must be commit or probe")
        for step in plan.steps:
            self._used_mechanisms.append(step.mechanism_id)
            result = self._call(step.method, step.params, intent=intent)
            observed = not (isinstance(result, dict) and result.get("success") is False)
            observed = observed and step.expected_output in self._produced_names(result)
            if exec_as_probe:
                self._belief.update(step.mechanism_id, observed)
            if not observed:
                return False
        return True

    @property
    def used_mechanism_ids(self) -> tuple[str, ...]:
        return tuple(self._used_mechanisms)

    def solve(
        self, *, policy: ExecutionPolicy | None = None,
        strategy: ExecutionStrategy = ARBITER_EXECUTION,
    ) -> dict[str, Any]:
        if self._belief is None:
            raise RuntimeError("agent is not configured")
        policy = policy or ExecutionPolicy()
        self._used_mechanisms = []
        self._explicit_probes = []
        blocked_actions: set[str] = set()
        traces: list[dict[str, Any]] = []
        spent = 0
        controller = DecisionController()
        decisions: list[str] = []

        def finish(success: bool, feasible: bool | None) -> dict[str, Any]:
            if success and self._anchors:
                self.api.report_version(self._anchors[int(self._belief.q.argmax())])
            self.api.submit({"feasible": feasible})
            return {**self._result(success, feasible, decisions), "policy_trace": traces}

        while len(self._used_mechanisms) < policy.max_actions and spent < policy.max_actions:
            self.kit = Counter({item["name"]: item["count"] for item in self.api.inventory()})
            if self.kit[self.goal.item] >= self.goal.count:
                return finish(True, True)
            operators = [
                replace(
                    operator,
                    probability=self._belief.mechanism_probability(operator.mechanism_id),
                )
                for operator in self._operators
            ]
            planner = BeliefPlanner(
                operators, self.kit,
                uncertainty_weight=policy.uncertainty_weight,
                action_limit=policy.max_actions - spent,
                inventory_aware=policy.inventory_aware,
                blocked_actions=frozenset(blocked_actions),
            )
            plans = planner.candidates(
                self.goal.item, self.goal.count, policy.n_plans,
            )
            first_steps = {
                plan.steps[0].mechanism_id: plan.steps[0]
                for plan in plans if plan.steps
            }
            eligible: set[str] = set()
            if strategy.probe_mode == "voi" and len(self._explicit_probes) < policy.max_probes:
                eligible = set(first_steps)
                decision = controller.choose(
                    plans, self._belief,
                    {key: step.cost for key, step in first_steps.items()},
                    decision_voi=self._ablations.decision_voi,
                    probe_candidates=eligible,
                )
            elif not plans:
                decision = Decision("infeasible")
            elif strategy.probe_mode == "all":
                step = plans[0].steps[0]
                decision = Decision("probe", mechanism_id=step.mechanism_id)
            else:
                decision = Decision("execute", plan=plans[0])
            risk_value = 0.0
            if (
                decision.kind != "infeasible"
                and strategy.probe_mode == "voi"
                and self._ablations.decision_voi
                and policy.decision_error_cost > 0
            ):
                scored = [
                    (
                        classification_information_value(
                            self._belief.mechanism_probability(key),
                            policy.decision_error_cost,
                            first_steps[key].cost,
                        ),
                        key,
                    )
                    for key in sorted(eligible)
                    if key not in (self._belief.observations or {})
                ]
                if scored:
                    risk_value, mechanism_id = max(scored)
                    if risk_value > controller.kappa:
                        decision = Decision(
                            "probe", mechanism_id=mechanism_id,
                            score=risk_value,
                        )
            decisions.append(decision.kind)
            if decision.kind == "infeasible":
                return finish(False, False)
            step = (
                first_steps[decision.mechanism_id]
                if decision.kind == "probe"
                else decision.plan.steps[0]
            )
            signature = action_signature(
                step.method, step.params, step.expected_output,
            )
            if signature in blocked_actions or spent + step.cost > policy.max_actions:
                return finish(False, None)
            intent = "probe" if decision.kind == "probe" else "commit"
            probability_before = self._belief.mechanism_probability(step.mechanism_id)
            result = self._call(step.method, step.params, intent=intent)
            self._used_mechanisms.append(step.mechanism_id)
            if intent == "probe":
                self._explicit_probes.append(step.mechanism_id)
            spent += int(result.get("cost", {}).get("primary", step.cost))
            observed = (
                result.get("success") is not False
                and step.expected_output in self._produced_names(result)
            )
            resource_failure = result.get("error") in {
                "missing ingredient", "insufficient fuel",
            }
            if strategy.update_belief and not resource_failure and (
                intent == "probe" or self._ablations.exec_as_probe
            ):
                self._belief.update(step.mechanism_id, observed)
            if not observed:
                blocked_actions.add(signature)
            traces.append({
                "intent": intent,
                "mechanism_id": step.mechanism_id,
                "action_signature": signature,
                "probability_before": probability_before,
                "decision_score": decision.score,
                "observed": observed,
                "resource_failure": resource_failure,
                "remaining_plans": len(plans),
                "probe_candidates": sorted(eligible),
                "risk_information_value": risk_value,
                "execution_strategy": strategy.name,
            })
        return finish(False, None)

    def _result(self, success: bool, feasible: bool | None,
                decisions: list[str]) -> dict[str, Any]:
        return {
            "success": success, "feasible": feasible, "decisions": decisions,
            "used_mechanism_ids": list(self._used_mechanisms),
            "probe_mechanism_ids": list(self._explicit_probes),
        }
