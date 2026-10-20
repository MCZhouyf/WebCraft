#!/usr/bin/env python3
"""Assemble the anonymous, review-only WebCraft release without raw pages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARBITER = ROOT.parent / "arbiter"
RELEASE = ROOT / "release"
PACKAGE = RELEASE / "package"


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_tree(source: Path, target: Path, *, suffixes: tuple[str, ...] = (".py", ".md", ".json", ".csv", ".jsonl")) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        copy_file(path, target / path.relative_to(source))


def deterministic_tar(sources: list[tuple[Path, str]], target: Path) -> None:
    with tarfile.open(target, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for source, prefix in sources:
            for path in sorted(source.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                info = archive.gettarinfo(str(path), arcname=str(Path(prefix) / path.relative_to(source)))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with path.open("rb") as handle:
                    archive.addfile(info, handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    password_file = Path(os.environ.get("WEBCRAFT_POLLUTION_PASSWORD_FILE", ""))
    if not password_file.is_file():
        raise SystemExit("set WEBCRAFT_POLLUTION_PASSWORD_FILE to a readable password file outside the repository")
    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)
    PACKAGE.mkdir(parents=True)
    copy_file(RELEASE / "LICENSE", PACKAGE / "LICENSE")

    for name in ("cost_model.json", "resource_model.json", "recipe_type_policy.json"):
        copy_file(ROOT / "config" / name, PACKAGE / "config" / name)
    copy_file(ROOT / "harness/config/arena.json", PACKAGE / "harness/config/arena.json")
    for name in ("tasks.jsonl", "conflicts.jsonl", "stale_agent_expectations.jsonl"):
        copy_file(ROOT / "data" / name, PACKAGE / "data" / name)
    for name in ("1.16.5.json", "1.19.json", "1.21.11.json", "minecraft_data_diff.json", "separator_empirical.json", "separator_truth.json"):
        copy_file(ROOT / "truth" / name, PACKAGE / "truth" / name)
    for version in ("1.16.5", "1.19", "1.21.11"):
        path = PACKAGE / "truth" / f"{version}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.get("source", {})["server_jar"] = f"servers/{version}/server.jar"
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    copy_tree(ROOT / "sim", PACKAGE / "sim")
    copy_file(ROOT / "oracle/__init__.py", PACKAGE / "oracle/__init__.py")
    copy_file(ROOT / "oracle/planner.py", PACKAGE / "oracle/planner.py")
    copy_tree(ROOT / "metrics", PACKAGE / "metrics")
    copy_tree(ROOT / "tests" / "sim", PACKAGE / "tests" / "sim")
    (PACKAGE / "tests/sim/__init__.py").unlink(missing_ok=True)
    for name in ("search_backend.py", "claims_cache.py", "structured_claims_cache.py"):
        copy_file(ROOT / "corpus" / name, PACKAGE / "corpus" / name)

    # Derived public conditions are plaintext. The polluted condition is encrypted below.
    public_conditions = (
        "matched_1.16.5", "matched_1.19", "matched_1.21.11",
        "mismatched_new2old", "mismatched_old2new", "mixed",
    )
    for condition in public_conditions:
        copy_tree(ROOT / "corpus/claims_cache/formal_p1" / condition,
                  PACKAGE / "corpus/claims_cache/formal_p1" / condition, suffixes=(".jsonl", ".json"))
        copy_tree(ROOT / "corpus/indexes/formal_p1" / condition,
                  PACKAGE / "corpus/indexes/formal_p1" / condition, suffixes=(".jsonl", ".json"))

    copy_tree(ROOT / "prereg", PACKAGE / "prereg", suffixes=(".md", ".json", ".csv"))
    copy_tree(ARBITER / "src/arbiter/adapters", PACKAGE / "method_code/adapters", suffixes=(".py",))
    copy_tree(ARBITER / "src/arbiter/evaluation", PACKAGE / "method_code/evaluation", suffixes=(".py",))
    copy_tree(ARBITER / "config", PACKAGE / "method_code/config", suffixes=(".json",))
    copy_file(ARBITER / "pyproject.toml", PACKAGE / "method_code/pyproject.toml")

    result_files = (
        "S7_primary_matrix_v2.csv", "S7_secondary_metrics.csv", "S7_IAA_v2.csv",
        "w5/task_mechanism_map.csv", "w5/mechanism_results.csv", "w5/four_counts.csv",
        "w5/figure3a_always_abstain.csv", "w5/table2_real_costs.csv",
        "w5/appendix_confusion_v3_v3d_compact.csv", "w5/figure2_task_count_audit.csv",
        "w5/independent_mechanism_evidence.csv", "w5/ext_candidates.csv",
        "w5/REPORT.md", "w5/SOURCE_HASHES.json",
    )
    for name in result_files:
        copy_file(ROOT / "reports/paperA" / name, PACKAGE / "results" / name)
    for name in ("pipeline_episodes_v3.csv", "external_episodes_v3.csv", "confusion_v3.csv"):
        copy_file(ROOT / "reports/paperA/inputs/w5" / name, PACKAGE / "results/per_instance" / name)
    copy_file(ROOT / "reports/paperA/release_attribution_manifest.csv", PACKAGE / "corpus/source_attribution.csv")

    # The frozen implementation records its original absolute root. Release copies are
    # made relocatable without changing the frozen source files.
    for path in list((PACKAGE / "sim").glob("*.py")) + [PACKAGE / "oracle/planner.py"]:
        body = path.read_text(encoding="utf-8")
        body = body.replace("ROOT = Path('/root/autodl-tmp/webcraft')", "ROOT = Path(__file__).resolve().parents[1]")
        body = body.replace("FORMAL_ROOT = ROOT / 'corpus' / 'indexes' / 'formal'", "FORMAL_ROOT = ROOT / 'corpus' / 'indexes' / 'formal_p1'")
        path.write_text(body, encoding="utf-8")
    for path in (PACKAGE / "tests/sim").glob("*.py"):
        body = path.read_text(encoding="utf-8").replace(
            "ROOT = Path('/root/autodl-tmp/webcraft')", "ROOT = Path(__file__).resolve().parents[2]"
        )
        path.write_text(body, encoding="utf-8")

    encrypted = PACKAGE / "corpus/encrypted/synthetic_pollution.tar.gz.enc"
    encrypted.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="webcraft-pollution-") as temp_dir:
        archive = Path(temp_dir) / "synthetic_pollution.tar.gz"
        deterministic_tar([
            (ROOT / "corpus/claims_cache/formal_p1/polluted", "claims_cache/formal_p1/polluted"),
            (ROOT / "corpus/indexes/formal_p1/polluted", "indexes/formal_p1/polluted"),
        ], archive)
        subprocess.run([
            "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
            "-in", str(archive), "-out", str(encrypted), "-pass", f"file:{password_file}",
        ], check=True)

    environment = """# Runtime lock used for the anonymous review package
python==3.10.8
pytest==9.0.3
numpy==2.2.6
scipy==1.15.3
scikit-learn==1.7.2
node==20.15.1
backend=deterministic-cpu
gpu_required=false
"""
    (PACKAGE / "ENVIRONMENT.lock").write_text(environment, encoding="utf-8")
    reproduce = """#!/usr/bin/env bash
set -euo pipefail
export CUDA_VISIBLE_DEVICES=''
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pytest -q tests/sim metrics/test_episode.py
python3 - <<'PY'
from sim.runner import load_tasks, run_oracle
result = run_oracle(load_tasks()['eff_007'], '1.21.11')
assert result['success'], result
print({'smoke_task': 'eff_007', 'world': '1.21.11', 'primary_cost': result['primary_cost']})
PY
"""
    (PACKAGE / "reproduce.sh").write_text(reproduce, encoding="utf-8")
    (PACKAGE / "reproduce.sh").chmod(0o755)
    readme = """# WebCraft-Versions Anonymous Review Package

This package contains frozen truth, tasks and expectations; the deterministic executor and scorer; derived claim caches and frozen snippet indexes; method prompts/adapters; per-instance exports; tests; and preregistration/amendment records. It contains no original web-page snapshots, credentials, endpoint addresses, model weights, or private evaluator metadata.

Run `./reproduce.sh` from this directory for the CPU-only simulator test suite and one oracle smoke task. The main pipeline is deterministic and made zero online LLM calls. External-agent exports are included for result audit, but reproducing those calls requires author-supplied endpoints and is not part of the one-click CPU path.

The `polluted` retrieval condition is stored only as `corpus/encrypted/synthetic_pollution.tar.gz.enc` (AES-256-CBC/PBKDF2). Its password is not in this repository and will be supplied to reviewers through the submission system. Non-polluted `documents.jsonl` files are frozen retrieval snippets, not original pages; source attribution and license metadata are in `corpus/source_attribution.csv`.

Resource status: deterministic backend, CPU only, no GPU, approximately 2 GB free memory recommended for the full tests. No remote repository is configured or pushed by this builder.
"""
    (PACKAGE / "README.md").write_text(readme, encoding="utf-8")

    manifest_path = PACKAGE / "MANIFEST.sha256"
    entries = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and path != manifest_path:
            entries.append(f"{sha256(path)}  {path.relative_to(PACKAGE)}")
    manifest_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

    release_manifest = RELEASE / "MANIFEST.sha256"
    release_entries = []
    for path in sorted(RELEASE.rglob("*")):
        if path.is_file() and path != release_manifest:
            release_entries.append(f"{sha256(path)}  {path.relative_to(ROOT)}")
    release_manifest.write_text("\n".join(release_entries) + "\n", encoding="utf-8")
    print({"files": len(entries), "bytes": sum(path.stat().st_size for path in PACKAGE.rglob("*") if path.is_file())})


if __name__ == "__main__":
    main()
