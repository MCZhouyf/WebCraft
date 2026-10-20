"""Build the final development-result bundle from the two canonical runs."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.evaluation.p6_matrix import one_sided_conflict_ids


ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "all_public": ROOT / "artifacts/p6_final_all_public",
    "llm_only": ROOT / "artifacts/p6_final_llm_only",
}
METHODS = ("naive_rag", "arbiter")
EXPECTED_TASKS = {
    "eff_007", "fea_002", "fea_009", "pla_001", "pla_003",
    "pla_008", "pla_009", "pla_011",
}
ACTION_METHODS = {"mine", "craft_grid", "smelt", "check_name"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _terminal(row: dict[str, Any]) -> str:
    if row["metrics"]["SR"] and row["true_feasible"]:
        return "solved"
    if row["reported_feasible"] is False:
        return "plan_not_found" if row["true_feasible"] else "reported_infeasible"
    return "budget_exhausted" if row["metrics"]["primary_cost"] >= 90 else "execution_failed"


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(row["metrics"][field]) for row in rows) / len(rows)


def main() -> None:
    output = ROOT / "artifacts/p6_final_summary"
    output.mkdir(parents=True, exist_ok=True)
    one_sided = one_sided_conflict_ids(
        Path("/root/autodl-tmp/webcraft/prereg/corpus_freeze.md"),
    )
    aggregates: list[dict[str, Any]] = []
    subsets: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []
    audit: dict[str, Any] = {
        "schema_version": 1,
        "scope": "post-selection eight-task development validation",
        "evaluation_tasks_read_or_run": False,
        "llm_calls": 0,
        "runs": {},
    }
    reference_manifest = None
    for mode, run_dir in RUNS.items():
        rows = _jsonl(run_dir / "episodes.jsonl")
        if len(rows) != 144 or {row["task_id"] for row in rows} != EXPECTED_TASKS:
            raise RuntimeError(f"{mode} does not contain the frozen development matrix")
        if {row["method"] for row in rows} != set(METHODS):
            raise RuntimeError(f"{mode} method coverage differs")
        manifest_hash = _digest(run_dir / "run_manifest.json")
        reference_manifest = reference_manifest or manifest_hash
        if manifest_hash != reference_manifest:
            raise RuntimeError("reading modes use different manifests")
        evidence = sorted((run_dir / "evidence").glob("*.json"))
        if len(evidence) != 24 or any(
            json.loads(path.read_text()).get("claim_source") != "precomputed_cache"
            for path in evidence
        ):
            raise RuntimeError(f"{mode} did not exclusively use frozen claim caches")
        arbiter_rows = [row for row in rows if row["method"] == "arbiter"]
        aligned = True
        for row in arbiter_rows:
            actions = [event for event in row["events"] if event["method"] in ACTION_METHODS]
            trace = row.get("policy_trace", [])
            aligned &= len(actions) == len(trace) and all(
                event.get("intent") == step.get("intent")
                for event, step in zip(actions, trace)
            )
        if not aligned:
            raise RuntimeError(f"{mode} action intents are not aligned with policy traces")
        for method in METHODS:
            group = [row for row in rows if row["method"] == method]
            aggregates.append({
                "reading_mode": mode,
                "method": method,
                "episodes": len(group),
                "SR": _mean(group, "SR"),
                "HTR_commit": _mean(group, "HTR_commit"),
                "HTR_all": _mean(group, "HTR_all"),
                "probe_count": _mean(group, "probe_count"),
                "probe_cost": _mean(group, "probe_cost"),
                "primary_cost": _mean(group, "primary_cost"),
                "searches": sum(row["reader"]["search_count"] for row in group) / len(group),
            })
            count = Counter(_terminal(row) for row in group)
            terminals.append({
                "reading_mode": mode,
                "method": method,
                "episodes": len(group),
                **{name: count[name] for name in (
                    "solved", "reported_infeasible", "plan_not_found",
                    "budget_exhausted", "execution_failed",
                )},
            })
        for subset in ("all", "two_sided", "one_sided"):
            selected = [
                row for row in rows
                if subset == "all" or bool(
                    one_sided.intersection(row["conflict_mechanism_ids"]),
                ) == (subset == "one_sided")
            ]
            for method in METHODS:
                group = [row for row in selected if row["method"] == method]
                subsets.append({
                    "reading_mode": mode,
                    "subset": subset,
                    "method": method,
                    "episodes": len(group),
                    "SR": _mean(group, "SR"),
                    "HTR_commit": _mean(group, "HTR_commit"),
                    "HTR_all": _mean(group, "HTR_all"),
                    "probe_count": _mean(group, "probe_count"),
                    "probe_cost": _mean(group, "probe_cost"),
                    "searches": sum(row["reader"]["search_count"] for row in group) / len(group),
                })
        diagnosis = json.loads((run_dir / "diagnosis/summary.json").read_text())
        audit["runs"][mode] = {
            "episodes_sha256": _digest(run_dir / "episodes.jsonl"),
            "manifest_sha256": manifest_hash,
            "gate_sha256": _digest(run_dir / "gate.json"),
            "evidence_bundles": len(evidence),
            "claims_cache_only": True,
            "policy_trace_action_alignment": aligned,
            "maximum_probes": max(row["metrics"]["probe_count"] for row in arbiter_rows),
            "maximum_primary_cost": max(row["metrics"]["primary_cost"] for row in arbiter_rows),
            "missing_ingredient_probes": sum(
                event.get("intent") == "probe" and event.get("result", {}).get("error") == "missing ingredient"
                for row in arbiter_rows for event in row["events"]
            ),
            "closure": diagnosis["closure"],
            "gate": json.loads((run_dir / "gate.json").read_text()),
        }
    audit["shared_manifest_sha256"] = reference_manifest
    _write_csv(output / "aggregate.csv", aggregates)
    _write_csv(output / "subsets.csv", subsets)
    _write_csv(output / "terminal_counts.csv", terminals)
    (output / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
