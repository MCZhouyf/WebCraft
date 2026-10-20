"""Freeze the authorized ReAct/Reflexion minority-alias replication scope."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/main/post_review/alias_stratified/rerun_instances.csv"
PUBLIC_SAMPLE = ROOT / "prereg/external_second_model_sample_20261011.json"
OUT_CSV = ROOT / "prereg/alias_locked_instances_20261011.csv"
OUT_JSON = ROOT / "prereg/alias_locked_sample_20261011.json"
METHODS = {"react": 47, "reflexion": 10}
MINORITY_IDENTIFIER = "zai-org/GLM-5.2"
LOCKED_IDENTIFIER = "glm-5.2"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    rows = [
        row for row in csv.DictReader(SOURCE.open())
        if row["method"] in METHODS
    ]
    rows.sort(key=lambda row: (row["method"], row["condition"], row["world"], row["task_id"]))
    if {method: sum(row["method"] == method for row in rows) for method in METHODS} != METHODS:
        raise RuntimeError("authorized minority-stratum counts changed")
    if any(row["returned_model_identifier"] != MINORITY_IDENTIFIER for row in rows):
        raise RuntimeError("minority identifier changed")
    if len({(row["method"], row["instance_id"]) for row in rows}) != sum(METHODS.values()):
        raise RuntimeError("duplicate authorized instance")

    public = json.loads(PUBLIC_SAMPLE.read_text())
    by_instance = {
        (row["condition"], row["world"], row["task_id"]): row
        for row in public["jobs"] if row["method"] == "direct_llm"
    }
    jobs = []
    for row in rows:
        key = row["condition"], row["world"], row["task_id"]
        if key not in by_instance:
            raise RuntimeError(f"authorized instance absent from frozen public sample: {key}")
        source = by_instance[key]
        jobs.append({
            "method": row["method"],
            "instance_id": row["instance_id"],
            "task_id": row["task_id"],
            "task_type": source["task_type"],
            "condition": row["condition"],
            "world": row["world"],
            "seed": int(row["seed"]),
            "public_task": source["public_task"],
        })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "schema_version": 1,
        "requested_model": LOCKED_IDENTIFIER,
        "required_server_model_identifier": LOCKED_IDENTIFIER,
        "source_minority_identifier": MINORITY_IDENTIFIER,
        "source_rerun_instances_sha256": sha256(SOURCE),
        "source_public_sample_sha256": sha256(PUBLIC_SAMPLE),
        "methods": METHODS,
        "episodes": len(jobs),
        "jobs": jobs,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instances": str(OUT_CSV.relative_to(ROOT)),
        "instances_sha256": sha256(OUT_CSV),
        "sample": str(OUT_JSON.relative_to(ROOT)),
        "sample_sha256": sha256(OUT_JSON),
        "counts": METHODS,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
