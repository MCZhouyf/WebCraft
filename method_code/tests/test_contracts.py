import json

from arbiter.contracts import METHOD_COSTS, primary_cost
from arbiter.normalization import assertion_to_probe, mechanism_id, probe_to_assertion


def test_cost_model_matches_webcraft(webcraft_root):
    model = json.loads((webcraft_root / "config/cost_model.json").read_text())
    assert model["charges"] == METHOD_COSTS
    assert primary_cost("smelt", {"n": 4}) == 4
    assert primary_cost("smelt", {"n": 0}) == model["invalid_smelt_n_charge"]


def test_first_twenty_conflicts_round_trip(webcraft_root):
    lines = (webcraft_root / "data/conflicts.jsonl").read_text().splitlines()[:20]
    assert len(lines) == 20
    for line in lines:
        record = json.loads(line)
        assert mechanism_id(record["assertion"]) == record["id"]
        spec = assertion_to_probe(record["assertion"], record["probe"])
        rebuilt = probe_to_assertion(spec)
        assert mechanism_id(rebuilt) == record["id"]
        assert rebuilt == record["assertion"]

