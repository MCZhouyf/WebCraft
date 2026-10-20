"""Analyze existing fixture trajectories, never run an agent or an API."""
import json
import sys

from scripts import analyze_a18_local_cds as exporter


def test_remote_export_is_separate_and_reports_real_backend_kind(tmp_path, monkeypatch):
    root = exporter.ROOT
    fixture = root / 'artifacts/post_review/a18_qwen_cds_20261017/local_nf4/episodes.jsonl'
    record = json.loads(fixture.read_text().splitlines()[0])
    # Synthetic transport metadata only; the immutable local trajectory is read-only.
    record['local_model_identifiers'] = []
    record['server_model_identifiers'] = ['synthetic-test-model']
    local_out, local_report = tmp_path / 'artifacts/local_nf4', tmp_path / 'reports/local_nf4'
    remote_out, remote_report = local_out.parent / 'remote_service', local_report.parent / 'remote_service'
    remote_out.mkdir(parents=True)
    (remote_out / 'run_manifest.json').write_text(json.dumps({
        'required_server_model_identifier': 'synthetic-test-model'}))
    (remote_out / 'episodes.jsonl').write_text(json.dumps(record) + '\n')
    (remote_out / 'status.json').write_text('{"status":"SYNTHETIC_TEST_ONLY"}')
    monkeypatch.setattr(exporter, 'OUT', local_out)
    monkeypatch.setattr(exporter, 'REPORT', local_report)
    monkeypatch.setattr(sys, 'argv', ['exporter', '--backend', 'remote_service', '--allow-partial'])
    exporter.main()
    usage = json.loads((remote_report / 'usage.json').read_text())
    assert usage['local_generations'] == usage['remote_generations'] == usage['API_calls'] == 0
    assert usage['local_model_identifier'] is None
    assert usage['server_model_identifier'] == 'synthetic-test-model'
    assert usage['episodes_completed'] == 1
    assert not local_report.exists()
    assert 'Qwen_remote_service' in (remote_report / 'condition_metrics.csv').read_text()
    assert 'not established by a response alias' in (remote_report / 'REPORT.md').read_text()
