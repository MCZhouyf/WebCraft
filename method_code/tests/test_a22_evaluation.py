"""Orchestration-only tests: no held-out execution or method changes."""
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('a22_eval', ROOT / 'scripts/run_a22_evaluation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_frozen_policy_hashes_still_match():
    assert module.verify_policy()['B'] == 1


def test_scope_and_reading_mode_separate_job_paths():
    c = {'scope': 'main', 'mode': 'all_public', 'condition': 'synthetic',
         'world': 'sandbox', 'task': {'id': 'test'}}
    keys = {module.key({**c, 'scope': s, 'mode': m})
            for s in ['main', 'ext_v2'] for m in ['all_public', 'llm_only', 'oracle_reading']}
    assert len(keys) == 6


def test_orchestration_projects_reading_mode_without_changing_frozen_policy(monkeypatch, tmp_path):
    seen = []
    def fake_execute(payload):
        seen.append(payload)
        return {'reading_mode': 'all_public', 'result': {'policy_trace': []}, 'metrics': {'SR': 0}}
    monkeypatch.setattr(module, 'execute', fake_execute)
    c = {'scope': 'main', 'mode': 'oracle_reading', 'family_id': 'test',
         'evidence': {'reader': {'search_count': 0}}, 'evidence_path': 'synthetic.json'}
    row = module.formal_job((c, 20261020, tmp_path))
    assert row['reading_mode'] == 'oracle_reading'
    assert row['runtime_search_calls'] == 0
    assert seen[0][1:4] == ('pf_rag', 1, 20261020)
    assert json.loads((tmp_path / 'episode.json').read_text()) == row
