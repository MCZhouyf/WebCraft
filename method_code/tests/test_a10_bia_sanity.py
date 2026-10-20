from scripts.analyze_a10_bia_sanity_20261016 import recall_and_accuracy


def test_comparator_components_preserve_recall_and_unweighted_accuracy():
    rows = [
        {"gold_valid": 1, "always_invalid_prediction": 0, "agent_prediction": 1, "agent_answered": 1},
        {"gold_valid": 0, "always_invalid_prediction": 0, "agent_prediction": "uncertain", "agent_answered": 0},
    ]
    baseline = recall_and_accuracy(rows, "always_invalid_prediction", answered_only=False)
    assert baseline["valid_recall"] == 0.0
    assert baseline["invalid_recall"] == 1.0
    assert baseline["balanced_accuracy"] == 0.5
    assert baseline["unweighted_accuracy"] == 0.5

    agent = recall_and_accuracy(rows, "agent_prediction", answered_only=True)
    assert agent["scored_items"] == 1
    assert agent["uncertain_n"] == 1
    assert agent["valid_recall"] == 1.0
    assert agent["invalid_recall"] == ""
