import json
from pathlib import Path
import subprocess

import pytest

from arbiter.adapters import (
    EmptySearchBackend,
    PublicSemanticAdapter,
    load_search_backend,
)
from arbiter.adapters.claims import ValidatedClaimsCache, validate_claim_rows
from arbiter.pinning import (
    SIM_BACKEND_RE,
    STAGE1_READY_RE,
    COMBINED_BACKEND_RE,
    CLAIMS_CACHE_V3_READY_RE,
    CLAIMS_CACHE_V4_READY_RE,
    PinMismatch,
    build_pin,
    verify_webcraft_pin,
)


class PublicAPI:
    def __init__(self): self.mined = []
    def mine(self, value, *, intent=None): self.mined.append((value, intent)); return {"success": True}
    def craft_grid(self, value, *, intent=None): return {"success": True}
    def smelt(self, item, fuel, n, *, intent=None): return {"success": True}
    def check_name(self, value, *, intent=None): return {"resolvable": True}
    def inventory(self, *, intent=None): return []
    def report_version(self, value, *, intent=None): return {"confirmed": True}
    def submit(self, result, *, intent=None): return {"confirmed": True}
    def search(self, query, k, *, intent=None): return []


def test_public_adapter_accepts_exact_surface():
    public = PublicAPI()
    adapter = PublicSemanticAdapter(public)
    assert adapter.inventory() == []
    assert adapter.search("query", 5) == []
    adapter.mine("oak_log", intent="probe")
    adapter.mine("oak_log")
    adapter.mine("iron_ore")
    adapter.mine("cobblestone")
    assert public.mined == [("log_1", "probe"), ("log_2", None),
                            ("iron_1", None), ("cobble_1", None)]


def test_empty_search_stub():
    assert EmptySearchBackend().search("query", 5) == []


def test_claim_cache_contract_rejects_extra_fields():
    row = {
        "claim_id": "claim_1", "chunk_id": "chunk_1", "claim_type": "drop",
        "subject_item": "iron_ore", "payload": {"drops": ["raw_iron"]},
        "polarity": "positive", "tense": "current", "span_text": "drops raw iron",
        "extractor_version": "sha256:test",
        "normalizer_version": None, "parent_claim_id": None,
    }
    assert validate_claim_rows([row], "chunk_1") == [row]
    invalid = dict(row)
    invalid["condition"] = "mixed"
    with pytest.raises(ValueError):
        validate_claim_rows([invalid], "chunk_1")
    legacy = {key: value for key, value in row.items()
              if key not in {"normalizer_version", "parent_claim_id"}}
    with pytest.raises(ValueError, match="eleven-field"):
        validate_claim_rows([legacy], "chunk_1")
    normalized = dict(row, normalizer_version="normalizer-test", parent_claim_id="parent-1")
    assert validate_claim_rows([normalized], "chunk_1") == [normalized]
    for field in ("normalizer_version", "parent_claim_id"):
        with pytest.raises(ValueError):
            validate_claim_rows([dict(row, **{field: {"value": "bad"}})], "chunk_1")
    for forbidden in ("truth_vector", "synthetic", "provisional", "condition",
                      "index_dir", "file_path", "path"):
        invalid = dict(row, payload={"nested": [{forbidden: "private"}]})
        with pytest.raises(ValueError, match="forbidden"):
            validate_claim_rows([invalid], "chunk_1")


def test_structured_reading_modes_filter_the_shared_cache():
    class Cache:
        def get(self, chunk_id):
            base = {
                "chunk_id": chunk_id, "claim_type": "recipe",
                "subject_item": "item", "polarity": "positive",
                "tense": "current", "span_text": "source",
                "normalizer_version": None, "parent_claim_id": None,
            }
            return [
                dict(base, claim_id="template", payload={"extractor": "template"},
                     extractor_version="parser"),
                dict(base, claim_id="llm", payload={}, extractor_version="96b07e80"),
                dict(base, claim_id="prose", payload={"extractor": "deterministic_prose"},
                     extractor_version="parser"),
            ]

    all_public = ValidatedClaimsCache(
        Cache(), reading_mode="all_public",
        llm_extractor_version="96b07e80",
    )
    llm = ValidatedClaimsCache(
        Cache(), reading_mode="llm_only", llm_extractor_version="96b07e80",
    )
    assert [row["claim_id"] for row in all_public.get("chunk_1")] == [
        "template", "llm", "prose",
    ]
    assert [row["claim_id"] for row in llm.get("chunk_1")] == ["llm"]
    with pytest.raises(ValueError, match="reading mode"):
        ValidatedClaimsCache(Cache(), reading_mode="all", llm_extractor_version="96b07e80")


def test_readiness_markers_do_not_match_negative_prose():
    report = "WebCraft does not yet claim STAGE1_READY_FOR_PILOT with backend=sim."
    assert STAGE1_READY_RE.search(report) is None
    assert SIM_BACKEND_RE.search(report) is None
    assert STAGE1_READY_RE.search("**STAGE1_READY_FOR_PILOT**\n")
    assert SIM_BACKEND_RE.search("- **backend=sim**\n")
    assert COMBINED_BACKEND_RE.search("**STAGE1_READY_FOR_PILOT (backend=sim)**\n")
    assert CLAIMS_CACHE_V3_READY_RE.search("## CLAIMS_CACHE_V3_READY\n")
    assert CLAIMS_CACHE_V3_READY_RE.search("No CLAIMS_CACHE_V3_READY is declared.") is None
    assert CLAIMS_CACHE_V4_READY_RE.search("## CLAIMS_CACHE_V4_READY\n")
    assert CLAIMS_CACHE_V4_READY_RE.search("Awaiting CLAIMS_CACHE_V4_READY.") is None


def test_pin_round_trip_detects_drift_and_allows_empty_stub(tmp_path):
    root = tmp_path / "webcraft"
    (root / "config").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "config/example.json").write_text("{}\n")
    (root / "reports/STAGE1_REPORT.md").write_text("Not ready.\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-qm", "fixture"], cwd=root, check=True,
    )
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps(build_pin(root), sort_keys=True))
    state = verify_webcraft_pin(pin, root, require_ready=False)
    assert state["experiment_ready"] is False
    assert state["amendment_v6_ready"] is False
    backend = load_search_backend(
        "matched", "unused", root=root, pin_path=pin,
        allow_empty_stub=True,
    )
    assert isinstance(backend, EmptySearchBackend)
    (root / "config/example.json").write_text('{"changed": true}\n')
    with pytest.raises(PinMismatch):
        verify_webcraft_pin(pin, root, require_ready=False)
