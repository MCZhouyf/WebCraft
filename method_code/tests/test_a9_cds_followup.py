from scripts.analyze_a9_cds_followup_20261016 import (
    binary_summary, bootstrap_differences, episode_balanced_accuracy, short_event,
)


def test_boundary_summary_separates_abstention_from_answered_item_balance():
    items = [
        {"gold_valid": 1, "agent_prediction": 1, "agent_answered": 1},
        {"gold_valid": 0, "agent_prediction": 0, "agent_answered": 1},
        {"gold_valid": 1, "agent_prediction": "uncertain", "agent_answered": 0},
    ]
    summary = binary_summary(items, "agent_prediction")
    assert summary["BIA"] == 1.0
    assert summary["balanced_accuracy"] == 1.0
    assert summary["uncertain_rate"] == 1 / 3
    assert episode_balanced_accuracy(items, "agent_prediction") == 1.0


def test_cluster_bootstrap_uses_family_mean_and_event_rendering_is_bounded():
    point, low, high, n = bootstrap_differences([("a", 0.0), ("a", 1.0), ("b", 1.0)])
    assert point == 0.75
    assert low <= point <= high
    assert n == 3
    rendered = short_event({"method": "search", "params": {"q": "x"}, "success": True,
                            "result": [{"mechanism fact": "one", "source": "a"},
                                       {"mechanism fact": "two", "source": "b"},
                                       {"mechanism fact": "three", "source": "c"}]})
    assert "one" in rendered and "two" in rendered and "three" not in rendered
