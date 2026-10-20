"""Finalize the exact XENON continuation after its detached runner exits."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "artifacts/post_review/external_agents/final"
STATUS = FINAL / "xenon_continuation_finalize_status.json"
sys.path.insert(0, str(ROOT / "src"))

from run_external_parallel import _merge_rows


def record(status: str, **fields: object) -> None:
    payload = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        **fields,
    }
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(STATUS)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def attempt_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("worker_*/attempt_*/run_manifest.json"))


def job_key(row: dict[str, object]) -> tuple[object, object, object, object]:
    return row["method"], row["condition"], row["world"], row["task_id"]


def expected_jobs(original: Path) -> set[tuple[object, object, object, object]]:
    jobs = []
    workers = sorted(original.glob("worker_*"), key=lambda path: int(path.name.split("_")[1]))
    for worker in workers:
        manifest = json.loads((worker / "attempt_1/run_manifest.json").read_text())
        jobs.extend(job_key(row) for row in manifest["jobs"])
    if len(jobs) != 110 or len(set(jobs)) != 110:
        raise RuntimeError("original XENON manifests do not define 110 unique jobs")
    return set(jobs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner-pid", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    args = parser.parse_args()
    if args.runner_pid <= 0 or args.poll_seconds <= 0:
        raise ValueError("invalid finalizer arguments")
    record("waiting", runner_pid=args.runner_pid)
    while process_exists(args.runner_pid):
        time.sleep(args.poll_seconds)

    original = FINAL / "xenon"
    continuation = FINAL / "xenon_continuation_1"
    continuation_status = json.loads((continuation / "status.json").read_text())
    if continuation_status.get("status") != "completed":
        record("continuation_incomplete", runner_pid=args.runner_pid,
               continuation_status=continuation_status)
        return 2

    expected = expected_jobs(original)
    attempts = attempt_dirs(original) + attempt_dirs(continuation)
    rows = _merge_rows(attempts, len(expected))
    actual = {job_key(row) for row in rows}
    if actual != expected:
        raise RuntimeError("assembled XENON jobs differ from the frozen manifest")

    output = FINAL / "xenon_assembled"
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("XENON assembly output is not empty")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "episodes.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    provenance = {
        "schema_version": 1,
        "expected_jobs": 110,
        "assembled_jobs": len(rows),
        "exact_global_coverage": True,
        "sources": [str(original), str(continuation)],
    }
    (output / "assembly_manifest.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )

    runs = [
        FINAL / "direct_llm_assembled",
        FINAL / "react_assembled",
        FINAL / "reflexion_assembled",
        FINAL / "adam",
        output,
    ]
    subprocess.run(
        [sys.executable, "scripts/export_external_baselines.py", "--external-runs",
         *map(str, runs)], cwd=ROOT, check=True,
    )
    record("completed", assembled_jobs=len(rows), external_methods=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
