"""Runner guards without network requests, credentials or model execution."""
import json
import sys

from scripts import run_a18_remote_cds as runner


def test_existing_output_is_never_overwritten(tmp_path, monkeypatch, capsys):
    out = tmp_path / 'existing'
    out.mkdir()
    status = out / 'status.json'
    status.write_text('{"preserve": true}\n')
    monkeypatch.setattr(runner, 'OUT', out)
    monkeypatch.setattr(runner, 'REPORT', tmp_path / 'report')
    monkeypatch.setattr(runner, 'load_private_config', lambda _: {'model': 'test-only'})
    monkeypatch.setattr(runner, 'effective_available_bytes', lambda: 4 * 1024**3)
    monkeypatch.setattr(sys, 'argv', ['runner', '--private-config', 'unused'])
    assert runner.main() == 1
    assert status.read_text() == '{"preserve": true}\n'
    final = json.loads(capsys.readouterr().out)
    assert final['safe_code'] == 'remote_output_must_be_new_no_silent_episode_replay'
    assert final['calls'] == 0


def test_missing_config_creates_no_running_artifact(tmp_path, monkeypatch, capsys):
    out = tmp_path / 'new'
    monkeypatch.setattr(runner, 'OUT', out)
    monkeypatch.setattr(sys, 'argv', ['runner', '--private-config', str(tmp_path / 'absent')])
    assert runner.main() == 1
    assert not out.exists()
    final = json.loads(capsys.readouterr().out)
    assert final['status'] == 'BLOCKED_CONFIGURATION_OR_PREFLIGHT'
    assert final['calls'] == final['completed'] == 0
    assert str(tmp_path) not in json.dumps(final)


def test_registered_inputs_verify_without_gpu_or_network():
    jobs = runner.verify_inputs()
    assert len(jobs) == 208
    assert len({(row['condition'], row['instance_id']) for row in jobs}) == 208
