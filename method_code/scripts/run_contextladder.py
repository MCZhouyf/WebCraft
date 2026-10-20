import json
from pathlib import Path

from arbiter.checkpoint import require_memory
from arbiter.contextladder import run_grid


if __name__ == "__main__":
    require_memory(3 * 1024**3)
    summary = run_grid(Path("experiments/contextladder/results"))
    print(json.dumps(summary, sort_keys=True))
    raise SystemExit(0 if summary["passed"] else 1)

