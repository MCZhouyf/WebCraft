import argparse
import json
from pathlib import Path

from arbiter.checkpoint import require_memory
from arbiter.pinning import build_pin


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--webcraft-root", type=Path, default=Path("/root/autodl-tmp/webcraft"),
    )
    args = parser.parse_args()
    require_memory(256 * 1024**2, reserve_bytes=2 * 1024**3)
    pin = build_pin(args.webcraft_root.resolve())
    output = Path("config/webcraft_pin.json")
    output.write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "webcraft_commit": pin["webcraft_commit"],
        "backend_ready": pin["backend_ready"],
        "corpus_p0_ready": pin["corpus_p0_ready"],
        "claims_cache_p0_ready": pin["claims_cache_p0_ready"],
        "claims_cache_v3_ready": pin["claims_cache_v3_ready"],
        "claims_cache_v4_ready": pin["claims_cache_v4_ready"],
        "amendment_v6_ready": pin["amendment_v6_ready"],
        "preview_ready": pin["preview_ready"],
        "experiment_ready": pin["experiment_ready"],
    }, sort_keys=True))
