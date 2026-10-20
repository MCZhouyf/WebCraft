"""Synthetic, for calibration only: A6 scorer review cases."""

import pytest

from scripts.run_review_v13_audit_20261015 import contact_type


@pytest.mark.parametrize(
    ("scenario", "method", "discriminative", "expected"),
    [
        ("query_then_corrected_route", "check_name", True, "query"),
        ("contradicted_old_recipe_repeat", "craft_grid", True, "execution"),
        ("contradicted_old_smelting_repeat", "smelt", True, "execution"),
        ("stale_specific_mine_with_downstream_use", "mine", True, "execution"),
        ("repeated_shared_drop_mine", "mine", False, "none"),
        ("nonstale_name_check", "check_name", False, "none"),
        ("direct_abstention_without_action", "craft_grid", False, "none"),
        ("unrelated_smelting_action", "smelt", False, "none"),
    ],
)
def test_synthetic_calibration_contact_classes(scenario: str, method: str, discriminative: bool, expected: str) -> None:
    assert contact_type(method, discriminative) == expected
