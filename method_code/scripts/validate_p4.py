import json
from pathlib import Path

from arbiter.checkpoint import require_memory
from arbiter.evaluation.p4_validation import run


if __name__ == "__main__":
    require_memory(3 * 1024**3)
    result = run(
        Path("/root/autodl-tmp/webcraft"),
        Path("src/arbiter/agent"),
        Path("src/arbiter/adapters"),
    )
    output = Path("artifacts/p4/validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)
