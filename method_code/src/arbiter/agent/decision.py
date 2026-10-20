"""Context-aware plan scoring and one-step intervention value."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .planning import CandidatePlan


@dataclass
class ContextBelief:
    q: np.ndarray
    phi: dict[str, np.ndarray]
    observations: dict[str, bool] | None = None

    def __post_init__(self) -> None:
        self.q = np.asarray(self.q, dtype=float)
        self.q /= self.q.sum()
        if any(len(values) != len(self.q) for values in self.phi.values()):
            raise ValueError("mechanism context vectors must match q")
        if self.observations is None:
            self.observations = {}

    def mechanism_probability(self, mechanism_id: str) -> float:
        assert self.observations is not None
        if mechanism_id in self.observations:
            return float(self.observations[mechanism_id])
        return float(self.q @ self.phi.get(mechanism_id, np.full(len(self.q), 0.5)))

    def collapsed(self) -> "ContextBelief":
        return ContextBelief(
            np.array([1.0]),
            {mechanism_id: np.array([self.mechanism_probability(mechanism_id)])
             for mechanism_id in self.phi},
            dict(self.observations or {}),
        )

    @classmethod
    def majority(cls, votes: dict[str, list[bool]]) -> "ContextBelief":
        # Shared-planner control: only the aggregation changes.
        return cls(np.array([1.0]), {
            mechanism_id: np.array([0.99 if sum(values) >= len(values) / 2 else 0.01])
            for mechanism_id, values in votes.items() if values
        })

    def plan_success(self, plan: CandidatePlan) -> float:
        conditional = np.ones(len(self.q))
        for mechanism_id in plan.mechanism_ids:
            assert self.observations is not None
            if mechanism_id in self.observations:
                conditional *= float(self.observations[mechanism_id])
            else:
                conditional *= self.phi.get(mechanism_id, np.full(len(self.q), 0.5))
        return float(self.q @ conditional)

    def update(self, mechanism_id: str, outcome: bool, weight: float = 1.0) -> None:
        assert self.observations is not None
        self.observations[mechanism_id] = outcome
        probability = self.phi.get(mechanism_id, np.full(len(self.q), 0.5))
        likelihood = probability if outcome else 1 - probability
        posterior = self.q * np.power(np.clip(likelihood, 1e-9, 1), weight)
        self.q = posterior / posterior.sum()


@dataclass(frozen=True)
class Decision:
    kind: str
    plan: CandidatePlan | None = None
    mechanism_id: str | None = None
    score: float = 0.0


class DecisionController:
    def __init__(self, *, tau_s: float = 0.95, tau_inf: float = 0.10,
                 kappa: float = 0.001, delta: float = 0.05):
        self.tau_s = tau_s
        self.tau_inf = tau_inf
        self.kappa = kappa
        self.delta = delta

    def choose(self, plans: list[CandidatePlan], belief: ContextBelief,
               probe_costs: dict[str, float], decision_voi: bool = True,
               probe_candidates: set[str] | None = None) -> Decision:
        if not plans:
            return Decision("infeasible")
        scores = [belief.plan_success(plan) for plan in plans]
        best_index = int(np.argmax(scores))
        if scores[best_index] >= self.tau_s:
            return Decision("execute", plan=plans[best_index], score=scores[best_index])
        if belief.q.max() >= 1 - self.delta and max(scores) < self.tau_inf:
            return Decision("infeasible", score=max(scores))
        candidates = set().union(*(plan.mechanism_ids for plan in plans))
        if probe_candidates is not None:
            candidates &= probe_candidates
            candidates -= set(belief.observations or {})
        best_probe, best_value = None, -np.inf
        baseline = max(scores)
        for mechanism_id in (sorted(candidates) if probe_candidates is not None else candidates):
            probability = belief.phi.get(mechanism_id, np.full(len(belief.q), 0.5))
            p_true = float(belief.q @ probability)
            expected = 0.0
            for outcome, outcome_mass in ((True, p_true), (False, 1 - p_true)):
                if outcome_mass <= 0:
                    continue
                q = belief.q * (probability if outcome else 1 - probability)
                q /= q.sum()
                observations = dict(belief.observations or {})
                observations[mechanism_id] = outcome
                temporary = ContextBelief(q, belief.phi, observations)
                expected += outcome_mass * max(temporary.plan_success(plan) for plan in plans)
            value = (expected - baseline) / max(probe_costs.get(mechanism_id, 1.0), 1e-9)
            if not decision_voi:
                value = -(p_true * np.log(max(p_true, 1e-9))
                          + (1 - p_true) * np.log(max(1 - p_true, 1e-9)))
            if value > best_value:
                best_probe, best_value = mechanism_id, float(value)
        if best_probe is None or best_value <= self.kappa:
            return Decision("execute", plan=plans[best_index], score=scores[best_index])
        return Decision("probe", mechanism_id=best_probe, score=best_value)
