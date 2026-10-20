from pathlib import Path

from corpus.cache_extractor_counts import count_caches, effective_extractor


ROOT = Path(__file__).resolve().parents[2]


def test_effective_extractor_prefers_explicit_public_marker():
    assert effective_extractor({
        "extractor_version": "llm00001",
        "payload": {"extractor": "deterministic_prose"},
    }, llm_version="llm00001") == "deterministic_prose"


def test_effective_extractor_labels_llm_and_unknown_versions():
    assert effective_extractor({
        "extractor_version": "llm00001", "payload": {},
    }, llm_version="llm00001") == "llm"
    assert effective_extractor({
        "extractor_version": "other001", "payload": {},
    }, llm_version="llm00001") == "unclassified"


def test_frozen_cache_routes_are_complete_and_counted():
    rows = count_caches(ROOT)
    assert {row["extractor"] for row in rows} == {
        "template", "deterministic_prose", "llm",
    }
    totals = {
        (row["cache_generation"], row["extractor"]): row["claim_count"]
        for row in rows if row["index_name"] == "__all__"
    }
    assert totals == {
        ("formal", "template"): 12042,
        ("formal", "deterministic_prose"): 557,
        ("formal", "llm"): 7927,
        ("formal_p1", "template"): 18588,
        ("formal_p1", "deterministic_prose"): 869,
        ("formal_p1", "llm"): 12305,
    }
