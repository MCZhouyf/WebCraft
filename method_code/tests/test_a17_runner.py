"""Zero-call checks for the A17 execution envelope."""
import hashlib
import json
import sqlite3

from scripts import run_a17_ext_agents as runner


def test_frozen_scope_and_prompts():
    manifest = json.loads((runner.OUT / 'run_manifest.json').read_text())
    assert len(manifest['jobs']) == 180
    assert len({j['job_id'] for j in manifest['jobs']}) == 180
    assert {m: sum(j['method'] == m for j in manifest['jobs']) for m in runner.METHODS} == {
        'direct_llm': 60, 'react': 60, 'reflexion': 60}
    for method in runner.METHODS:
        assert hashlib.sha256(runner.public_system_prompt(method).encode()).hexdigest() == manifest['prompt_sha256'][method]
    assert manifest['max_actions'] == 90
    assert manifest['max_calls'] == 40


def test_queue_claims_are_unique_and_skip_blocked(tmp_path, monkeypatch):
    path = tmp_path / 'queue.sqlite'
    monkeypatch.setattr(runner, 'DATABASE', path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs(job_id TEXT, method TEXT,rank INTEGER,status TEXT,slot INTEGER,reason TEXT)')
        db.execute('CREATE TABLE blocked_methods(method TEXT PRIMARY KEY)')
        db.execute("INSERT INTO jobs VALUES ('a','react',0,'pending',NULL,'')")
        db.execute("INSERT INTO jobs VALUES ('b','direct_llm',1,'pending',NULL,'')")
        db.execute("INSERT INTO blocked_methods VALUES ('react')")
    assert runner.claimed_job(1)['job_id'] == 'b'
    assert runner.claimed_job(2) is None


def test_private_configuration_gate(monkeypatch):
    import pytest
    with pytest.raises(ValueError):
        runner.configure({'enabled': True, 'allow_api_calls': True, 'model': 'other'}, 1)


def test_closed_run_cannot_overwrite_logs_or_open_credentials(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(runner, 'OUT', tmp_path)
    (tmp_path / 'status.json').write_text('{"status":"completed"}')
    with pytest.raises(RuntimeError, match='registered_run_already_closed_no_rerun'):
        runner.run()


def test_closed_run_cannot_reinitialize_manifest_without_database(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(runner, 'OUT', tmp_path)
    monkeypatch.setattr(runner, 'DATABASE', tmp_path / 'queue.sqlite')
    (tmp_path / 'status.json').write_text('{"status":"completed"}')
    with pytest.raises(RuntimeError, match='registered_run_already_closed_no_reinitialization'):
        runner.prepare()
    assert not (tmp_path / 'queue.sqlite').exists()
