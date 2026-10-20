"""Evaluator-only operator universe for the preregistered Oracle baseline."""
from __future__ import annotations

import importlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from arbiter.agent.aggregation import (
    AggregationResult,
    SearchDerivedClaim,
    naive_rag,
    operator_from_assertion,
)
from arbiter.agent.decision import ContextBelief


def oracle_belief(mechanism_outcomes: Mapping[str, bool]) -> AggregationResult:
    """Preserve the evaluator-only belief helper used by earlier checkpoints."""
    return AggregationResult(
        "oracle",
        ContextBelief(np.array([1.0]), {
            mechanism: np.array([0.995 if outcome else 0.005])
            for mechanism, outcome in mechanism_outcomes.items()
        }),
        "none",
    )


def oracle_aggregation(
    world: str, initial: Counter[str], root: Path,
) -> tuple[list[Any], Any]:
    resolved = str(root.resolve())
    scripts = str((root / "scripts").resolve())
    for path in (resolved, scripts):
        if path not in sys.path:
            sys.path.insert(0, path)
    conflict_core = importlib.import_module("conflict_core")
    planner_module = importlib.import_module("oracle.planner")
    oracle = planner_module.OraclePlanner(world, initial=initial)
    operators: dict[str, Any] = {}
    for recipe in oracle.recipes:
        assertion = conflict_core.recipe_assertion(recipe["raw"])
        if assertion is None:
            continue
        operator = operator_from_assertion(assertion)
        operators[operator.mechanism_id] = operator
    for source in oracle.sources.values():
        assertion = {
            "kind": "drop", "block": source["block"], "silk_touch": False,
            "drops": [source["output"]],
            "_execution": {
                "requires": [source["tool"]] if source["tool"] else [],
                "output_count": source["output_count"],
            },
        }
        operator = operator_from_assertion(assertion)
        operators[operator.mechanism_id] = operator
    claims = [
        SearchDerivedClaim({
            "text": "evaluator-only oracle assertion",
            "source_site": "evaluator_oracle", "page_title": mechanism,
            "revid": index, "timestamp": "9999-12-31T23:59:59Z",
            "chunk_id": f"oracle_{index}",
        }, mechanism, True)
        for index, mechanism in enumerate(operators)
    ]
    return list(operators.values()), naive_rag(claims)
