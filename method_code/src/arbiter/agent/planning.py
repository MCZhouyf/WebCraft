"""Belief-derived quantity planner. Inputs are public mechanism statements only."""
from __future__ import annotations

import math
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

import pulp


def action_signature(method: str, params: dict[str, Any], output: str) -> str:
    return json.dumps([method, params, output], sort_keys=True)


def resolved_params(operator: "MechanismOperator", choices: tuple[str, ...], batch: int = 1) -> dict[str, Any]:
    if operator.method == "craft_grid":
        layout = [None] * 9
        for slot, item in zip(operator.layout_slots, choices):
            layout[slot] = item
        return {"layout": layout}
    if operator.method == "smelt":
        return {"input": choices[0], "fuel": operator.params.get("fuel", "coal"), "n": batch}
    return dict(operator.params)


@dataclass(frozen=True)
class MechanismOperator:
    mechanism_id: str
    method: str
    params: dict[str, Any]
    input_options: tuple[tuple[str, ...], ...]
    output: str
    output_count: int = 1
    action_cost: int = 1
    probability: float = 0.5
    requires: tuple[str, ...] = ()
    acquisition_cost: float = 0.0
    layout_slots: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlanStep:
    mechanism_id: str
    method: str
    params: dict[str, Any]
    expected_output: str
    expected_count: int
    cost: int


@dataclass(frozen=True)
class CandidatePlan:
    steps: tuple[PlanStep, ...]
    mechanism_ids: tuple[str, ...]
    primary_cost: int
    objective: float
    selected_counts: tuple[int, ...] = field(repr=False)


class BeliefPlanner:
    def __init__(
        self,
        operators: Iterable[MechanismOperator],
        initial: Counter[str],
        *,
        uncertainty_weight: float = 0.35,
        action_limit: int = 120,
        inventory_aware: bool = False,
        blocked_actions: frozenset[str] = frozenset(),
    ) -> None:
        self.operators = tuple(operators)
        self.initial = Counter(initial)
        self.uncertainty_weight = uncertainty_weight
        self.action_limit = action_limit
        self.inventory_aware = inventory_aware
        self.blocked_actions = blocked_actions

    def _resolved(self) -> list[tuple[MechanismOperator, tuple[str, ...]]]:
        reachable = {name for name, count in self.initial.items() if count > 0}
        depth = {name: 0 for name in reachable}
        selected: dict[int, tuple[str, ...]] = {}
        changed = True
        while changed:
            changed = False
            for index, operator in enumerate(self.operators):
                if operator.probability <= 0:
                    continue
                choices_list = []
                allocated = Counter()
                for options in operator.input_options:
                    available = [item for item in options if item in reachable]
                    if self.inventory_aware:
                        choice = min(available, key=lambda item: (
                            self.initial[item] <= allocated[item], depth.get(item, math.inf), item,
                        ), default="")
                    else:
                        choice = min(available, default="")
                    choices_list.append(choice)
                    allocated[choice] += 1
                choices = tuple(choices_list)
                if any(not choice for choice in choices):
                    continue
                if any(requirement not in reachable for requirement in operator.requires):
                    continue
                if action_signature(operator.method, resolved_params(operator, choices), operator.output) in self.blocked_actions:
                    continue
                selected[index] = choices
                if operator.output not in reachable:
                    reachable.add(operator.output)
                    depth[operator.output] = 1 + max((depth[item] for item in choices), default=0)
                    changed = True
        return [(self.operators[index], choices) for index, choices in sorted(selected.items())]

    def candidates(self, goal: str, count: int, limit: int = 5) -> list[CandidatePlan]:
        resolved = self._resolved()
        if self.initial[goal] >= count:
            return [CandidatePlan((), (), 0, 0.0, tuple(0 for _ in resolved))]
        if not any(operator.output == goal for operator, _ in resolved):
            return []
        exclusions: list[tuple[int, ...]] = []
        plans: list[CandidatePlan] = []
        attempts = 0
        while len(plans) < limit and attempts < limit * 4:
            attempts += 1
            solution = self._solve_once(resolved, goal, count, exclusions)
            if solution is None:
                break
            counts, objective = solution
            exclusions.append(counts)
            scheduled = self._schedule(resolved, counts)
            if scheduled is None:
                continue
            steps, primary = scheduled
            plans.append(CandidatePlan(
                tuple(steps), tuple(dict.fromkeys(step.mechanism_id for step in steps)),
                primary, objective, counts,
            ))
        return plans

    def _solve_once(self, resolved, goal, count, exclusions):
        problem = pulp.LpProblem("arbiter_belief_plan", pulp.LpMinimize)
        variables = [
            pulp.LpVariable(f"x_{index}", 0, self.action_limit, cat="Integer")
            for index in range(len(resolved))
        ]
        active = [
            pulp.LpVariable(f"z_{index}", 0, 1, cat="Binary")
            for index in range(len(resolved))
        ]
        for variable, selected in zip(variables, active):
            problem += variable <= self.action_limit * selected
            problem += variable >= selected
        produced: dict[str, list[Any]] = {}
        consumed: dict[str, list[Any]] = {}
        fuel_variables: dict[int, Any] = {}
        for variable, (operator, choices) in zip(variables, resolved):
            produced.setdefault(operator.output, []).append(operator.output_count * variable)
            for item in choices:
                consumed.setdefault(item, []).append(variable)
        for index, (variable, (operator, _)) in enumerate(zip(variables, resolved)):
            if operator.method != "smelt":
                continue
            fuel = pulp.LpVariable(f"fuel_{index}", 0, self.action_limit, cat="Integer")
            problem += 8 * fuel >= variable
            problem += 8 * fuel - variable <= 7
            consumed.setdefault(operator.params.get("fuel", "coal"), []).append(fuel)
            fuel_variables[index] = fuel
        items = set(self.initial) | set(produced) | set(consumed) | {goal}
        for item in items:
            balance = self.initial[item] + pulp.lpSum(produced.get(item, [])) - pulp.lpSum(consumed.get(item, []))
            problem += balance >= (count if item == goal else 0)
        for variable, (operator, _) in zip(variables, resolved):
            for requirement in operator.requires:
                availability = self.initial[requirement] + pulp.lpSum(produced.get(requirement, []))
                used = pulp.LpVariable(f"u_{len(problem.constraints)}", 0, 1, cat="Binary")
                problem += variable <= self.action_limit * used
                problem += variable >= used
                problem += availability >= used
        for exclusion_index, previous in enumerate(exclusions):
            problem += pulp.lpSum(
                (1 - selected) if count > 0 else selected
                for selected, count in zip(active, previous)
            ) >= 1, f"exclude_support_{exclusion_index}"
        terms = []
        for variable, (operator, _) in zip(variables, resolved):
            penalty = -self.uncertainty_weight * math.log(max(operator.probability, 1e-9))
            terms.append((operator.action_cost + penalty) * variable)
        problem += pulp.lpSum(terms)
        status = problem.solve(pulp.PULP_CBC_CMD(msg=False, threads=1, timeLimit=20))
        if status != pulp.LpStatusOptimal:
            return None
        counts = tuple(int(round(pulp.value(variable) or 0)) for variable in variables)
        return counts, float(pulp.value(problem.objective) or 0.0)

    def _schedule(self, resolved, counts):
        inventory = Counter(self.initial)
        remaining = list(counts)
        steps: list[PlanStep] = []
        while any(remaining):
            progressed = False
            for index, ((operator, choices), uses) in enumerate(zip(resolved, remaining)):
                if not uses:
                    continue
                batch = uses if operator.method == "smelt" else 1
                required = Counter({item: amount * batch for item, amount in Counter(choices).items()})
                if operator.method == "smelt":
                    fuel = operator.params.get("fuel", "coal")
                    required[fuel] += math.ceil(batch / 8)
                if any(inventory[item] < amount for item, amount in required.items()):
                    continue
                if any(inventory[item] < 1 for item in operator.requires):
                    continue
                inventory.subtract(required)
                inventory[operator.output] += operator.output_count * batch
                params = dict(operator.params)
                if operator.method == "craft_grid":
                    layout = [None] * 9
                    for slot, item in zip(operator.layout_slots, choices):
                        layout[slot] = item
                    params = {"layout": layout}
                elif operator.method == "smelt":
                    params = {"input": choices[0], "fuel": params.get("fuel", "coal"), "n": batch}
                steps.append(PlanStep(
                    operator.mechanism_id, operator.method, params,
                    operator.output, operator.output_count * batch, operator.action_cost * batch,
                ))
                remaining[index] -= batch
                progressed = True
                break
            if not progressed:
                return None
        return steps, sum(step.cost for step in steps)
