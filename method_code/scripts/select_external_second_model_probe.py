"""Freeze the second-model probe jobs before any model call."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path

from run_main import ROOT, WEBCRAFT, load_evaluation_tasks


SEED = 20261011
CELLS = (
    ("matched_1.16.5", "1.16.5"),
    ("matched_1.19", "1.19"),
    ("matched_1.21.11", "1.21.11"),
    ("mismatched_old2new", "1.21.11"),
    ("mismatched_new2old", "1.16.5"),
)
OUTPUT = ROOT / "prereg/external_second_model_sample_20261011.json"


def verify_frozen_files(root: Path) -> tuple[dict, str]:
    pin = json.loads((ROOT / "config/webcraft_pin.json").read_text())
    mismatches = []
    for group in pin["roots"].values():
        for relative, expected in group.get("files", {}).items():
            path = root / relative
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual != expected:
                mismatches.append(relative)
    if mismatches:
        raise RuntimeError(f"frozen WebCraft file mismatch: {mismatches}")
    current = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return pin, current


def stable_rank(row: dict) -> str:
    identity = "|".join((str(SEED), row["task_type"], row["condition"], row["world"], row["task_id"]))
    return hashlib.sha256(identity.encode()).hexdigest()


def public_job(method: str, condition: str, world: str, task: dict,
               task_types: dict[str, str]) -> dict:
    return {
        "method": method,
        "condition": condition,
        "world": world,
        "task_id": task["id"],
        "task_type": task_types[task["id"]],
        "public_task": {
            "target": task["target"],
            "standard_items": task["standard_items"],
            "task_supplements": task.get("task_supplements", []),
        },
    }


def main() -> None:
    webcraft = WEBCRAFT.resolve()
    pin, current_commit = verify_frozen_files(webcraft)
    tasks = load_evaluation_tasks(
        webcraft / "data/tasks.jsonl", webcraft / "config/resource_model.json"
    )
    task_types = {
        row["id"]: row["type"]
        for row in (json.loads(line) for line in (webcraft / "data/tasks.jsonl").read_text().splitlines() if line)
    }
    selected = [task for task in tasks if task["conflict_ids"]]
    if len(selected) != 22:
        raise RuntimeError(f"expected 22 conflict-bearing evaluation tasks, got {len(selected)}")

    direct = [public_job("direct_llm", condition, world, task, task_types)
              for condition, world in CELLS for task in selected]
    feasible = [public_job("xenon", condition, world, task, task_types)
                for condition, world in CELLS for task in selected if task["feasible"][world]]
    if len(direct) != 110 or len(feasible) != 72:
        raise RuntimeError(f"unexpected population sizes: Direct={len(direct)}, feasible={len(feasible)}")

    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in feasible:
        strata[(row["task_type"], row["condition"])].append(row)
    quotas = {}
    remainders = []
    assigned = 0
    for key, rows in sorted(strata.items()):
        exact = 40 * len(rows) / len(feasible)
        base = math.floor(exact)
        quotas[key] = base
        assigned += base
        remainders.append((exact - base, key))
    for _, key in sorted(remainders, key=lambda item: (-item[0], item[1]))[:40 - assigned]:
        quotas[key] += 1

    xenon = []
    allocation = []
    for key, rows in sorted(strata.items()):
        rows.sort(key=stable_rank)
        chosen = rows[:quotas[key]]
        xenon.extend(chosen)
        allocation.append({
            "task_type": key[0], "condition": key[1],
            "population": len(rows), "selected": len(chosen),
        })
    if len(xenon) != 40 or len({(r["condition"], r["world"], r["task_id"]) for r in xenon}) != 40:
        raise RuntimeError("stratified sample is not exactly 40 unique feasible instances")

    direct.sort(key=lambda row: (row["condition"], row["world"], row["task_id"]))
    xenon.sort(key=lambda row: (row["condition"], row["world"], row["task_id"]))
    payload = {
        "schema_version": 1,
        "selection_seed": SEED,
        "selection_algorithm": (
            "proportional allocation over task_type x condition; floors first; "
            "largest fractional remainders with lexical ties; SHA256(seed|stratum|instance) rank"
        ),
        "model": "glm-5.2",
        "webcraft_pinned_commit": pin["webcraft_commit"],
        "webcraft_current_commit_at_selection": current_commit,
        "webcraft_commit_drift": current_commit != pin["webcraft_commit"],
        "pin_sha256": hashlib.sha256((ROOT / "config/webcraft_pin.json").read_bytes()).hexdigest(),
        "population": {"conflict_tasks": 22, "direct_instances": 110, "gold_feasible_instances": 72},
        "xenon_allocation": allocation,
        "jobs": direct + xenon,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"direct": len(direct), "xenon": len(xenon), "allocation": allocation}, indent=2))


if __name__ == "__main__":
    main()
