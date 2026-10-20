import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbiter.llm.gateway import CanaryFailed


@pytest.mark.parametrize("stage", ["preflight", "episode"])
def test_canary_failure_never_scores_episode_or_starts_next_job(monkeypatch, tmp_path, stage):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    runner = importlib.import_module("run_external_agents")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/webcraft_pin.json").write_text("{}")
    output = tmp_path / "run"
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "parse_args", lambda: SimpleNamespace(
        limit=2, offset=0, output_dir=output, webcraft_root=tmp_path, methods=["direct_llm"],
        index_dir=tmp_path, preflight_only=False, thinking="disabled", request_interval=10.0,
        planned_memory_mib=512,
        ledger=tmp_path / "ledger.jsonl",
    ))
    monkeypatch.setattr(runner, "verify_webcraft_pin", lambda *a, **k: None)
    monkeypatch.setattr(runner, "load_conflicts", lambda *a, **k: ())
    monkeypatch.setattr(runner, "load_resource_nodes", lambda *a, **k: {})
    monkeypatch.setattr(runner, "load_evaluation_tasks", lambda *a: [
        {"id": str(i), "conflict_ids": ["conflict"], "standard_items": [],
         "target": {"item": "paper", "count": 1}} for i in range(22)
    ])
    class Client:
        model = "glm-5.2"

        def __init__(self, *args):
            pass

        def validate_canary(self, **kwargs):
            if stage == "preflight":
                raise CanaryFailed("canary_validation_failed")

    monkeypatch.setattr(runner, "GatewayClient", Client)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **k: "test-commit")
    sessions = []
    shutdown = []

    def create(**kwargs):
        sessions.append(kwargs)
        return {"api": object(), "shutdown": lambda: shutdown.append(True)}

    monkeypatch.setattr(runner, "load_webcraft_module", lambda *a, **k: SimpleNamespace(create_simulated_agent=create))
    monkeypatch.setattr(runner, "PublicSemanticAdapter", lambda api: api)

    def fail(*args, **kwargs):
        raise CanaryFailed("canary_validation_failed")

    monkeypatch.setattr(runner, "run_external_episode", fail)
    assert runner.main() == 2
    assert not (output / "episodes.jsonl").exists()
    assert len(sessions) == (1 if stage == "episode" else 0)
    assert len(shutdown) == len(sessions)
    if stage == "episode":
        assert json.loads((output / "status.json").read_text())["failures"] == {}
