#!/usr/bin/env python3
"""Evaluate frozen CP-RAG with the complete W7a CDS instance key."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_cp_rag_20261016 as cp  # noqa: E402


FROZEN_HASH = "1385f8fad52f58c4b0efb566c5bfc316b739b67be2e3bc19381f071dd70109d7"


def evaluate_cds(pin, prior, vectors, output):
    tasks = cp.jsonl(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
    public = cp.public_tasks(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
    task_map = {row["id"]: row for row in public}
    raw_map = {row["id"]: row for row in tasks}
    manifest = json.loads((ROOT / "cds_materials/cds_manifest.json").read_text())
    base = cp.baselines("cds")
    rows, records = [], []
    for item in manifest["rows"]:
        private = json.loads((ROOT / item["evaluator_path"]).read_text())
        visible = json.loads((ROOT / item["visible_path"]).read_text())
        for condition in ("K", "K+S", "S", "empty", "K+N"):
            full_condition = f"{private['instance_id']}__{condition}"
            bundle = cp.w7a.cds_bundle(private, visible, condition)
            synthetic = dict(vectors)
            for candidate in private["boundary_truth"]:
                synthetic[candidate["mechanism_id"]] = {
                    world: bool(candidate["truth"])
                    for world in ("1.16.5", "1.19", "1.21.11")
                }
            record = cp.run_cp(
                cp.P6Job(full_condition, private["task_id"], private["target_world"],
                         "cp_rag", cp.SEED),
                task_map[private["task_id"]], bundle, pin=pin, prior=prior,
                vectors=synthetic, output=output / "cds",
            )
            row = cp.metric(
                record, "cds", raw_map[private["task_id"]]["independent_family_id"]
            )
            row["cds_instance_id"] = private["instance_id"]
            row["cds_condition"] = condition
            for method in ("naive_rag", "verify_all"):
                peer = base[(full_condition, private["target_world"],
                             private["task_id"], method)]
                row[f"action_equal_{method}"] = int(
                    cp.normalized_actions(record) == cp.normalized_actions(peer)
                )
                row[f"SR_delta_vs_{method}"] = (
                    int(record["metrics"]["SR"]) - int(peer["metrics"]["SR"])
                )
            rows.append(row)
            records.append(record)
    return rows, records


def main() -> None:
    source_hash = hashlib.sha256(Path(cp.__file__).read_bytes()).hexdigest()
    freeze = json.loads((cp.OUT / "dev_freeze.json").read_text())
    if source_hash != FROZEN_HASH or freeze["implementation_sha256"] != FROZEN_HASH:
        raise RuntimeError("frozen CP-RAG source or dev freeze hash changed")
    pin, prior, vectors = cp.setup()
    with tempfile.TemporaryDirectory(prefix="cp_eval_fixed_", dir="/root/autodl-tmp") as directory:
        temporary = Path(directory)
        main_rows, main_records = cp.evaluate_main(pin, prior, vectors, temporary)
        ext_rows, ext_records = cp.evaluate_ext(pin, prior, vectors, temporary)
        cds_rows, cds_records = evaluate_cds(pin, prior, vectors, temporary)
    rows = main_rows + ext_rows + cds_rows
    cp.write_csv(cp.OUT / "episode_metrics.csv", rows)
    cp.write_csv(cp.OUT / "paired_effects.csv", [
        cp.bootstrap(rows, scope) for scope in ("main", "ext_v2", "cds")
    ])
    with (cp.OUT / "episodes.jsonl").open("w") as handle:
        for record in main_records + ext_records + cds_records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    (cp.OUT / "REPORT.md").write_text(
        "# CP-RAG diagnostic\n\nZero LLM calls. The implementation hash matched the dev8 freeze.\n"
    )


if __name__ == "__main__":
    main()
