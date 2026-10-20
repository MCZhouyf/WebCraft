import json
from pathlib import Path

from arbiter.checkpoint import require_memory
from arbiter.inference.synthetic import run_synthetic_validation


if __name__ == "__main__":
    require_memory(2 * 1024**3)
    result = run_synthetic_validation()
    output = Path("artifacts/p2/synthetic_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.__dict__, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result.__dict__, sort_keys=True))
    raise SystemExit(0 if result.passed else 1)

