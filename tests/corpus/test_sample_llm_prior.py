import json
from pathlib import Path

from corpus.sample_llm_prior import prompt_payload, selected_conflicts, summarize


def test_prompt_payload_does_not_include_truth_or_versions():
    conflicts = [{"id": "c1", "assertion": {"kind": "name", "item": "chain"}}]
    text = json.dumps(prompt_payload(conflicts))
    assert "truth" not in text
    assert "1.16" not in text and "1.19" not in text and "1.21" not in text


def test_summary_computes_version_agreement():
    conflicts = [{"id": "c1", "truth_vector": {"old": False, "new": True}}]
    samples = [
        {"conflict_id": "c1", "answer": "true"},
        {"conflict_id": "c1", "answer": "false"},
    ]
    result = summarize(conflicts, samples)
    assert result["agreement_by_version"] == {"new": 0.5, "old": 0.5}
    assert result["preferred_versions"] == ["new", "old"]
