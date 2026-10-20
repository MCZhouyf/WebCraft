import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assemble_external_shards import assemble


def _attempt(root: Path, worker: int, jobs: list[dict], completed: list[dict]) -> None:
    attempt = root / f"worker_{worker}" / "attempt_1"
    attempt.mkdir(parents=True)
    (attempt / "run_manifest.json").write_text(json.dumps({"jobs": jobs}))
    (attempt / "episodes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in completed)
    )


def test_assembly_requires_exact_reference_coverage(tmp_path):
    base = {
        "method": "direct_llm", "condition": "matched_1.16.5", "world": "1.16.5",
    }
    job_a, job_b = ({**base, "task_id": task_id} for task_id in ("a", "b"))
    row_a, row_b = ({**job, "seed": 20261006} for job in (job_a, job_b))
    original = tmp_path / "original"
    continuation = tmp_path / "continuation"
    _attempt(original, 1, [job_a], [row_a])
    _attempt(original, 2, [job_b], [])
    _attempt(continuation, 1, [job_b], [row_b])
    for root in (original, continuation):
        (root / "parallel_manifest.json").write_text("{}\n")

    rows, provenance = assemble(original, [original, continuation])
    assert [row["task_id"] for row in rows] == ["a", "b"]
    assert provenance["exact_global_coverage"] is True
    assert provenance["assembled_jobs"] == 2

    (continuation / "worker_1/attempt_1/episodes.jsonl").write_text("")
    with pytest.raises(RuntimeError):
        assemble(original, [original, continuation])
