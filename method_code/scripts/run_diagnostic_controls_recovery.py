"""Resume only missing A7 diagnostic episodes after transient provider throttling."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from arbiter.checkpoint import effective_available_bytes
from arbiter.evaluation.diagnostic_controls import DIAGNOSTIC_INSTANCES


ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path("/root/.config/scope_credentials/glm52_pending.json")
BASE_HEADROOM = 2 * 1024**3
WORKER_HEADROOM = 512 * 1024**2
CELLS = (("direct_llm", "K"), ("react", "K"), ("react", "K+C"), ("react", "K+N"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--credential-config", type=Path, default=CONFIG)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--max-rounds", type=int, default=12)
    return parser.parse_args()


def completed_keys(paths: list[Path]) -> set[tuple[str, str, str, str, str]]:
    result = set()
    for root in paths:
        for file in root.glob("**/episodes.jsonl"):
            for line in file.read_text().splitlines():
                row = json.loads(line)
                result.add((str(row["method"]), str(row["condition"]), str(row["task_id"]), str(row["world"]), str(row["source_condition"])))
    return result


def missing(paths: list[Path]) -> dict[tuple[str, str], list[dict[str, str]]]:
    seen = completed_keys(paths)
    result: dict[tuple[str, str], list[str]] = {}
    for method, condition in CELLS:
        result[(method, condition)] = [
            {"task_id": task_id, "world": world, "source_condition": source_condition}
            for task_id, world, source_condition in DIAGNOSTIC_INSTANCES
            if (method, condition, task_id, world, source_condition) not in seen
        ]
    return result


def launch(method: str, condition: str, task_ids: list[dict[str, str]], *, slot: int, round_no: int,
           output: Path, config: dict[str, Any], interval: float) -> subprocess.Popen[str]:
    plan = output / "plans" / f"round_{round_no:02d}_{method}_{condition.replace('+', 'plus')}.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps(task_ids, indent=2) + "\n")
    destination = output / f"round_{round_no:02d}_{method}_{condition.replace('+', 'plus')}"
    log = (output / f"round_{round_no:02d}_{method}_{condition.replace('+', 'plus')}.log").open("w")
    env = os.environ.copy()
    env.update({"WC_LLM_API_KEY": config["api_keys"][slot], "WC_LLM_BASE_URL": str(config["base_url"]),
                "WC_LLM_MODEL": "glm-5.2", "ARB_LLM_BUDGET": "1000000", "WC_LLM_PRICE_IN_PER_MTOK": "0",
                "WC_LLM_PRICE_OUT_PER_MTOK": "0", "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PYTHONPATH": str(ROOT / "src")})
    env.pop("WC_LLM_API_KEYS", None)
    command = ["nice", "-n", "15", sys.executable, str(ROOT / "scripts/run_diagnostic_controls_20261016.py"),
               "--method", method, "--condition", condition, "--task-ids-file", str(plan),
               "--output-dir", str(destination), "--ledger", str(destination / "ledger.jsonl"),
               "--request-interval", str(interval)]
    return subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)


def main() -> int:
    args = parse_args()
    source, output = args.source_dir.resolve(), args.output_dir.resolve()
    config = json.loads(args.credential_config.read_text())
    if config.get("model") != "glm-5.2" or len(config.get("api_keys", [])) < 4:
        raise RuntimeError("four distinct glm-5.2 credentials are required for recovery")
    output.mkdir(parents=True, exist_ok=True)
    history = []
    stagnant = 0
    for round_no in range(1, args.max_rounds + 1):
        todo = missing([source, output])
        remaining = sum(len(values) for values in todo.values())
        if not remaining:
            break
        if effective_available_bytes() < BASE_HEADROOM + WORKER_HEADROOM * len([x for x in todo.values() if x]):
            time.sleep(600)
            continue
        before = 128 - remaining
        processes = []
        for slot, ((method, condition), task_ids) in enumerate(todo.items()):
            if task_ids:
                processes.append(launch(method, condition, task_ids, slot=slot, round_no=round_no,
                                        output=output, config=config, interval=args.request_interval))
        codes = [process.wait() for process in processes]
        after = 128 - sum(len(values) for values in missing([source, output]).values())
        history.append({"round": round_no, "before": before, "after": after, "exit_codes": codes})
        (output / "recovery_status.json").write_text(json.dumps({"history": history, "remaining": 128 - after}, indent=2) + "\n")
        stagnant = stagnant + 1 if after == before else 0
        if stagnant >= 3:
            time.sleep(60)
            stagnant = 0
    remaining = sum(len(values) for values in missing([source, output]).values())
    (output / "recovery_status.json").write_text(json.dumps({"history": history, "remaining": remaining}, indent=2) + "\n")
    return 0 if remaining == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
