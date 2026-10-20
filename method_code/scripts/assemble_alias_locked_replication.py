"""Strictly validate and combine completed alias-locked shards."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "prereg/alias_locked_sample_20261011.json"
DEFAULT_OUTPUT = ROOT / "artifacts/post_review/alias_locked_replication"
EXPECTED = {"react": 47, "reflexion": 10}
MODEL = "glm-5.2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return row["method"], row["condition"], row["world"], row["task_id"]


def add_optional(values: list[float | None]) -> float | None:
    return None if any(value is None for value in values) else sum(float(value) for value in values)


def main() -> None:
    args = parse_args()
    output = args.output_dir.resolve()
    shards = [path.resolve() for path in args.shard]
    if len(shards) != len(set(shards)):
        raise ValueError("duplicate shard directory")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("output directory must be empty")
    selection = load_json(SELECTION)
    expected_jobs = {key(row): row for row in selection["jobs"]}
    if len(expected_jobs) != sum(EXPECTED.values()):
        raise RuntimeError("frozen selection is incomplete or duplicated")

    manifests, statuses, episodes = [], [], []
    for shard in shards:
        manifest = load_json(shard / "run_manifest.json")
        status = load_json(shard / "status.json")
        rows = load_jsonl(shard / "episodes.jsonl")
        if status["status"] != "completed":
            raise RuntimeError(f"incomplete shard: {shard}")
        if status["episodes_completed"] != len(rows) or manifest["episodes"] != len(rows):
            raise RuntimeError(f"shard count mismatch: {shard}")
        if (manifest["model"] != MODEL
                or manifest["required_server_model_identifier"] != MODEL
                or any(row["server_model_identifiers"] != [MODEL] for row in rows)):
            raise RuntimeError(f"model identifier is not locked: {shard}")
        if any(not (ROOT / row["action_log"]).is_file() for row in rows):
            raise RuntimeError(f"missing action log: {shard}")
        manifests.append(manifest)
        statuses.append(status)
        episodes.extend(rows)

    counts = Counter(row["method"] for row in episodes)
    if counts != Counter(EXPECTED):
        raise RuntimeError(f"combined method counts differ from preregistration: {counts}")
    by_key = {key(row): row for row in episodes}
    if len(by_key) != len(episodes) or set(by_key) != set(expected_jobs):
        raise RuntimeError("combined jobs contain duplicates, omissions, or extras")
    ordered = [by_key[key(row)] for row in selection["jobs"]]
    common = (
        "selection_sha256", "prereg_sha256", "external_prompt_code_sha256",
        "webcraft_pinned_commit", "max_model_calls", "max_actions", "temperature",
        "max_tokens", "thinking", "trace_retention_steps", "reading_interface",
    )
    for field in common:
        if len({json.dumps(manifest[field], sort_keys=True) for manifest in manifests}) != 1:
            raise RuntimeError(f"shards disagree on {field}")

    methods = {
        method: {
            "status": "completed",
            "stop_reason": None,
            "server_model_identifiers": [MODEL],
            "episodes_completed": count,
        }
        for method, count in EXPECTED.items()
    }
    successful = sum(status["successful_provider_responses"] for status in statuses)
    status = {
        "status": "completed",
        "methods": methods,
        "episodes_completed": len(ordered),
        "successful_provider_responses": successful,
        "infrastructure_error_attempts": 0,
        "total_http_attempts": successful,
        "task_calls": sum(row["task_calls"] for row in statuses),
        "input_tokens": sum(row["input_tokens"] for row in statuses),
        "output_tokens": sum(row["output_tokens"] for row in statuses),
        "actual_provider_cost": add_optional([row.get("actual_provider_cost") for row in statuses]),
        "elapsed_seconds_max_parallel": max(row["elapsed_seconds"] for row in statuses),
        "elapsed_seconds_sum_worker": sum(row["elapsed_seconds"] for row in statuses),
        "peak_rss_after_kib_max_worker": max(row["peak_rss_after_kib"] for row in statuses),
        "source_shards": [str(path.relative_to(ROOT)) for path in shards],
    }
    manifest = {
        "schema_version": 1,
        "record_type": "assembled_alias_locked_replication",
        "model": MODEL,
        "episodes": len(ordered),
        "methods": list(EXPECTED),
        "source_shards": status["source_shards"],
        "source_code_commits": sorted({row["code_commit"] for row in manifests}),
        **{field: manifests[0][field] for field in common},
    }
    output.mkdir(parents=True)
    (output / "episodes.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in ordered)
    )
    (output / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
