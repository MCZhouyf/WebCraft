import json
from pathlib import Path

from arbiter.evaluation.prior_bundle import project_prior_bundle


def test_prior_projection_discards_truth_and_covers_operator_universe(tmp_path: Path):
    assertion = {
        "kind": "drop", "block": "stone", "drops": ["cobblestone"], "count": 1,
    }
    assertion_rows = [
        {
            "conflict_id": "cf_a", "assertion": assertion,
            "truth_vector": {"old": True}, "prompt_version": "old",
        },
    ]
    prior_rows = [
        {"conflict_id": "cf_a", "sample_index": 1, "answer": "true"},
        {"conflict_id": "cf_a", "sample_index": 2, "answer": "false"},
    ]
    assertion_path = tmp_path / "assertions.jsonl"
    prior_path = tmp_path / "prior.jsonl"
    assertion_path.write_text("\n".join(map(json.dumps, assertion_rows)) + "\n")
    prior_path.write_text("\n".join(map(json.dumps, prior_rows)) + "\n")

    bundle = project_prior_bundle(prior_path, assertion_path)

    assert set(bundle) == {"schema_version", "assertions", "samples"}
    assert bundle["assertions"] == [assertion]
    assert [list(sample.values()) for sample in bundle["samples"]] == [[True], [False]]
    assert "truth_vector" not in json.dumps(bundle)
