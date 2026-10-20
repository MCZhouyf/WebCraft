import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_external_baselines import aggregate


def test_external_summary_uses_episode_denominators():
    rows = [
        {
            "source": "external_llm", "method": "direct_llm", "SR": value,
            "behavioral_HTR_commit": 0, "HTR_all": value,
            "self_reported_HTR_commit": value, "false_abstention": 1 - value,
            "execution_failure": 0, "probe_count": value, "probe_cost": value * 2,
            "model_calls": 2 + value, "input_tokens": 10, "output_tokens": 1,
        }
        for value in (0, 1)
    ]
    result = aggregate(rows)
    assert len(result) == 1
    assert result[0]["episodes"] == 2
    assert result[0]["SR"] == 0.5
    assert result[0]["HTR_all"] == 0.5
    assert result[0]["mean_probe_cost"] == 1.0
    assert result[0]["mean_model_calls"] == 2.5
