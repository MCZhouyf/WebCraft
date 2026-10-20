from pathlib import Path

from arbiter.checkpoint import require_memory, snapshot_webcraft


if __name__ == "__main__":
    require_memory(256 * 1024**2)
    snapshot_webcraft(
        Path("/root/autodl-tmp/webcraft"),
        Path("artifacts/provenance/webcraft_snapshot.json"),
    )

