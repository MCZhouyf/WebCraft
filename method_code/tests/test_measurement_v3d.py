from scripts.run_measurement_v3d_20261015 import episode_v3d, safe_ratio_difference


def test_episode_v3d_normalizes_csv_seed_to_record_seed() -> None:
    records = {
        ("first_run", "agent", "matched_1.19", "1.19", "task", 20261006): {},
    }
    actions = [{
        "setting": "first_run", "method": "agent", "condition": "matched_1.19",
        "world": "1.19", "task_id": "task", "seed": "20261006",
        "world_denied_discriminative": 1, "v3d_propagation": 1,
        "v3_label": "commit",
    }]
    assert episode_v3d(actions, records) == [{
        "setting": "first_run", "method": "agent", "condition": "matched_1.19",
        "world": "1.19", "task_id": "task", "seed": 20261006,
        "v3d_episode_lower": 1, "v3d_episode_upper": 1,
    }]


def test_v3d_ratio_difference_is_explicit_when_conditional_denominator_absent() -> None:
    assert safe_ratio_difference({"task": (0, 0)}, {"task": (0, 1)}, 1) == ("", "", 10_000)
