import json
import random
from pathlib import Path

import pytest

from corpus.build_indexes import build_index, chunks, wikitext_to_text
from corpus.search_backend import RETURN_KEYS, SearchBackend


def test_wikitext_rendering_preserves_recipe_claims():
    text = "== Crafting ==\n{{Crafting|A1=Iron Ingot|B1=Stick|Output=Iron Pickaxe}}"
    rendered = wikitext_to_text(text)
    assert "Crafting recipe for Iron Pickaxe" in rendered
    assert "Iron Ingot" in rendered


def test_chunk_size_is_stable():
    values = chunks(" ".join(f"w{i}" for i in range(700)))
    assert [len(value.split()) for value in values] == [300, 300, 180]


def test_search_contract(tmp_path: Path):
    source = {
        "text": "Iron ingot crafting recipe and smelting information.",
        "source_site": "test",
        "page_title": "Iron Ingot",
        "revid": 1,
        "timestamp": "2020-01-01T00:00:00Z",
    }
    build_index(tmp_path, [source], ["mixed"], {"provisional": True})
    result = SearchBackend("mixed", str(tmp_path)).search("iron", 3)
    assert len(result) == 1
    assert set(result[0]) == RETURN_KEYS


def test_formal_matched_condition_is_allowed():
    assert "matched_1.21.11" in __import__(
        "corpus.search_backend", fromlist=["ALLOWED_CONDITIONS"]
    ).ALLOWED_CONDITIONS


@pytest.mark.parametrize(
    "condition,directory",
    [
        ("matched_1.16.5", "matched_1.16.5"),
        ("matched_1.19", "matched_1.19"),
        ("mismatched_old2new", "mismatched_old2new"),
        ("mismatched_new2old", "mismatched_new2old"),
        ("mixed", "mixed"),
    ],
)
def test_provisional_indexes_random_leak_scan(condition: str, directory: str):
    root = Path("corpus/indexes/provisional") / directory
    if not root.exists():
        pytest.skip("provisional indexes not built")
    backend = SearchBackend(condition, str(root))
    vocabulary = sorted({token for row in backend.documents for token in row["text"].split()})
    rng = random.Random(20261006)
    for _ in range(200):
        result = backend.search(rng.choice(vocabulary), 5)
        for row in result:
            assert set(row) == RETURN_KEYS
            lowered = row["text"].casefold()
            assert "truth_vector" not in lowered
            assert "synthetic" not in lowered


@pytest.mark.parametrize(
    "condition,directory",
    [
        ("matched_1.16.5", "matched_1.16.5"),
        ("matched_1.19", "matched_1.19"),
        ("matched_1.21.11", "matched_1.21.11"),
        ("mismatched_old2new", "mismatched_old2new"),
        ("mismatched_new2old", "mismatched_new2old"),
        ("mixed", "mixed"),
    ],
)
def test_formal_indexes_random_leak_scan(condition: str, directory: str):
    root = Path("corpus/indexes/formal") / directory
    if not root.exists():
        pytest.skip("formal indexes not built")
    backend = SearchBackend(condition, str(root))
    vocabulary = sorted({token for row in backend.documents for token in row["text"].split()})
    rng = random.Random(20261006)
    for _ in range(200):
        result = backend.search(rng.choice(vocabulary), 5)
        for row in result:
            assert set(row) == RETURN_KEYS
            lowered = row["text"].casefold()
            assert "truth_vector" not in lowered
            assert "synthetic" not in lowered


@pytest.mark.parametrize(
    "condition",
    [
        "matched_1.16.5",
        "matched_1.19",
        "matched_1.21.11",
        "mismatched_old2new",
        "mismatched_new2old",
        "mixed",
        "polluted",
    ],
)
def test_formal_p1_indexes_random_leak_scan(condition: str):
    root = Path("corpus/indexes/formal_p1") / condition
    if not root.exists():
        pytest.skip("formal P1 indexes not built")
    backend = SearchBackend(condition, str(root))
    vocabulary = sorted({token for row in backend.documents for token in row["text"].split()})
    rng = random.Random(20261007)
    for _ in range(200):
        result = backend.search(rng.choice(vocabulary), 5)
        for row in result:
            assert set(row) == RETURN_KEYS
            lowered = row["text"].casefold()
            assert "truth_vector" not in lowered
            assert "synthetic" not in lowered
