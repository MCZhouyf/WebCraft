"""Assemble recovered external-agent shards after exact job-coverage checks."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from run_external_parallel import _merge_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-run", type=Path, required=True)
    parser.add_argument("--source-runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _attempt_dirs(root: Path) -> list[Path]:
    return sorted(
        path.parent for path in root.glob("worker_*/attempt_*/run_manifest.json")
    )


def _job_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return row["method"], row["condition"], row["world"], row["task_id"]


def _expected_jobs(reference_run: Path) -> set[tuple[str, str, str, str]]:
    expected = []
    for attempt in _attempt_dirs(reference_run):
        manifest = json.loads((attempt / "run_manifest.json").read_text())
        expected.extend(_job_key(row) for row in manifest["jobs"])
    if not expected or len(expected) != len(set(expected)):
        raise RuntimeError("reference run has missing or duplicate manifest jobs")
    return set(expected)


def assemble(
    reference_run: Path, source_runs: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = _expected_jobs(reference_run)
    attempts = [attempt for root in source_runs for attempt in _attempt_dirs(root)]
    if not attempts:
        raise RuntimeError("no source attempts found")
    rows = _merge_rows(attempts, len(expected))
    actual = {_job_key(row) for row in rows}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise RuntimeError(
            f"assembled jobs do not match reference: missing={missing}, unexpected={unexpected}"
        )
    sources = []
    for root in source_runs:
        manifest = root / "parallel_manifest.json"
        if not manifest.is_file():
            raise RuntimeError(f"source parallel manifest missing: {manifest}")
        sources.append({
            "path": str(root),
            "parallel_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "attempts": len(_attempt_dirs(root)),
        })
    provenance = {
        "schema_version": 1,
        "reference_run": str(reference_run),
        "expected_jobs": len(expected),
        "assembled_jobs": len(rows),
        "exact_global_coverage": True,
        "sources": sources,
    }
    return rows, provenance


def main() -> None:
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise RuntimeError("assembly output directory must be empty")
    rows, provenance = assemble(args.reference_run, args.source_runs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "episodes.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (args.output_dir / "assembly_manifest.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(provenance, sort_keys=True))


if __name__ == "__main__":
    main()
