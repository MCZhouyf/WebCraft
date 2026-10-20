"""Audit one frozen P6 round-5 reading mode against round 4."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from arbiter.evaluation.p6_execution import _claims
from arbiter.evaluation.p6_matrix import one_sided_conflict_ids, result_subsets
from arbiter.evaluation.reporting import load_episode_records


sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_p6_sanity_gates import _gold_pipeline  # noqa: E402


DEVELOPMENT_IDS = {
    "eff_007", "fea_002", "fea_009", "pla_001", "pla_003",
    "pla_008", "pla_009", "pla_011",
}
TERMINALS = (
    "solved", "reported_infeasible", "plan_not_found",
    "budget_exhausted", "execution_failed",
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty analysis table")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _terminal(row: dict[str, Any]) -> str:
    if row["metrics"]["SR"] and row["true_feasible"]:
        return "solved"
    if row["reported_feasible"] is False:
        return "plan_not_found" if row["true_feasible"] else "reported_infeasible"
    primary = sum(float(event["cost"]["primary"]) for event in row["events"])
    return "budget_exhausted" if primary >= 90 else "execution_failed"


def _trim_layout(layout: list[Any]) -> list[list[Any]]:
    rows = [layout[offset:offset + 3] for offset in range(0, 9, 3)]
    while rows and not any(value is not None for value in rows[0]):
        rows.pop(0)
    while rows and not any(value is not None for value in rows[-1]):
        rows.pop()
    if not rows:
        return []
    while rows and not any(row[0] is not None for row in rows):
        rows = [row[1:] for row in rows]
    while rows and not any(row[-1] is not None for row in rows):
        rows = [row[:-1] for row in rows]
    return rows


def _assignable(values: list[str], options: tuple[tuple[str, ...], ...]) -> bool:
    remaining = Counter(values)

    def assign(index: int) -> bool:
        if index == len(options):
            return not any(remaining.values())
        for item in options[index]:
            if remaining[item] <= 0:
                continue
            remaining[item] -= 1
            if assign(index + 1):
                return True
            remaining[item] += 1
        return False

    return len(values) == len(options) and assign(0)


def _retrieval_matches(operator: Any, claim: dict[str, Any]) -> bool:
    if claim.get("polarity") != "positive" or claim.get("tense") != "current":
        return False
    payload = claim.get("payload")
    if not isinstance(payload, dict):
        return False
    if operator.method == "mine":
        return bool(
            claim.get("claim_type") == "drop"
            and claim.get("subject_item") == operator.params["node_type"]
            and not payload.get("conditional_variants")
            and not payload.get("silk_touch")
            and isinstance(payload.get("drops"), list)
            and operator.output in payload["drops"]
        )
    expected_type = "smelting" if operator.method == "smelt" else "recipe"
    if claim.get("claim_type") != expected_type or claim.get("subject_item") != operator.output:
        return False
    try:
        if int(payload.get("output_count", 1)) != operator.output_count:
            return False
    except (TypeError, ValueError):
        return False
    ingredients = payload.get("ingredients")
    if not isinstance(ingredients, list) or not all(isinstance(x, str) for x in ingredients):
        return False
    if operator.method == "smelt":
        return any(option in ingredients for option in operator.input_options[0])
    layout = payload.get("layout")
    if isinstance(layout, list) and len(layout) == 9 and operator.layout_slots:
        options = payload.get("layout_options")
        cells: list[Any] = options if isinstance(options, list) and len(options) == 9 else [
            None if item is None else [item] for item in layout
        ]
        actual = _trim_layout(cells)
        expected_layout = [None] * 9
        for slot, choices in zip(operator.layout_slots, operator.input_options):
            expected_layout[slot] = choices
        expected = _trim_layout(expected_layout)
        if len(actual) != len(expected) or any(
            len(left) != len(right) for left, right in zip(actual, expected)
        ):
            return False
        def compatible(candidate: list[list[Any]]) -> bool:
            for expected_row, actual_row in zip(expected, candidate):
                for wanted, found in zip(expected_row, actual_row):
                    if wanted is None or found is None:
                        if wanted is not None or found is not None:
                            return False
                    elif not set(wanted).intersection(found):
                        return False
            return True

        return compatible(actual) or compatible([list(reversed(row)) for row in actual])
    return _assignable(ingredients, operator.input_options)


def _gold_operators(webcraft: Path) -> dict[str, Any]:
    result = {}
    for world in ("1.16.5", "1.19", "1.21.11"):
        _, operators, _ = _gold_pipeline(world, webcraft)
        result.update({operator.mechanism_id: operator for operator in operators})
    return result


def _summaries(rows: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["method"], row["condition"])].append(row)
    output = []
    for (method, condition), values in sorted(grouped.items()):
        output.append({
            "stage": stage, "method": method, "condition": condition,
            "episodes": len(values),
            **{name: sum(row["terminal_type"] == name for row in values)
               for name in TERMINALS},
            "mean_closure": sum(float(row["closure_rate"]) for row in values) / len(values),
            "mean_retrieved_closure": sum(
                float(row.get("retrieved_closure_rate", row["closure_rate"]))
                for row in values
            ) / len(values),
            "mean_compiled_closure": sum(
                float(row.get("compiled_closure_rate", row["closure_rate"]))
                for row in values
            ) / len(values),
            "SR": sum(float(row["SR"]) for row in values) / len(values),
            "HTR_all": sum(float(row["HTR_all"]) for row in values) / len(values),
            "HTR_commit": (
                sum(float(row["HTR_commit"]) for row in values) / len(values)
                if all(row["HTR_commit"] != "" for row in values) else ""
            ),
            "mean_probe_count": sum(float(row["probe_count"]) for row in values) / len(values),
            "mean_probe_cost": sum(float(row["probe_cost"]) for row in values) / len(values),
            "mean_searches": sum(float(row["search_count"]) for row in values) / len(values),
        })
    return output


def analyze(
    run_dir: Path, old_diagnosis: Path, output_dir: Path,
    stage_label: str = "round5",
) -> dict[str, Any]:
    episodes = _jsonl(run_dir / "episodes.jsonl")
    if len(episodes) != 144 or {row["task_id"] for row in episodes} != DEVELOPMENT_IDS:
        raise ValueError("run is not the frozen 144-episode development matrix")
    old_rows = _read_csv(old_diagnosis)
    old = {
        (row["method"], row["condition"], row["task_id"], row["world"], int(row["seed"])): row
        for row in old_rows
    }

    bundles: dict[tuple[str, str, int], dict[str, Any]] = {}
    modes = set()
    extractor_counts: dict[str, int] = defaultdict(int)
    for path in sorted((run_dir / "evidence").glob("*.json")):
        bundle = json.loads(path.read_text())
        if bundle["task_id"] not in DEVELOPMENT_IDS:
            raise ValueError("evidence contains a non-development task")
        modes.add(bundle.get("reading_mode"))
        for extractor, count in bundle["reader"]["extractor_claim_counts"].items():
            extractor_counts[extractor] += int(count)
        seed = int(path.stem.rsplit("__", 1)[1])
        derived, _ = _claims(bundle)
        bundles[(bundle["condition"], bundle["task_id"], seed)] = {
            "compiled": {claim.mechanism_id for claim in derived},
            "claims": bundle["claims"],
        }
    if len(modes) != 1 or None in modes:
        raise ValueError("evidence does not have one explicit reading mode")
    reading_mode = modes.pop()
    gold = _gold_operators(Path("/root/autodl-tmp/webcraft"))

    detail = []
    for row in episodes:
        key = (row["method"], row["condition"], row["task_id"], row["world"], int(row["seed"]))
        before = old[key]
        required = set(filter(None, before["required_mechanism_ids"].split(";")))
        bundle = bundles[(row["condition"], row["task_id"], int(row["seed"]))]
        compiled = required & bundle["compiled"]
        missing_gold = required - set(gold)
        if missing_gold:
            raise ValueError(f"required mechanisms absent from G1 universe: {missing_gold}")
        retrieved = {
            mechanism for mechanism in required
            if any(_retrieval_matches(gold[mechanism], claim) for claim in bundle["claims"])
        }
        denominator = len(required)
        metrics = row["metrics"]
        detail.append({
            "method": row["method"], "condition": row["condition"],
            "task_id": row["task_id"], "world": row["world"], "seed": row["seed"],
            "reading_mode": reading_mode, "terminal_type": _terminal(row),
            "search_count": row["reader"]["search_count"],
            "unique_chunks": row["reader"]["unique_chunk_count"],
            "admitted_claims": row["reader"]["admitted_claim_count"],
            "retrieved_closure_numerator": len(retrieved),
            "retrieved_closure_denominator": denominator,
            "retrieved_closure_rate": len(retrieved) / denominator if denominator else 1.0,
            "compiled_closure_numerator": len(compiled),
            "compiled_closure_denominator": denominator,
            "compiled_closure_rate": len(compiled) / denominator if denominator else 1.0,
            "closure_numerator": len(compiled), "closure_denominator": denominator,
            "closure_rate": len(compiled) / denominator if denominator else 1.0,
            "true_feasible": row["true_feasible"],
            "reported_feasible": row["reported_feasible"],
            "SR": metrics["SR"], "HTR_all": metrics["HTR_all"],
            "HTR_commit": metrics["HTR_commit"],
            "probe_count": row["probe_count"], "probe_cost": row["probe_cost"],
            "required_mechanism_ids": ";".join(sorted(required)),
            "retrieved_mechanism_ids": ";".join(sorted(retrieved)),
            "matched_mechanism_ids": ";".join(sorted(compiled)),
        })
    _write_csv(output_dir / "episodes.csv", detail)

    before_detail = [{
        **row, "HTR_all": row.get("HTR", row.get("HTR_recorded", 0)),
        "HTR_commit": "", "probe_count": row.get("probe_count", 0),
        "probe_cost": row.get("probe_cost", 0),
    } for row in old_rows]
    before_after = _summaries(before_detail, "round4") + _summaries(detail, stage_label)
    _write_csv(output_dir / "method_condition_before_after.csv", before_after)
    current = _summaries(detail, stage_label)
    _write_csv(output_dir / "method_condition.csv", current)

    records = load_episode_records(run_dir / "episodes.jsonl")
    subsets = result_subsets(
        records,
        one_sided_conflict_ids(Path("/root/autodl-tmp/webcraft/prereg/corpus_freeze.md")),
    )
    subset_rows = []
    for subset_name, subset in subsets.items():
        for method in ("naive_rag", "arbiter"):
            values = [row for row in subset if row.method == method]
            subset_rows.append({
                "subset": subset_name, "method": method, "episodes": len(values),
                "SR": sum(row.sr for row in values) / len(values),
                "HTR_commit": sum(row.committed_htr for row in values) / len(values),
                "HTR_all": sum(row.htr_all for row in values) / len(values),
                "probe_count": sum(row.probes for row in values) / len(values),
                "probe_cost": sum(row.probe_cost for row in values) / len(values),
                "searches": sum(row.searches for row in values) / len(values),
            })
    _write_csv(output_dir / "three_subset_summary.csv", subset_rows)

    naive_mismatch = {
        condition: {
            "HTR_all": next(row["HTR_all"] for row in current
                            if row["method"] == "naive_rag" and row["condition"] == condition),
            "HTR_commit": next(row["HTR_commit"] for row in current
                               if row["method"] == "naive_rag" and row["condition"] == condition),
        }
        for condition in ("mismatched_old2new", "mismatched_new2old")
    }
    gate = json.loads((run_dir / "gate.json").read_text())
    result = {
        "schema_version": 2, "stage": stage_label, "reading_mode": reading_mode,
        "episodes": len(detail), "development_task_ids": sorted(DEVELOPMENT_IDS),
        "evaluation_tasks_read_or_run": False, "llm_calls": 0,
        "gate": gate, "naive_mismatch_HTR": naive_mismatch,
        "method_condition": current, "three_subset_summary": subset_rows,
        "extractor_claim_counts": dict(sorted(extractor_counts.items())),
        "closure": {
            "required": sum(int(row["compiled_closure_denominator"]) for row in detail) // 2,
            "retrieved": sum(int(row["retrieved_closure_numerator"]) for row in detail) // 2,
            "compiled": sum(int(row["compiled_closure_numerator"]) for row in detail) // 2,
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--old-diagnosis", type=Path,
        default=Path("artifacts/p6_round4_pipeline/diagnosis/episodes.csv"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stage-label", default="round5")
    args = parser.parse_args()
    print(json.dumps(analyze(
        args.run_dir, args.old_diagnosis, args.output_dir, args.stage_label,
    ), sort_keys=True))
