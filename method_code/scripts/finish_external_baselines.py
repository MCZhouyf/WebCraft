"""Run the remaining approved baselines and export each completed method."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
FINAL = Path("artifacts/post_review/external_agents/final")
STATUS = FINAL / "remaining_queue_status.json"


def record(stage: str, status: str, **fields: object) -> None:
    payload = dict(stage=stage, status=status, timestamp=datetime.now(timezone.utc).isoformat(),
                   pid=os.getpid(), **fields)
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(STATUS)
    print(json.dumps(payload), flush=True)


def execute(stage: str, script: str, *args: str) -> None:
    record(stage, "running")
    result = subprocess.run([sys.executable, f"scripts/{script}", *args], cwd=ROOT)
    if result.returncode:
        record(stage, "stopped", returncode=result.returncode)
        raise SystemExit(result.returncode)


def complete(path: Path, expected: int = 110) -> bool:
    episodes = path / "episodes.jsonl"
    return episodes.is_file() and sum(1 for line in episodes.read_text().splitlines() if line) == expected


def main() -> None:
    os.chdir(ROOT)
    import fcntl
    lock = (FINAL / "remaining_queue.lock").open("a")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    common = ["--thinking", "disabled", "--request-interval", "15",
              "--worker-start-stagger", "5", "--rate-limit-cooldown", "60",
              "--max-rate-limit-cooldown", "600", "--transport-cooldown", "60",
              "--max-transport-cooldown", "600",
              "--memory-threshold-gib", "1.8"]
    if not complete(FINAL / "react_assembled"):
        execute("react", "run_external_parallel.py", "--methods", "react", "--workers", "4",
                "--chunk-plan", "prereg/external_react_continuation_4.json", *common,
                "--output-dir", str(FINAL / "react_continuation_4_retry_1"))
        sources = ["react_retry_1", *[f"react_continuation_{n}" for n in range(1, 4)],
                   "react_continuation_4_retry_1"]
        execute("react_assembly", "assemble_external_shards.py",
                "--reference-run", str(FINAL / "react_retry_1"),
                "--source-runs", *[str(FINAL / name) for name in sources],
                "--output-dir", str(FINAL / "react_assembled"))
    completed = [str(FINAL / "direct_llm_assembled"), str(FINAL / "react_assembled")]
    execute("export_react", "export_external_baselines.py", "--external-runs", *completed)
    if not complete(FINAL / "reflexion_assembled"):
        for number in (3, 2):
            execute(f"reflexion_continuation_{number}", "run_external_parallel.py",
                    "--methods", "reflexion", "--workers", "4", "--chunk-plan",
                    f"prereg/external_reflexion_continuation_{number}.json", *common,
                    "--output-dir", str(FINAL / f"reflexion_continuation_{number}"))
        execute("reflexion_assembly", "assemble_external_shards.py",
                "--reference-run", str(FINAL / "reflexion"), "--source-runs",
                str(FINAL / "reflexion"), str(FINAL / "reflexion_continuation_1"),
                str(FINAL / "reflexion_continuation_3"),
                str(FINAL / "reflexion_continuation_2"),
                "--output-dir", str(FINAL / "reflexion_assembled"))
    completed.append(str(FINAL / "reflexion_assembled"))
    execute("export_reflexion", "export_external_baselines.py", "--external-runs", *completed)
    for method in ("adam", "xenon"):
        output = str(FINAL / method)
        execute(method, "run_external_parallel.py", "--methods", method,
                "--offset", "0", "--limit", "110", "--workers", "6", *common,
                "--output-dir", output)
        completed.append(output)
        execute(f"export_{method}", "export_external_baselines.py", "--external-runs", *completed)
    record("all", "completed", completed_methods=5, expected_episodes=550)


if __name__ == "__main__":
    main()
