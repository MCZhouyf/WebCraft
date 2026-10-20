"""Read-only provenance snapshots and resource checks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_WEBCRAFT = ("truth", "oracle", "metrics", "config", "data", "harness")


def effective_available_bytes() -> int:
    fields = Path("/proc/meminfo").read_text().splitlines()
    host = int(next(x for x in fields if x.startswith("MemAvailable:")).split()[1]) * 1024
    maximum = Path("/sys/fs/cgroup/memory.max")
    current = Path("/sys/fs/cgroup/memory.current")
    if maximum.exists() and current.exists() and maximum.read_text().strip() != "max":
        remaining = int(maximum.read_text()) - int(current.read_text())
        return min(host, remaining)
    return host


def memory_requirement(planned_bytes: int, reserve_bytes: int = 8 * 1024**3) -> int:
    """Return the active resource gate, including an explicit author override."""
    if planned_bytes < 0 or reserve_bytes < 0:
        raise ValueError("memory requirements must be nonnegative")
    authorized_total = os.getenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES")
    if authorized_total is None:
        return planned_bytes + reserve_bytes
    try:
        total = int(authorized_total)
    except ValueError:
        raise ValueError("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES must be an integer") from None
    if total <= 0:
        raise ValueError("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES must be positive")
    return max(planned_bytes, total)


def require_memory(planned_bytes: int, reserve_bytes: int = 8 * 1024**3) -> int:
    available = effective_available_bytes()
    required = memory_requirement(planned_bytes, reserve_bytes)
    if available < required:
        raise RuntimeError(
            f"insufficient effective memory: {available} < {required}"
        )
    return available


def snapshot_webcraft(root: Path, output: Path) -> dict[str, object]:
    root = root.resolve()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True,
        capture_output=True,
    ).stdout.strip()
    hashes: dict[str, str] = {}
    for name in ALLOWED_WEBCRAFT:
        base = root / name
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    record: dict[str, object] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "webcraft_root": str(root),
        "commit": commit,
        "files": hashes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record
