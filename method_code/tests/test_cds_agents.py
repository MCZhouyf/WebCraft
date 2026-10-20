import json
from pathlib import Path

from arbiter.evaluation.cds_agents import (
    boundary_metrics, boundary_truth, lexical_search, parse_boundary, public_rows,
)
from scripts.analyze_cds_agents_20261017 import bootstrap_mean, condition_intervals


def test_public_rows_and_search_use_only_visible_contract():
    material = {"conditions": {"K": [
        {"mechanism fact": "Blue wool uses dye.", "source": "wiki", "timestamp": "2021-01-01"},
        {"mechanism fact": "Iron smelts in a furnace.", "source": "wiki", "timestamp": "2020-01-01"},
    ]}}
    rows = public_rows(material, "K")
    assert lexical_search(rows, "blue wool", 5) == [rows[0]]
    assert lexical_search(rows, "unrelated", 5) == []


def test_boundary_parser_treats_invalid_or_missing_items_as_uncertain():
    value = json.dumps({"judgments": [
        {"index": 1, "label": "在当前世界有效"},
        {"index": 2, "label": "无效"},
        {"index": 2, "label": "在当前世界有效"},
    ]})
    assert parse_boundary(value, 3) == [True, None, None]
    assert parse_boundary("not json", 2) == [None, None]


def test_boundary_truth_and_preregistered_baselines():
    candidates = [
        {"mechanism fact": "old", "source": "a", "timestamp": "2020"},
        {"mechanism fact": "new", "source": "b", "timestamp": "2021"},
    ]
    import hashlib
    evaluator = {"boundary_truth": [
        {"candidate_text_sha256": hashlib.sha256(b"old").hexdigest(), "truth": True},
        {"candidate_text_sha256": hashlib.sha256(b"new").hexdigest(), "truth": False},
    ]}
    truth = boundary_truth({"boundary_question": candidates}, evaluator)
    metrics = boundary_metrics([True, None], truth, candidates)
    assert truth == [True, False]
    assert metrics["BIA"] == 1.0
    assert metrics["boundary_uncertain_rate"] == 0.5
    assert metrics["newest_timestamp_BIA"] == 0.0
    assert metrics["majority_class_BIA"] == 0.5


def test_condition_intervals_cluster_by_family_and_cover_registered_metrics():
    rows = []
    for index, success in enumerate((0, 1, 1, 1)):
        rows.append({
            "method": "react", "condition": "K", "family_id": f"family-{index // 2}",
            "success": success, "true_feasible": 1, "overconservative": 1 - success,
            "terminal_execution_failure": 0, "first_conflict_target_agreement": success,
            "HTR_all": 0, "discriminative_stale_contact": 0, "query_contact": 0,
            "action_contact": 0, "v3d_lower": 0, "v3d_upper": 0,
            "BIA_episode": 0.5, "semantic_actions": 2,
            "action_model_calls": 3, "boundary_model_calls": 1,
        })
    point, low, high, n = bootstrap_mean(rows, "success")
    assert (point, n) == (0.75, 4)
    assert low <= point <= high
    intervals = condition_intervals(rows)
    assert {row["metric"] for row in intervals} >= {"SR", "BIA_episode", "total_model_calls"}
    assert all(row["bootstrap_unit"] == "independent_family" for row in intervals)
