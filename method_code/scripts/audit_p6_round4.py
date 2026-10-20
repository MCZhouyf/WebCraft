"""Audit the authorized cache-only P6 rerun from saved development artifacts."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path

from arbiter.adapters.claims import validate_claim_rows
from arbiter.evaluation.p6_matrix import one_sided_conflict_ids, result_subsets
from arbiter.evaluation.reporting import load_episode_records
from arbiter.fairness import scan_agent_tree
from arbiter.pinning import verify_webcraft_pin


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    root = Path("/root/autodl-tmp/webcraft")
    output = Path("artifacts/p6_round4_pipeline")
    baseline = Path("artifacts/p6_round3_pipeline")
    pin = verify_webcraft_pin(Path("config/webcraft_pin.json"), root)
    freeze_path = root / "prereg/corpus_freeze.md"
    section = freeze_path.read_text().split("## v5 (redirect and source-mechanism repair)", 1)[1]
    directory_hashes = {}
    for title, directory in (("Index", "corpus/indexes/formal"), ("Cache", "corpus/claims_cache")):
        table = section.split("| " + title + " |", 1)[1].split("\n\n", 1)[0]
        for line in table.splitlines():
            match = re.search(r"\| `([^`]+)` .*`([a-f0-9]{64})`", line)
            if not match:
                continue
            name, expected = match.groups()
            base = root / directory / name
            hasher = hashlib.sha256()
            for file in sorted(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                hasher.update(file.relative_to(base).as_posix().encode())
                hasher.update(b"\0")
                hasher.update(file.read_bytes())
            actual = hasher.hexdigest()
            assert actual == expected, (directory, name)
            directory_hashes[str(base.relative_to(root))] = actual
    assert len(directory_hashes) == 12
    old_pin = json.loads(subprocess.check_output(["git", "show", "b30299f:config/webcraft_pin.json"]))
    for name in ("sim", "truth", "data", "config"):
        assert old_pin["roots"][name] == pin["roots"][name], name

    immutable = [
        "config/arbiter_hparams.json", "config/p6_development_tasks.json",
        "src/arbiter/agent", "src/arbiter/inference",
        "src/arbiter/evaluation/p6_execution.py", "src/arbiter/evaluation/p6_matrix.py",
        "scripts/run_p6.py", "scripts/analyze_p6_round3.py",
    ]
    changed = subprocess.check_output(["git", "diff", "b30299f", "--", *immutable])
    assert not changed, "round 3 method/reader/gate configuration changed"
    violations = scan_agent_tree(Path("src/arbiter/agent")) + scan_agent_tree(Path("src/arbiter/adapters"))
    assert not violations, violations
    assert digest(output / "run_manifest.json") == digest(baseline / "run_manifest.json")

    bundles = [json.loads(path.read_text()) for path in sorted((output / "evidence").glob("*.json"))]
    assert len(bundles) == 24
    raw_claims = 0
    for bundle in bundles:
        assert bundle["claim_source"] == "precomputed_cache"
        assert bundle["reader"]["search_count"] <= 40
        hits = {hit["chunk_id"] for hit in bundle["search_rows"]}
        for claim in bundle["claims"]:
            assert claim["chunk_id"] in hits
            validate_claim_rows([claim], claim["chunk_id"])
            raw_claims += 1
    assert not (output / "preview_claim_cache").exists()
    records = load_episode_records(output / "episodes.jsonl")
    assert len(records) == 144
    subsets = result_subsets(records, one_sided_conflict_ids(freeze_path))
    subset_rows = []
    for name, values in subsets.items():
        for method in ("naive_rag", "arbiter"):
            rows = [row for row in values if row.method == method]
            subset_rows.append({
                "subset": name, "method": method, "episodes": len(rows),
                "SR": sum(row.sr for row in rows) / len(rows),
                "HTR": sum(row.htr for row in rows) / len(rows),
                "mean_probes": sum(row.probes for row in rows) / len(rows),
                "mean_searches": sum(row.searches for row in rows) / len(rows),
            })
    write_csv(output / "subset_summary.csv", subset_rows)
    comparison = []
    for label, directory in (("round3", baseline), ("round4", output)):
        for row in read_csv(directory / "diagnosis/method_condition.csv"):
            comparison.append({"round": label, **row})
    write_csv(output / "diagnosis/method_condition_before_after.csv", comparison)

    audit = {
        "webcraft_commit": pin["webcraft_commit"],
        "arbiter_execution_commit": "6a4f918",
        "baseline_commit": "b30299f",
        "claims_contract": "v2", "claims_fields": 11,
        "claims_cache_v3_ready": pin["claims_cache_v3_ready"],
        "freeze_sha256": digest(freeze_path),
        "pin_sha256": digest(Path("config/webcraft_pin.json")),
        "directory_hashes": directory_hashes,
        "method_reader_gate_hparams_unchanged": True,
        "method_hparam_tuning_rounds": 0,
        "backend_truth_tasks_resources_unchanged": True,
        "fairness_violations": violations,
        "episodes": len(records), "evidence_bundles": len(bundles),
        "validated_claim_occurrences": raw_claims,
        "manifest_sha256": digest(output / "run_manifest.json"),
        "episodes_sha256": digest(output / "episodes.jsonl"),
        "llm_calls": 0, "llm_cost_cny": 0,
        "fallback_used": False,
        "subset_summary": subset_rows,
    }
    (output / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"audit_passed": True, "episodes": len(records), "subset_summary": subset_rows}))


if __name__ == "__main__":
    main()
