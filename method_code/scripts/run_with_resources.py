"""Run one authorized deterministic check and record resource provenance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import subprocess

from arbiter.checkpoint import effective_available_bytes, require_memory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--planned-mib", type=int, default=512)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")

    planned = args.planned_mib * 1024**2
    reserve = 2 * 1024**3
    authorized_total = os.getenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES")
    effective_required = (
        max(planned, int(authorized_total))
        if authorized_total is not None else planned + reserve
    )
    available_before = require_memory(planned, reserve_bytes=reserve)
    started = datetime.now(timezone.utc)
    result = subprocess.run(command, check=False)
    finished = datetime.now(timezone.utc)
    record = {
        "schema_version": 1,
        "command": command,
        "exit_code": result.returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": (finished - started).total_seconds(),
        "planned_bytes": planned,
        "reserve_bytes": reserve,
        "authorized_total_bytes": (
            int(authorized_total) if authorized_total is not None else None
        ),
        "effective_required_bytes": effective_required,
        "effective_available_before": available_before,
        "effective_available_after": effective_available_bytes(),
        "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
