"""Replace the completed four-key queue with a pre-authorized six-key queue."""
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
QUEUE_STATUS = FINAL / "remaining_queue_status.json"
HANDOFF_STATUS = FINAL / "six_key_handoff_status.json"


def write_status(status: str, **fields: object) -> None:
    payload = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        **fields,
    }
    temporary = HANDOFF_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(HANDOFF_STATUS)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-queue-pid", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.old_queue_pid <= 0 or args.poll_seconds <= 0:
        raise ValueError("invalid handoff arguments")
    keys = json.loads(os.environ.get("WC_LLM_API_KEYS", "null"))
    endpoints = json.loads(os.environ.get("WC_LLM_ENDPOINTS", "null"))
    if not isinstance(keys, list) or len(keys) != 6 or len(set(keys)) != 6:
        raise RuntimeError("handoff requires six distinct credentials")
    if not isinstance(endpoints, list) or len(endpoints) != 6:
        raise RuntimeError("handoff requires six endpoint records")
    if any("siliconflow" in str(row.get("base_url", "")).lower() for row in endpoints):
        raise RuntimeError("SiliconFlow is excluded from the six-key handoff")
    write_status("waiting_for_reflexion", old_queue_pid=args.old_queue_pid)
    while True:
        state = json.loads(QUEUE_STATUS.read_text()) if QUEUE_STATUS.is_file() else {}
        if (state.get("stage") == "adam" and state.get("status") == "stopped"
                and not process_exists(args.old_queue_pid)):
            break
        if not process_exists(args.old_queue_pid):
            write_status("stopped_unexpectedly", old_queue_pid=args.old_queue_pid,
                         queue_state=state)
            raise SystemExit(2)
        time.sleep(args.poll_seconds)
    hold = FINAL / "adam"
    marker = hold / ".six_key_handoff_hold"
    archived = FINAL / "adam_four_key_hold_20261010"
    if not marker.is_file() or set(hold.iterdir()) != {marker} or archived.exists():
        write_status("invalid_hold_state")
        raise SystemExit(2)
    hold.rename(archived)
    log = (ROOT / "logs/post_review/remaining_queue_20261010_six_key.log").open("x")
    process = subprocess.Popen(
        ["nice", "-n", "15", sys.executable, "scripts/finish_external_baselines.py"],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, env=os.environ.copy(),
    )
    write_status("launched_six_key_queue", queue_pid=process.pid,
                 excluded_provider="siliconflow", credential_count=6)


if __name__ == "__main__":
    main()
