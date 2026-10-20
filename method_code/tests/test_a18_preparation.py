"""Zero-call A18 scope, parity, and immutable-preparation checks."""
import csv
import json

import pytest

from scripts import prepare_a18_qwen_cds as preparation


def test_frozen_scope_matches_four_a8_conditions():
    lock = json.loads(preparation.LOCK.read_text())
    keys = [(r['condition'], r['instance_id']) for r in lock['jobs']]
    assert lock['episodes'] == len(keys) == len(set(keys)) == 208
    for condition in preparation.CONDITIONS:
        assert sum(c == condition for c, _ in keys) == 52
    assert {r['method'] for r in lock['jobs']} == {'react'}


def test_input_lock_matches_read_only_materials():
    registered = json.loads(preparation.LOCK.read_text())
    rebuilt = preparation.build_lock()
    for field in ('jobs', 'manifest_sha256', 'w7a_freeze_sha256',
                  'source_hashes', 'react_prompt_sha256'):
        assert registered[field] == rebuilt[field]


def test_reference_is_glm_only_not_pooled_or_synthetic():
    rows = list(csv.DictReader((preparation.OUT / 'glm_reference_episodes.csv').open()))
    assert len(rows) == 208
    assert {r['server_model'] for r in rows} == {'glm-5.2'}
    assert sum(int(r['action_model_calls']) for r in rows) == 2136
    assert sum(int(r['boundary_model_calls']) for r in rows) == 208
    status = json.loads((preparation.OUT / 'status.json').read_text())
    assert status['model_calls'] == status['episodes_run'] == 0
    assert status['server_model_identifier'] is None


def test_refuses_to_overwrite_existing_input_lock(tmp_path, monkeypatch):
    path = tmp_path / 'already_registered.json'
    path.write_text('{}\n')
    monkeypatch.setattr(preparation, 'LOCK', path)
    with pytest.raises(RuntimeError, match='do_not_overwrite'):
        preparation.main()
    assert path.read_text() == '{}\n'
