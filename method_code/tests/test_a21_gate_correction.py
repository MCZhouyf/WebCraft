"""Approved gate-placement correction; all checks are offline."""
import copy
import json

import pytest

from scripts import run_a21_mds as run


def test_real_development_low_SR_passes_technical_entry_without_mutating_gates():
    paths = [run.REPORT / name for name in run.DEVELOPMENT_GATES.values()]
    before = [p.read_bytes() for p in paths]
    assert all(json.loads(v)['feasible_success_rate'] == .25 for v in before)
    proof = run.development_executability()
    assert proof['passed'] and proof['n'] == 8
    assert proof['amendment_sha256'] == run.GATE_AMENDMENT_SHA256
    assert [p.read_bytes() for p in paths] == before


def test_missing_submit_or_malformed_request_cannot_pass_technical_entry(monkeypatch):
    records = run.completed_records(run.stage_output('development'), run.material_rows('development'))
    original = run.read_jsonl
    path = run.ROOT / records[0]['action_log']
    monkeypatch.setattr(run, 'read_jsonl', lambda p: [e for e in original(p) if e.get('method') != 'submit']
                        if p == path else original(p))
    assert not run.development_executability()['passed']
    path = run.ROOT / records[0]['prompt_log']

    def corrupt(p):
        rows = copy.deepcopy(original(p))
        if p == path:
            prompt = next(v for v in rows if v.get('record_type') == 'full_prompt'
                          and v.get('call_type') == 'external_react')
            next(m for m in prompt['messages'] if m['role'] == 'user')['content'] = '{invalid'
        return rows

    monkeypatch.setattr(run, 'read_jsonl', corrupt)
    with pytest.raises(json.JSONDecodeError):
        run.development_executability()


def test_formal_manifest_is_locked_before_request_or_configuration(monkeypatch):
    real = run.sha
    monkeypatch.setattr(run, 'sha', lambda p: 'changed' if p == run.material_manifest('formal') else real(p))
    with pytest.raises(RuntimeError, match='formal_frozen_manifest_mismatch'):
        run.material_rows('formal')


def test_formal_numeric_gate_uses_20_feasible_not_32_total():
    rows = [{'condition': 'K', 'true_feasible': True, 'success': int(i < 8)} for i in range(20)]
    rows += [{'condition': 'K', 'true_feasible': False, 'success': 1} for _ in range(12)]
    result = run.success_gate(rows, 32)
    assert result['passed'] and result['feasible_n'] == 20
    assert result['feasible_success_rate'] == .4
    rows[7]['success'] = 0
    assert not run.success_gate(rows, 32)['passed']


def test_prompt_privilege_checks_keys_not_allowed_source_strings():
    assert not run.forbidden_prompt_key({'source': 'truth_oracle_path'})
    assert run.forbidden_prompt_key({'observation': {'truth_vector': {}}})
