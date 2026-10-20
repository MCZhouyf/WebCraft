import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/main/post_review/w16_20261019"


def test_w16_required_outputs_and_registered_seed():
    for name in ["factorial_s.csv", "audit_sensitivity.csv", "component_bootstrap.csv", "cost_table.csv"]:
        assert (OUT / name).is_file()
    factorial = list(csv.DictReader((OUT / "factorial_s.csv").open()))
    assert any(row["condition"] == "S" and row["metric"] == "SR" for row in factorial)
    assert all(row["seed"] == "20261019" for row in factorial)


def test_w16_component_analysis_has_two_registered_contrasts():
    rows = list(csv.DictReader((OUT / "component_bootstrap.csv").open()))
    assert len(rows) == 2
    assert {row["n_components"] for row in rows} == {"5"}
