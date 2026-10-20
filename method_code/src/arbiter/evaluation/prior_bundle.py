"""Project frozen LLM-prior records into the public runtime bundle."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from arbiter.agent.aggregation import operator_from_assertion


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def project_prior_bundle(
    unconditional_path: Path, assertion_path: Path,
) -> dict[str, Any]:
    """Use only assertion identity and sampled answers; discard evaluator fields."""
    assertions_by_conflict: dict[str, dict[str, Any]] = {}
    for row in _jsonl(assertion_path):
        conflict_id = str(row["conflict_id"])
        assertion = row["assertion"]
        previous = assertions_by_conflict.setdefault(conflict_id, assertion)
        if previous != assertion:
            raise ValueError(f"inconsistent assertion for {conflict_id}")

    samples_by_index: dict[int, dict[str, bool]] = defaultdict(dict)
    operators: dict[str, dict[str, Any]] = {}
    conflict_to_mechanism: dict[str, str] = {}
    for conflict_id, assertion in sorted(assertions_by_conflict.items()):
        try:
            operator = operator_from_assertion(assertion)
        except ValueError:
            continue
        if operator.mechanism_id in operators and operators[operator.mechanism_id] != assertion:
            raise ValueError("distinct prior assertions share a mechanism identity")
        operators[operator.mechanism_id] = assertion
        conflict_to_mechanism[conflict_id] = operator.mechanism_id

    for row in _jsonl(unconditional_path):
        conflict_id = str(row["conflict_id"])
        if conflict_id not in conflict_to_mechanism:
            continue
        answer = str(row.get("answer", "")).casefold()
        if answer not in {"true", "false"}:
            raise ValueError(f"unparsed prior answer for {conflict_id}")
        sample_index = int(row["sample_index"])
        mechanism = conflict_to_mechanism[conflict_id]
        if mechanism in samples_by_index[sample_index]:
            raise ValueError("duplicate prior answer in one sample")
        samples_by_index[sample_index][mechanism] = answer == "true"

    universe = set(operators)
    samples = [samples_by_index[index] for index in sorted(samples_by_index)]
    if not universe or not samples or any(set(sample) != universe for sample in samples):
        raise ValueError("prior samples do not cover the compilable assertion universe")
    return {
        "schema_version": 1,
        "assertions": [operators[key] for key in sorted(operators)],
        "samples": [{key: sample[key] for key in sorted(sample)} for sample in samples],
    }
