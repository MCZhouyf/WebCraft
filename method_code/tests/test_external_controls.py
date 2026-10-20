from pathlib import Path

from arbiter.evaluation.external_controls import (
    PUBLIC_SEARCH_FIELDS,
    OracleSearchAdapter,
    lexical_search,
    matrix_jobs,
    public_task,
)


def _tasks():
    rows = []
    for index in range(22):
        rows.append({"id": f"x{index}", "type": "planning", "conflict_ids": ["c"]})
    for index in range(12):
        rows.append({"id": f"c{index}", "type": "control", "conflict_ids": []})
    return rows


def test_matrix_cardinalities():
    assert len(matrix_jobs(_tasks(), "oracle_knowledge", ("direct_llm", "react"))) == 220
    assert len(matrix_jobs(_tasks(), "control_all_public", ("direct_llm", "react"))) == 120
    assert len(matrix_jobs(_tasks(), "conditions", ("direct_llm", "react"))) == 264


def test_public_task_removes_evaluator_fields():
    raw = {
        "target": {"item": "stick", "count": 2},
        "standard_items": [{"name": "oak_log", "count": 1}],
        "task_supplements": [], "feasible": {"1.16.5": True}, "oracle_plans": {},
    }
    assert set(public_task(raw)) == {"target", "standard_items", "task_supplements"}


def test_lexical_search_is_public_and_stable():
    rows = [
        {"text": "Crafting recipe for stick: oak planks.", "source_site": "wiki",
         "page_title": "Stick", "revid": 1, "timestamp": "t", "chunk_id": "b"},
        {"text": "Breaking coal ore drops coal.", "source_site": "wiki",
         "page_title": "Coal Ore", "revid": 2, "timestamp": "t", "chunk_id": "a"},
    ]
    result = lexical_search(rows, "stick recipe", 1)
    assert result[0]["chunk_id"] == "b"
    assert set(result[0]) == PUBLIC_SEARCH_FIELDS


class _Base:
    def __init__(self): self.calls = []
    def search(self, query, k, *, intent=None): self.calls.append((query, k, intent)); return []


def test_oracle_adapter_replaces_search_result():
    row = {"text": "gold", "source_site": "wiki", "page_title": "X", "revid": 1,
           "timestamp": "t", "chunk_id": "x"}
    base = _Base()
    adapter = OracleSearchAdapter(base, [row])
    assert adapter.search("x", 1, intent="commit") == [row]
    assert base.calls == [("x", 1, "commit")]
    assert adapter.observations[0]["result"] == [row]
