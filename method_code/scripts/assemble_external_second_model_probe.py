"""Validate and combine immutable second-model probe shards."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "prereg/external_second_model_sample_20261011.json"
DEFAULT_OUTPUT = ROOT / "artifacts/post_review/external_second_model_probe"
EXPECTED = {"direct_llm": 110, "xenon": 40}
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


def episode_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return row["method"], row["condition"], row["world"], row["task_id"]


def add_optional(values: list[float | None]) -> float | None:
    if any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None)


def load_shard(shard: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = load_json(shard / "run_manifest.json")
    rows = load_jsonl(shard / "episodes.jsonl")
    status_path = shard / "status.json"
    if status_path.is_file():
        status = load_json(status_path)
        if status["status"] == "completed":
            if status["episodes_completed"] != len(rows) or manifest["episodes"] != len(rows):
                raise RuntimeError(f"shard episode count mismatch: {shard}")
            status["infrastructure_error_attempts"] = 0
            return manifest, status, rows
        continuation_path = shard / "rate_limit_continuation.json"
        if not continuation_path.is_file():
            raise RuntimeError(f"incomplete shard: {shard}")
        continuation = load_json(continuation_path)
        method = next(iter(status["methods"])) if len(status["methods"]) == 1 else None
        valid = (
            status["status"] == "partial"
            and method == "xenon"
            and status["methods"][method]["stop_reason"] == "infrastructure_retries_exhausted"
            and status["episodes_completed"] == len(rows) == continuation["completed_episode_count"]
            and continuation["http_status"] == 429
            and continuation["infrastructure_attempts"] == 3
            and continuation["accepted_model_responses_for_failed_episode"] == 0
            and continuation["result_record_written_for_failed_episode"] is False
            and continuation["continuation_job_offset"] == manifest["job_offset"] + len(rows)
            and continuation["continuation_job_limit"] == manifest["episodes"] - len(rows)
        )
        if not valid:
            raise RuntimeError(f"invalid rate-limit continuation record: {shard}")
        status["status"] = "completed_infrastructure_fragment"
        status["methods"][method]["status"] = "completed_infrastructure_fragment"
        status["infrastructure_error_attempts"] = continuation["infrastructure_attempts"]
        return manifest, status, rows

    boundary_path = shard / "boundary_stop.json"
    if not boundary_path.is_file():
        raise RuntimeError(f"shard has neither completion nor boundary-stop record: {shard}")
    boundary = load_json(boundary_path)
    required = {
        "status": "operator_shard_split_at_completed_episode_boundary",
        "episodes_completed": len(rows),
        "next_job_model_calls": 0,
    }
    if any(boundary.get(key) != value for key, value in required.items()):
        raise RuntimeError(f"invalid boundary-stop record: {shard}")
    if boundary["ledger_rows_before_stop"] != boundary["ledger_rows_after_stop"]:
        raise RuntimeError(f"a request crossed the boundary stop: {shard}")
    accounting = boundary.get("accounting")
    if not isinstance(accounting, dict):
        raise RuntimeError(f"boundary shard lacks accounting: {shard}")
    status = {
        "status": "completed_boundary_fragment",
        "episodes_completed": len(rows),
        "total_calls_including_canaries": accounting["total_calls_including_canaries"],
        "task_calls": accounting["task_calls"],
        "input_tokens": accounting["input_tokens"],
        "output_tokens": accounting["output_tokens"],
        "actual_provider_cost": accounting["actual_provider_cost"],
        "infrastructure_error_attempts": 0,
        "elapsed_seconds": boundary.get("elapsed_seconds"),
        "peak_rss_after_kib": boundary.get("peak_rss_after_kib"),
        "methods": {
            "xenon": {
                "status": "completed_boundary_fragment",
                "stop_reason": "operator_shard_split_at_completed_episode_boundary",
                "server_model_identifiers": sorted({
                    identifier for row in rows for identifier in row["server_model_identifiers"]
                }),
                "episodes_completed": len(rows),
            }
        },
    }
    return manifest, status, rows


def main() -> None:
    args = parse_args()
    output = args.output_dir.resolve()
    shards = [path.resolve() for path in args.shard]
    if len(set(shards)) != len(shards):
        raise ValueError("duplicate shard directory")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("output directory must be empty")

    selection = load_json(SELECTION)
    expected_jobs = {episode_key(row): row for row in selection["jobs"]}
    if len(expected_jobs) != sum(EXPECTED.values()):
        raise RuntimeError("frozen selection contains duplicate or missing jobs")

    manifests: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for shard in shards:
        manifest, status, rows = load_shard(shard)
        if manifest["model"] != MODEL:
            raise RuntimeError(f"unexpected requested model: {shard}")
        if any(row["server_model_identifiers"] != [MODEL] for row in rows):
            raise RuntimeError(f"unexpected server model identifier: {shard}")
        for row in rows:
            action_log = ROOT / row["action_log"]
            if not action_log.is_file():
                raise RuntimeError(f"missing action log: {action_log}")
        manifests.append(manifest)
        statuses.append(status)
        episodes.extend(rows)

    actual = Counter(row["method"] for row in episodes)
    if actual != Counter(EXPECTED):
        raise RuntimeError(f"combined method counts differ from frozen scope: {actual}")
    actual_by_key = {episode_key(row): row for row in episodes}
    if len(actual_by_key) != len(episodes):
        raise RuntimeError("combined shards contain duplicate episodes")
    if set(actual_by_key) != set(expected_jobs):
        missing = sorted(set(expected_jobs) - set(actual_by_key))
        extra = sorted(set(actual_by_key) - set(expected_jobs))
        raise RuntimeError(f"combined jobs differ from frozen selection: missing={missing}, extra={extra}")

    ordered = [actual_by_key[episode_key(row)] for row in selection["jobs"]]
    common_fields = (
        "selection_sha256", "prereg_sha256", "amendment_sha256",
        "external_prompt_code_sha256", "webcraft_pinned_commit",
    )
    for field in common_fields:
        values = {manifest[field] for manifest in manifests}
        if len(values) != 1:
            raise RuntimeError(f"shards disagree on {field}: {values}")

    methods: dict[str, Any] = {}
    for method, expected_count in EXPECTED.items():
        identifiers = sorted({
            identifier
            for shard_status in statuses
            for state_method, state in shard_status["methods"].items()
            if state_method == method
            for identifier in state["server_model_identifiers"]
        })
        methods[method] = {
            "status": "completed",
            "stop_reason": None,
            "server_model_identifiers": identifiers,
            "episodes_completed": expected_count,
        }
    successful_calls = sum(row["total_calls_including_canaries"] for row in statuses)
    infrastructure_errors = sum(row["infrastructure_error_attempts"] for row in statuses)
    status = {
        "status": "completed",
        "methods": methods,
        "episodes_completed": len(ordered),
        "total_calls_including_canaries": successful_calls,
        "successful_provider_responses": successful_calls,
        "infrastructure_error_attempts": infrastructure_errors,
        "total_http_attempts": successful_calls + infrastructure_errors,
        "task_calls": sum(row["task_calls"] for row in statuses),
        "input_tokens": sum(row["input_tokens"] for row in statuses),
        "output_tokens": sum(row["output_tokens"] for row in statuses),
        "nominal_cost_usd": None,
        "actual_provider_cost": add_optional([row.get("actual_provider_cost") for row in statuses]),
        "elapsed_seconds_max_parallel": max(
            row["elapsed_seconds"] for row in statuses if row.get("elapsed_seconds") is not None
        ),
        "elapsed_seconds_sum_worker": sum(
            row["elapsed_seconds"] for row in statuses if row.get("elapsed_seconds") is not None
        ),
        "peak_rss_after_kib_max_worker": max(
            row["peak_rss_after_kib"] for row in statuses
            if row.get("peak_rss_after_kib") is not None
        ),
        "source_shards": [str(path.relative_to(ROOT)) for path in shards],
    }
    manifest = {
        "schema_version": 1,
        "record_type": "assembled_external_second_model_probe",
        "model": MODEL,
        "episodes": len(ordered),
        "methods": list(EXPECTED),
        "source_shards": [str(path.relative_to(ROOT)) for path in shards],
        "source_code_commits": sorted({row["code_commit"] for row in manifests}),
        **{field: manifests[0][field] for field in common_fields},
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
