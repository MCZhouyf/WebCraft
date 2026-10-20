"""Run fixed contiguous external-agent shards with one credential per worker."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from arbiter.checkpoint import effective_available_bytes, memory_requirement, require_memory
from arbiter.evaluation.external_agents import EXTERNAL_METHODS


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", choices=EXTERNAL_METHODS, nargs="+", required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--chunk-plan", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--thinking", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--request-interval", type=float, default=15.0)
    parser.add_argument("--worker-start-stagger", type=float, default=5.0)
    parser.add_argument("--rate-limit-cooldown", type=float, default=60.0)
    parser.add_argument("--max-rate-limit-cooldown", type=float, default=600.0)
    parser.add_argument("--max-rate-limit-retries", type=int, default=12)
    parser.add_argument("--transport-cooldown", type=float, default=60.0)
    parser.add_argument("--max-transport-cooldown", type=float, default=600.0)
    parser.add_argument("--max-transport-retries", type=int, default=12)
    parser.add_argument("--memory-threshold-gib", type=float, default=1.8)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--webcraft-root", type=Path, default=Path("/root/autodl-tmp/webcraft"))
    parser.add_argument("--index-dir", type=Path,
                        default=Path("/root/autodl-tmp/webcraft/corpus/indexes/formal_p1"))
    return parser.parse_args()


def _credentials() -> list[str]:
    raw = os.environ.get("WC_LLM_API_KEYS")
    if not raw:
        raise RuntimeError("parallel execution requires WC_LLM_API_KEYS")
    try:
        values = json.loads(raw)
    except ValueError:
        raise RuntimeError("invalid WC_LLM_API_KEYS encoding") from None
    if (not isinstance(values, list) or not values or
            any(not isinstance(value, str) or not value.strip() for value in values)):
        raise RuntimeError("WC_LLM_API_KEYS must be a nonempty JSON string array")
    return list(dict.fromkeys(values))


def _endpoint_configs(credential_count: int) -> list[dict[str, str]]:
    raw = os.environ.get("WC_LLM_ENDPOINTS")
    if raw:
        try:
            values = json.loads(raw)
        except ValueError:
            raise RuntimeError("invalid WC_LLM_ENDPOINTS encoding") from None
        if (not isinstance(values, list) or len(values) != credential_count
                or any(not isinstance(value, dict)
                       or set(value) != {"base_url", "model"} for value in values)):
            raise RuntimeError(
                "WC_LLM_ENDPOINTS must contain one base_url/model object per credential"
            )
    else:
        base_url = os.environ.get("WC_LLM_BASE_URL")
        model = os.environ.get("WC_LLM_MODEL")
        if not base_url or not model:
            raise RuntimeError("common WC_LLM_BASE_URL and WC_LLM_MODEL are required")
        values = [{"base_url": base_url, "model": model} for _ in range(credential_count)]
    configs = []
    for value in values:
        base_url = str(value["base_url"]).rstrip("/")
        model = str(value["model"]).strip()
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc or not model:
            raise RuntimeError("credential endpoints require an HTTPS base URL and model")
        configs.append({"base_url": base_url, "model": model})
    return configs


def _chunks(offset: int, limit: int, workers: int) -> list[tuple[int, int]]:
    if offset < 0 or limit <= 0 or workers <= 0:
        raise ValueError("offset/workers must be nonnegative and limit positive")
    workers = min(workers, limit)
    size = math.ceil(limit / workers)
    chunks = []
    consumed = 0
    while consumed < limit:
        count = min(size, limit - consumed)
        chunks.append((offset + consumed, count))
        consumed += count
    return chunks


def _chunk_plan(path: Path) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    payload = json.loads(path.read_text())
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or not isinstance(payload.get("chunks"), list) or not payload["chunks"]):
        raise ValueError("invalid continuation chunk plan")
    chunks = []
    for row in payload["chunks"]:
        if not isinstance(row, dict) or set(row) != {"offset", "count"}:
            raise ValueError("invalid continuation chunk")
        offset, count = row["offset"], row["count"]
        if type(offset) is not int or type(count) is not int or offset < 0 or count <= 0:
            raise ValueError("invalid continuation chunk bounds")
        chunks.append((offset, count))
    intervals = sorted((offset, offset + count) for offset, count in chunks)
    if any(right_start < left_end for (_, left_end), (right_start, _) in zip(intervals, intervals[1:])):
        raise ValueError("continuation chunks overlap")
    provenance = {
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "reason": payload.get("reason"), "source_run": payload.get("source_run"),
    }
    return chunks, provenance


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _terminate(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(p.poll() is None for p in processes):
        time.sleep(0.2)
    for process in processes:
        if process.poll() is None:
            process.kill()


def _rate_limited(attempt_dir: Path) -> bool:
    ledger = attempt_dir / "ledger.jsonl"
    if not ledger.is_file():
        return False
    return any(
        row.get("http_status") == 429
        for row in (json.loads(line) for line in ledger.read_text().splitlines() if line)
    )


def _rate_limit_retry_after(attempt_dir: Path) -> float | None:
    ledger = attempt_dir / "ledger.jsonl"
    if not ledger.is_file():
        return None
    records = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    hints = [row.get("retry_after_seconds") for row in records if row.get("http_status") == 429]
    numeric = [float(value) for value in hints if isinstance(value, (int, float)) and value >= 0]
    return numeric[-1] if numeric else None


def _transport_interrupted(attempt_dir: Path) -> bool:
    """Return true only for retryable network/5xx infrastructure failures."""
    ledger = attempt_dir / "ledger.jsonl"
    if not ledger.is_file():
        return False
    records = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    # A later protocol/model/canary failure must never inherit retryability.
    if records and records[-1].get("record_type") == "canary_validation":
        records = records[:-1]
    if not records or records[-1].get("status") != "infrastructure_error":
        return False
    latest = records[-1]
    status = latest.get("http_status")
    return (
        latest.get("error_type") in {
            "URLError", "TimeoutError", "RemoteDisconnected", "IncompleteRead",
            "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError",
        }
        or isinstance(status, int) and 500 <= status <= 599
    )


def _merge_rows(attempt_dirs: list[Path], expected_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for attempt in attempt_dirs:
        manifest = json.loads((attempt / "run_manifest.json").read_text())
        expected = [
            (job["method"], job["condition"], job["world"], job["task_id"])
            for job in manifest["jobs"]
        ]
        episodes = attempt / "episodes.jsonl"
        current = ([] if not episodes.is_file() else [
            json.loads(line) for line in episodes.read_text().splitlines() if line
        ])
        actual = [
            (row["method"], row["condition"], row["world"], row["task_id"])
            for row in current
        ]
        if actual != expected[:len(actual)]:
            raise RuntimeError("parallel attempt did not complete its fixed manifest prefix")
        rows.extend(current)
    keys = [(row["method"], row["condition"], row["world"], row["task_id"]) for row in rows]
    if len(rows) != expected_count or len(Counter(keys)) != expected_count:
        raise RuntimeError("parallel workers completed with missing or duplicate jobs")
    rows.sort(key=lambda row: (
        row["method"], row["condition"], row["world"], row["task_id"], row["seed"],
    ))
    return rows


def _rotate_retry_credentials(
    states: list[dict[str, Any]], limited_slots: set[int], credential_count: int,
) -> None:
    """Assign rate-limited shards to distinct credentials freed by completed shards."""
    retrying = [state for state in states if state["slot"] in limited_slots and state["remaining"]]
    fixed = {
        state["credential_index"] for state in states
        if state["remaining"] and state["slot"] not in limited_slots
    }
    available = [index for index in range(credential_count) if index not in fixed]
    for state in sorted(retrying, key=lambda value: value["slot"]):
        current = state["credential_index"]
        ordered = [
            (current + distance) % credential_count
            for distance in range(1, credential_count + 1)
        ]
        selected = next((index for index in ordered if index in available and index != current), None)
        if selected is None:
            selected = next(index for index in ordered if index in available)
        state["credential_index"] = selected
        available.remove(selected)


def main() -> int:
    args = parse_args()
    if (args.rate_limit_cooldown < 0 or args.max_rate_limit_cooldown < args.rate_limit_cooldown
            or args.max_rate_limit_retries < 0
            or args.transport_cooldown < 0
            or args.max_transport_cooldown < args.transport_cooldown
            or args.max_transport_retries < 0
            or not 0 <= args.worker_start_stagger <= 60
            or args.memory_threshold_gib <= 0):
        raise ValueError("invalid rate-limit recovery settings")
    keys = _credentials()
    endpoints = _endpoint_configs(len(keys))
    if args.chunk_plan is not None:
        if args.offset != 0 or args.limit != 0:
            raise ValueError("chunk plan cannot be combined with offset or limit")
        chunks, chunk_provenance = _chunk_plan(args.chunk_plan)
        if len(chunks) > min(args.workers, len(keys)):
            raise ValueError("chunk plan has more shards than workers or credentials")
    else:
        chunks = _chunks(args.offset, args.limit, min(args.workers, len(keys)))
        chunk_provenance = None
    expected_count = sum(count for _, count in chunks)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise RuntimeError("parallel output directory must be empty")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    planned = len(chunks) * 256 * 1024**2
    authorized_total = math.ceil(args.memory_threshold_gib * 1024**3)
    os.environ["ARB_AUTHORIZED_MEMORY_TOTAL_BYTES"] = str(authorized_total)
    required_memory = memory_requirement(planned, 2 * 1024**3)
    while effective_available_bytes() < required_memory:
        print(json.dumps({"status": "waiting_for_memory", "poll_seconds": 600,
                          "required_bytes": required_memory}), flush=True)
        for _ in range(10):
            time.sleep(60)
    available_before = require_memory(planned, reserve_bytes=2 * 1024**3)
    started = time.monotonic()
    manifest = {
        "schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "methods": args.methods, "offset": args.offset, "limit": expected_count,
        "workers": len(chunks), "chunks": [list(chunk) for chunk in chunks],
        "chunk_plan": chunk_provenance,
        "credential_slots": list(range(1, len(chunks) + 1)),
        "credential_endpoints": [
            {"slot": index + 1, **endpoint} for index, endpoint in enumerate(endpoints)
        ],
        "thinking": args.thinking, "request_interval_seconds": args.request_interval,
        "worker_start_stagger_seconds": args.worker_start_stagger,
        "merge_policy": "merge_only_if_all_workers_complete",
        "failure_policy": "429_or_transport_fixed_suffix_after_cooldown; other_errors_terminate_all",
        "rate_limit_cooldown_seconds": args.rate_limit_cooldown,
        "max_rate_limit_cooldown_seconds": args.max_rate_limit_cooldown,
        "max_rate_limit_retries": args.max_rate_limit_retries,
        "transport_cooldown_seconds": args.transport_cooldown,
        "max_transport_cooldown_seconds": args.max_transport_cooldown,
        "max_transport_retries": args.max_transport_retries,
        "aggregate_planned_memory_bytes": planned,
        "authorized_total_memory_bytes": authorized_total,
        "required_memory_bytes": required_memory,
        "worker_planned_memory_bytes": 256 * 1024**2,
        "worker_gate_policy": "parent_authorizes_total; child_checks_own_plan",
    }
    _write(args.output_dir / "parallel_manifest.json", manifest)
    base_env = os.environ.copy()
    base_env.pop("WC_LLM_API_KEYS", None)
    base_env.pop("WC_LLM_API_KEY", None)
    base_env.pop("WC_LLM_ENDPOINTS", None)
    states = [
        {"slot": index, "credential_index": index - 1, "offset": offset,
         "remaining": count, "attempt": 0, "rate_limit_retries": 0,
         "transport_retries": 0,
         "attempt_dirs": [], "attempt_credential_slots": []}
        for index, (offset, count) in enumerate(chunks, 1)
    ]
    fatal_error = None
    while any(state["remaining"] for state in states) and fatal_error is None:
        active = [state for state in states if state["remaining"]]
        processes: list[subprocess.Popen[str]] = []
        logs = []
        attempts: list[tuple[dict[str, Any], Path]] = []
        try:
            for active_index, state in enumerate(active):
                state["attempt"] += 1
                worker_dir = args.output_dir / f"worker_{state['slot']}"
                worker_dir.mkdir(parents=True, exist_ok=True)
                attempt_dir = worker_dir / f"attempt_{state['attempt']}"
                if attempt_dir.exists():
                    raise RuntimeError("parallel attempt output already exists")
                state["attempt_dirs"].append(attempt_dir)
                state["attempt_credential_slots"].append(state["credential_index"] + 1)
                attempts.append((state, attempt_dir))
                log_path = worker_dir / f"attempt_{state['attempt']}.log"
                log_handle = log_path.open("w")
                os.chmod(log_path, 0o600)
                logs.append(log_handle)
                endpoint = endpoints[state["credential_index"]]
                env = {**base_env, "WC_LLM_API_KEY": keys[state["credential_index"]],
                       "WC_LLM_BASE_URL": endpoint["base_url"],
                       "WC_LLM_MODEL": endpoint["model"], "PYTHONPATH": "src",
                       "ARB_AUTHORIZED_MEMORY_TOTAL_BYTES": str(256 * 1024**2),
                       "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                       "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
                command = [
                    "nice", "-n", "15", sys.executable, "scripts/run_external_agents.py",
                    "--methods", *args.methods, "--offset", str(state["offset"]),
                    "--limit", str(state["remaining"]), "--thinking", args.thinking,
                    "--request-interval", str(args.request_interval),
                    "--planned-memory-mib", "256", "--output-dir", str(attempt_dir),
                    "--ledger", str(attempt_dir / "ledger.jsonl"),
                    "--webcraft-root", str(args.webcraft_root), "--index-dir", str(args.index_dir),
                ]
                processes.append(subprocess.Popen(
                    command, cwd=ROOT, env=env, stdout=log_handle,
                    stderr=subprocess.STDOUT, text=True,
                ))
                if active_index + 1 < len(active) and args.worker_start_stagger:
                    time.sleep(args.worker_start_stagger)
            while any(process.poll() is None for process in processes):
                for process, (_, attempt_dir) in zip(processes, attempts):
                    if (process.poll() not in {None, 0}
                            and not _rate_limited(attempt_dir)
                            and not _transport_interrupted(attempt_dir)):
                        fatal_error = f"non_rate_limit_worker_exit_{process.returncode}"
                        _terminate(processes)
                        break
                if fatal_error is not None:
                    break
                time.sleep(1)
        except BaseException:
            _terminate(processes)
            raise
        finally:
            for handle in logs:
                handle.close()
        if fatal_error is not None:
            break
        needs_cooldown = False
        cooldown_seconds = 0.0
        limited_slots: set[int] = set()
        for process, (state, attempt_dir) in zip(processes, attempts):
            status_path = attempt_dir / "status.json"
            status = json.loads(status_path.read_text()) if status_path.is_file() else {}
            completed_now = int(status.get("episodes_completed", 0))
            if completed_now < 0 or completed_now > state["remaining"]:
                fatal_error = "invalid_worker_completion_count"
                break
            state["offset"] += completed_now
            state["remaining"] -= completed_now
            if process.returncode == 0:
                if state["remaining"]:
                    fatal_error = "worker_exited_zero_with_incomplete_chunk"
                    break
                continue
            if _rate_limited(attempt_dir):
                state["rate_limit_retries"] += 1
                if state["rate_limit_retries"] > args.max_rate_limit_retries:
                    fatal_error = "rate_limit_retry_cap_exceeded"
                    break
                hinted = _rate_limit_retry_after(attempt_dir)
                backoff = min(
                    args.rate_limit_cooldown * 2 ** (state["rate_limit_retries"] - 1),
                    args.max_rate_limit_cooldown,
                )
                cooldown_seconds = max(cooldown_seconds, hinted if hinted is not None else backoff)
            elif _transport_interrupted(attempt_dir):
                state["transport_retries"] += 1
                if state["transport_retries"] > args.max_transport_retries:
                    fatal_error = "transport_retry_cap_exceeded"
                    break
                backoff = min(
                    args.transport_cooldown * 2 ** (state["transport_retries"] - 1),
                    args.max_transport_cooldown,
                )
                cooldown_seconds = max(cooldown_seconds, backoff)
            else:
                fatal_error = f"non_rate_limit_worker_exit_{process.returncode}"
                break
            limited_slots.add(state["slot"])
            needs_cooldown = True
        if fatal_error is None and needs_cooldown:
            _rotate_retry_credentials(states, limited_slots, len(keys))
            print(json.dumps({"status": "provider_cooldown", "seconds": cooldown_seconds,
                              "remaining_jobs": sum(state["remaining"] for state in states)}), flush=True)
            time.sleep(cooldown_seconds)
    completed = fatal_error is None and not any(state["remaining"] for state in states)
    rows = []
    if completed:
        attempt_dirs = [path for state in states for path in state["attempt_dirs"]
                        if (path / "run_manifest.json").is_file()]
        rows = _merge_rows(attempt_dirs, expected_count)
        with (args.output_dir / "episodes.jsonl").open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {
        "status": "completed" if completed else "stopped", "fatal_error": fatal_error,
        "worker_attempts": {str(state["slot"]): state["attempt"] for state in states},
        "attempt_credential_slots": {
            str(state["slot"]): state["attempt_credential_slots"] for state in states
        },
        "rate_limit_retries": {str(state["slot"]): state["rate_limit_retries"] for state in states},
        "transport_retries": {str(state["slot"]): state["transport_retries"] for state in states},
        "remaining_jobs": sum(state["remaining"] for state in states),
        "episodes_merged": len(rows) if completed else 0,
        "elapsed_seconds": time.monotonic() - started,
        "available_before_bytes": available_before,
        "available_after_bytes": effective_available_bytes(),
        "peak_parent_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    _write(args.output_dir / "status.json", summary)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
