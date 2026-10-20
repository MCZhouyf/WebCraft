"""Belief-only controls for the shared MIP planner."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np

from arbiter.inference import ContextMixtureModel, SourceEvidence
from arbiter.normalization import mechanism_id

from .decision import ContextBelief
from .planning import BeliefPlanner, CandidatePlan, MechanismOperator


SEARCH_FIELDS = frozenset({
    "text", "source_site", "page_title", "revid", "timestamp", "chunk_id",
})
VerificationPolicy = Literal["none", "all_plan_dependencies", "decision_voi"]


@dataclass(frozen=True)
class SearchDerivedClaim:
    """A normalized claim whose provenance is exactly one public search row."""

    hit: Mapping[str, Any]
    mechanism_id: str
    value: bool

    def __post_init__(self) -> None:
        if set(self.hit) != SEARCH_FIELDS:
            raise ValueError("claim provenance must contain exactly the six search fields")
        if not self.mechanism_id:
            raise ValueError("mechanism_id is required")


@dataclass(frozen=True)
class AggregationResult:
    name: str
    belief: ContextBelief
    verification: VerificationPolicy


def _single_context(name: str, probabilities: Mapping[str, float],
                    verification: VerificationPolicy = "none") -> AggregationResult:
    phi = {
        mechanism: np.array([float(np.clip(probability, 0.005, 0.995))])
        for mechanism, probability in probabilities.items()
    }
    return AggregationResult(name, ContextBelief(np.array([1.0]), phi), verification)


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def parametric(prior_samples: Sequence[Mapping[str, bool]]) -> AggregationResult:
    if not prior_samples:
        raise ValueError("parametric requires at least one prior sample")
    mechanisms = set(prior_samples[0])
    if any(set(sample) != mechanisms for sample in prior_samples):
        raise ValueError("prior samples must cover the same mechanisms")
    probabilities = {
        mechanism: (1 + sum(bool(sample[mechanism]) for sample in prior_samples))
        / (2 + len(prior_samples))
        for mechanism in mechanisms
    }
    return _single_context("parametric", probabilities)


def naive_rag(claims: Sequence[SearchDerivedClaim]) -> AggregationResult:
    latest: dict[str, tuple[float, int, bool]] = {}
    for index, claim in enumerate(claims):
        candidate = (_timestamp(claim.hit["timestamp"]), -index, claim.value)
        if claim.mechanism_id not in latest or candidate[:2] > latest[claim.mechanism_id][:2]:
            latest[claim.mechanism_id] = candidate
    probabilities = {key: 0.99 if row[2] else 0.01 for key, row in latest.items()}
    return _single_context("naive_rag", probabilities)


def majority_rag(claims: Sequence[SearchDerivedClaim]) -> AggregationResult:
    votes: dict[str, list[bool]] = defaultdict(list)
    for claim in claims:
        votes[claim.mechanism_id].append(claim.value)
    return AggregationResult("majority_rag", ContextBelief.majority(votes), "none")


def truthfinder_confidence(source_claims: np.ndarray, iterations: int = 20) -> np.ndarray:
    """Existing simplified Yin-style update, extended to sparse claim matrices."""
    values = np.asarray(source_claims, dtype=float)
    if values.ndim != 2 or not values.size:
        raise ValueError("source_claims must be a non-empty matrix")
    observed = np.isfinite(values)
    if not observed.any(axis=0).all():
        raise ValueError("each mechanism needs at least one claim")
    trust = np.full(values.shape[0], 0.8)
    confidence = np.full(values.shape[1], 0.5)
    for _ in range(iterations):
        signed = np.where(values == 1, trust[:, None], 1 - trust[:, None])
        confidence = np.divide(
            np.where(observed, signed, 0.0).sum(axis=0),
            observed.sum(axis=0),
        )
        agreement = np.where(values == 1, confidence, 1 - confidence)
        updated = np.divide(
            np.where(observed, agreement, 0.0).sum(axis=1),
            np.maximum(observed.sum(axis=1), 1),
        )
        updated = np.clip(updated, 0.01, 0.99)
        if np.max(np.abs(updated - trust)) < 1e-6:
            break
        trust = updated
    return confidence


def truthfinder_rag(claims: Sequence[SearchDerivedClaim]) -> AggregationResult:
    if not claims:
        raise ValueError("truthfinder_rag requires claims")
    mechanisms = sorted({claim.mechanism_id for claim in claims})
    sources = sorted({str(claim.hit["source_site"]) for claim in claims})
    matrix = np.full((len(sources), len(mechanisms)), np.nan)
    source_index = {source: index for index, source in enumerate(sources)}
    mechanism_index = {mechanism: index for index, mechanism in enumerate(mechanisms)}
    grouped: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for claim in claims:
        grouped[(str(claim.hit["source_site"]), claim.mechanism_id)].append(claim.value)
    for (source, mechanism), values in grouped.items():
        matrix[source_index[source], mechanism_index[mechanism]] = float(sum(values) >= len(values) / 2)
    confidence = truthfinder_confidence(matrix)
    return _single_context("truthfinder_rag", dict(zip(mechanisms, confidence)))


def verify_all(claims: Sequence[SearchDerivedClaim]) -> AggregationResult:
    base = naive_rag(claims)
    return AggregationResult("verify_all", base.belief, "all_plan_dependencies")


def arbiter(claims: Sequence[SearchDerivedClaim], *, use_time_prior: bool = True) -> AggregationResult:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for claim in claims:
        key = (
            str(claim.hit["source_site"]), str(claim.hit["page_title"]),
            str(claim.hit["revid"]),
        )
        row = grouped.setdefault(key, {
            "timestamp": claim.hit["timestamp"], "statements": {},
        })
        row["statements"][claim.mechanism_id] = int(claim.value)
    if len(grouped) < 2:
        raise ValueError("arbiter requires at least two independent search sources")
    sources = [
        SourceEvidence(
            source="::".join(key), timestamp=row["timestamp"],
            statements=row["statements"],
        )
        for key, row in grouped.items()
    ]
    model = ContextMixtureModel(use_time_prior=use_time_prior).fit(sources)
    assert model.world_responsibilities_ is not None and model.phi_ is not None
    belief = ContextBelief(
        model.world_responsibilities_.copy(),
        {
            mechanism: model.phi_[:, index].copy()
            for index, mechanism in enumerate(model.mechanisms_)
        },
    )
    return AggregationResult("arbiter", belief, "decision_voi")


def shared_planner(
    result: AggregationResult,
    operators: Iterable[MechanismOperator],
    initial: Counter[str],
    goal: str,
    count: int,
    *,
    limit: int = 5,
    uncertainty_weight: float = 0.35,
    inventory_aware: bool = False,
) -> list[CandidatePlan]:
    weighted = [
        MechanismOperator(**{
            **operator.__dict__,
            "probability": result.belief.mechanism_probability(operator.mechanism_id),
        })
        for operator in operators
    ]
    return BeliefPlanner(
        weighted, initial, uncertainty_weight=uncertainty_weight, inventory_aware=inventory_aware,
    ).candidates(goal, count, limit)


def verification_targets(result: AggregationResult, plan: CandidatePlan) -> tuple[str, ...]:
    if result.verification == "all_plan_dependencies":
        return plan.mechanism_ids
    return ()


RUNTIME_AGGREGATORS = {
    "parametric": parametric,
    "naive_rag": naive_rag,
    "majority_rag": majority_rag,
    "truthfinder_rag": truthfinder_rag,
    "verify_all": verify_all,
    "arbiter": arbiter,
}


def operator_from_assertion(assertion: Mapping[str, Any]) -> MechanismOperator:
    """Compile only mechanism structure explicitly present in extracted text."""
    payload = dict(assertion)
    execution = payload.pop("_execution", {})
    if not isinstance(execution, Mapping):
        raise ValueError("execution metadata must be a mapping")
    identity = mechanism_id(payload)
    kind = payload.get("kind")
    if kind == "drop":
        drops = payload.get("drops", [])
        if not isinstance(drops, list) or not drops or not isinstance(drops[0], str):
            raise ValueError("drop assertion requires a named output")
        return MechanismOperator(
            identity, "mine", {"node_type": str(payload["block"])}, (), drops[0],
            int(execution.get("output_count", payload.get("count", 1))),
            requires=tuple(map(str, execution.get("requires", ()))),
        )
    if kind != "recipe" or payload.get("station") not in {"crafting_grid", "furnace"}:
        raise ValueError("only explicit drop and crafting/furnace recipe claims are plannable")
    output = payload.get("output")
    if not isinstance(output, str):
        raise ValueError("recipe assertion requires output")
    slots: list[int] = []
    options: list[tuple[str, ...]] = []
    if payload.get("ordered"):
        for row_index, row in enumerate(payload.get("grid", [])):
            for column_index, values in enumerate(row):
                choices = (values,) if isinstance(values, str) else tuple(values or ())
                if choices:
                    slots.append(row_index * 3 + column_index)
                    options.append(tuple(map(str, choices)))
    else:
        for index, values in enumerate(payload.get("ingredients", [])):
            choices = (values,) if isinstance(values, str) else tuple(values or ())
            if choices:
                slots.append(index)
                options.append(tuple(map(str, choices)))
    if not options:
        raise ValueError("recipe assertion requires explicit ingredients")
    furnace = payload["station"] == "furnace"
    if furnace:
        params = {
            "input": options[0][0], "fuel": str(payload.get("fuel", "coal")),
            "n": 1,
        }
    else:
        layout: list[str | None] = [None] * 9
        for slot, choices in zip(slots, options):
            layout[slot] = choices[0]
        params = {"layout": layout}
    return MechanismOperator(
        identity, "smelt" if furnace else "craft_grid",
        params,
        tuple(options), output, int(payload.get("count", 1)),
        requires=((str(payload.get("fuel", "coal")),) if furnace else ()),
        layout_slots=tuple(slots),
    )
