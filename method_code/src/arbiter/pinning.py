"""Experiment-start pin verification, kept outside runtime agent/adapters."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


PIN_TARGETS = (
    "sim", "truth", "data", "config", "corpus/indexes/formal",
    "corpus/indexes/formal_p1", "corpus/claims_cache.py",
    "corpus/claims_cache", "corpus/claims_cache/formal_p1", "corpus/search_backend.py",
    "corpus/normalize_claims_cache.py", "corpus/cache_extractor_counts.py",
    "corpus/CLAIM_PAYLOAD_SCHEMA.md", "corpus/gold_claims.py",
    "reports/corpus_stats/cache_extractor_counts.csv",
    "reports/corpus_stats/FREEZE_MANIFEST.md",
    "reports/corpus_stats/solvability_matcher_rules.md",
    "reports/paperA/FORMAL_EXPERIMENT_READINESS.md",
    "reports/paperA/MAIN_TABLE_INPUT_CONTRACT.md", "prereg",
)
PIN_EXCLUDES = ("__pycache__", "corpus/raw", "corpus/private")
_LINE_PREFIX = r"^\s*(?:[-+*]\s+)?(?:\*\*)?"
_LINE_SUFFIX = r"(?:\*\*)?\s*$"
STAGE1_READY_RE = re.compile(
    _LINE_PREFIX + r"STAGE1_READY_FOR_PILOT" + _LINE_SUFFIX,
    re.MULTILINE,
)
SIM_BACKEND_RE = re.compile(
    _LINE_PREFIX + r"backend\s*=\s*sim" + _LINE_SUFFIX,
    re.MULTILINE,
)
COMBINED_BACKEND_RE = re.compile(
    _LINE_PREFIX + r"STAGE1_READY_FOR_PILOT\s*\(backend\s*=\s*sim\)" + _LINE_SUFFIX,
    re.MULTILINE,
)
STAGE2_READY_RE = re.compile(
    _LINE_PREFIX + r"STAGE2_P0_READY" + _LINE_SUFFIX,
    re.MULTILINE,
)
CLAIMS_CACHE_READY_RE = re.compile(
    _LINE_PREFIX + r"CLAIMS_CACHE_P0_READY" + _LINE_SUFFIX,
    re.MULTILINE,
)
CLAIMS_CACHE_V3_READY_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:[-+*]\s+)?(?:\*\*)?"
    r"CLAIMS_CACHE_V3_READY(?:\*\*)?\s*$",
    re.MULTILINE,
)
CLAIMS_CACHE_V4_READY_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:[-+*]\s+)?(?:\*\*)?"
    r"CLAIMS_CACHE_V4_READY(?:\*\*)?\s*$",
    re.MULTILINE,
)


class PinMismatch(RuntimeError):
    pass


def _excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if "__pycache__" in relative.parts:
        return True
    text = relative.as_posix()
    return text.startswith("corpus/raw/") or text.startswith("corpus/private/")


def _hash_roots(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in PIN_TARGETS:
        base = root / name
        files = {}
        if base.is_dir():
            for path in sorted(p for p in base.rglob("*") if p.is_file()):
                if _excluded(path, root):
                    continue
                files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif base.is_file() and not _excluded(base, root):
            files[name] = hashlib.sha256(base.read_bytes()).hexdigest()
        result[name] = {"exists": base.exists(), "files": files}
    return result


def build_pin(root: Path) -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, check=True,
        capture_output=True,
    ).stdout.strip()
    stage1 = (root / "reports/STAGE1_REPORT.md").read_text() if (root / "reports/STAGE1_REPORT.md").is_file() else ""
    stage2 = (root / "reports/STAGE2_REPORT.md").read_text() if (root / "reports/STAGE2_REPORT.md").is_file() else ""
    roots = _hash_roots(root)
    backend_ready = bool(
        roots["sim"]["exists"]
        and (COMBINED_BACKEND_RE.search(stage1)
             or (STAGE1_READY_RE.search(stage1) and SIM_BACKEND_RE.search(stage1)))
    )
    corpus_ready = bool(
        roots["corpus/indexes/formal"]["exists"]
        and (root / "corpus/search_backend.py").is_file()
        and STAGE2_READY_RE.search(stage2)
    )
    claims_ready = bool(
        (root / "corpus/claims_cache.py").is_file()
        and CLAIMS_CACHE_READY_RE.search(stage2)
    )
    preview_ready = backend_ready and corpus_ready
    claims_v3_ready = bool(claims_ready and CLAIMS_CACHE_V3_READY_RE.search(stage2))
    claims_v4_ready = bool(claims_ready and CLAIMS_CACHE_V4_READY_RE.search(stage2))
    amendment_v6_ready = bool(
        roots["prereg"]["exists"] and (root / "prereg/amendment_v6.md").is_file()
    )
    return {
        "schema_version": 2,
        "webcraft_commit": commit,
        "excludes": list(PIN_EXCLUDES),
        "roots": roots,
        "backend_ready": backend_ready,
        "corpus_p0_ready": corpus_ready,
        "claims_cache_p0_ready": claims_ready,
        "claims_cache_v3_ready": claims_v3_ready,
        "claims_cache_v4_ready": claims_v4_ready,
        "amendment_v6_ready": amendment_v6_ready,
        "preview_ready": preview_ready,
        "experiment_ready": preview_ready and claims_v4_ready and amendment_v6_ready,
    }


def verify_webcraft_pin(
    pin_path: Path, root: Path, *, require_ready: bool = True,
    allow_preview: bool = False,
) -> dict[str, object]:
    expected = json.loads(pin_path.read_text())
    actual = build_pin(root)
    if expected != actual:
        raise PinMismatch("WebCraft commit or pinned file hashes changed")
    readiness = "preview_ready" if allow_preview else "experiment_ready"
    if require_ready and not actual[readiness]:
        level = "preview" if allow_preview else "formal experiment"
        raise PinMismatch(f"WebCraft is not ready for {level}")
    return actual
