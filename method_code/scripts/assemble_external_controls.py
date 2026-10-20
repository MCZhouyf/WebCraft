"""Validate and assemble completed external-control shards."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.evaluation.external_controls import matrix_jobs


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
EXPECTED = {"oracle_knowledge": 220, "control_all_public": 120, "conditions": 264}


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return row["method"], row["condition"], row["world"], row["task_id"]


def ledger_accounting(directories: list[Path]) -> dict[str, Any]:
    records = []
    for directory in directories:
        ledger = directory / "ledger.jsonl"
        if ledger.is_file():
            records.extend(jsonl(ledger))
    accepted = [row for row in records if row.get("status") == "ok"]
    models = sorted({str(row["model"]) for row in accepted if row.get("model")})
    return {
        "task_calls": sum(not bool(row.get("canary")) for row in accepted),
        "successful_provider_responses": len(accepted),
        "input_tokens": sum(int(row.get("input_tokens", 0) or 0) for row in accepted),
        "output_tokens": sum(int(row.get("output_tokens", 0) or 0) for row in accepted),
        "server_model_identifiers": models,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment", choices=tuple(EXPECTED), required=True)
    parser.add_argument("--parallel-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    parallels = [path.resolve() for path in args.parallel_dir]
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("assembled output directory must be empty")
    rows = []
    sources = []
    accounting = []
    manifests = []
    for parallel in parallels:
        parent_status_path = parallel / "status.json"
        if parent_status_path.is_file():
            parent_status = json.loads(parent_status_path.read_text())
            if parent_status["status"] not in {"completed", "partial"}:
                raise RuntimeError(f"invalid parallel run state: {parallel}")
        manifest = json.loads((parallel / "parallel_manifest.json").read_text())
        manifests.append(manifest)
        directories = {
            ROOT / shard["output_dir"]
            for shard in manifest["shards"] if shard.get("output_dir") is not None
        }
        directories.update(path for path in parallel.glob("chunk_*") if path.is_dir())
        directories = sorted(directories)
        if not directories:
            raise RuntimeError(f"parallel run contains no shard directories: {parallel}")
        run_accounting = ledger_accounting(directories)
        if run_accounting["server_model_identifiers"] != ["glm-5.2"]:
            raise RuntimeError(f"ledger model identifier mismatch: {parallel}")
        accounting.append(run_accounting)
        for directory in directories:
            episodes_path = directory / "episodes.jsonl"
            if not episodes_path.is_file():
                continue
            part = jsonl(directory / "episodes.jsonl")
            run_manifest = json.loads((directory / "run_manifest.json").read_text())
            if len(part) > int(run_manifest["limit"]):
                raise RuntimeError(f"shard cardinality mismatch: {directory}")
            status_path = directory / "status.json"
            if status_path.is_file():
                status = json.loads(status_path.read_text())
                if status["status"] not in {"completed", "partial"}:
                    raise RuntimeError(f"invalid shard state: {directory}")
                if len(part) != status["episodes_completed"]:
                    raise RuntimeError(f"shard status cardinality mismatch: {directory}")
            if any(row["server_model_identifiers"] != ["glm-5.2"] for row in part):
                raise RuntimeError(f"episode model identifier mismatch: {directory}")
            rows.extend(part)
            sources.append(str(directory.relative_to(ROOT)))
    tasks = [row for row in jsonl(WEBCRAFT / "data/tasks.jsonl") if row.get("split") == "eval"]
    expected_rows = matrix_jobs(tasks, args.segment, ("direct_llm", "react"))
    expected = {key(row) for row in expected_rows}
    actual = {key(row) for row in rows}
    if len(rows) != EXPECTED[args.segment] or len(actual) != len(rows) or actual != expected:
        raise RuntimeError("assembled jobs differ from preregistered matrix")
    if any(row["server_model_identifiers"] != ["glm-5.2"] for row in rows):
        raise RuntimeError("assembled rows contain a model identifier mismatch")
    output.mkdir(parents=True)
    ordered = sorted(rows, key=key)
    (output / "episodes.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in ordered)
    )
    counts = Counter(row["method"] for row in ordered)
    status = {
        "status": "completed", "segment": args.segment,
        "episodes_completed": len(ordered), "methods": dict(counts),
        "server_model_identifiers": ["glm-5.2"],
        "task_calls": sum(row["task_calls"] for row in accounting),
        "successful_provider_responses": sum(row["successful_provider_responses"] for row in accounting),
        "input_tokens": sum(row["input_tokens"] for row in accounting),
        "output_tokens": sum(row["output_tokens"] for row in accounting),
        "source_shards": sources,
    }
    (output / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    (output / "assembly_manifest.json").write_text(json.dumps({
        "schema_version": 1, "segment": args.segment,
        "source_parallel_manifests": [
            str((parallel / "parallel_manifest.json").relative_to(ROOT))
            for parallel in parallels
        ],
        "source_shards": sources,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
