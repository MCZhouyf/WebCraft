"""Offline workflow tests; no models or simulation are invoked."""
import json

from scripts import continue_a21_mds as controller


def test_failed_gate_does_not_advance_or_change_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.run, 'REPORT', tmp_path)
    events = []
    monkeypatch.setattr(controller, 'publish', lambda status, **kw: events.append((status, kw)))
    data = {'passed': False, 'feasible_success_rate': 0.25, 'lower': 0.4, 'upper': 0.8}
    (tmp_path / 'gate.json').write_text(json.dumps(data))
    assert not controller.gate('gate.json')
    assert events[0][0] == 'NEEDS_REGISTERED_SHORTENING'
    assert json.loads((tmp_path / 'gate.json').read_text()) == data


def test_stages_preserve_wait_mode_and_all_budgets(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, 'step', lambda name, args: calls.append((name, args)) or True)
    assert controller.stage('formal', 'controls')
    args = calls[1][1]
    assert '--wait-on-429' in args and '--resume' in args
    assert args[args.index('--workers')+1] == '6'
    assert args[args.index('--formal-phase')+1] == 'controls'
    assert '--max-calls' not in args and '--max-actions' not in args


def test_failed_technical_check_does_not_read_formal_material(tmp_path, monkeypatch):
    stages, reads = [], []
    monkeypatch.setattr(controller.run, 'REPORT', tmp_path)
    monkeypatch.setattr(controller.run, 'material_rows', lambda name: reads.append(name) or [])
    monkeypatch.setattr(controller.run, 'completed_records', lambda *a: [])
    monkeypatch.setattr(controller, 'stage', lambda name: stages.append(name) or True)
    monkeypatch.setattr(controller.run, 'development_executability', lambda: {'passed': False})
    monkeypatch.setattr(controller, 'publish', lambda *a, **kw: None)
    controller.workflow()
    assert stages == ['development']
    assert reads == ['development']


def test_existing_partial_controls_skip_K_rerun(tmp_path, monkeypatch):
    stages, steps = [], []
    monkeypatch.setattr(controller.run, 'ROOT', tmp_path)
    monkeypatch.setattr(controller.run, 'REPORT', tmp_path)
    monkeypatch.setattr(controller.run, 'material_rows', lambda name: [])
    monkeypatch.setattr(controller.run, 'completed_records',
                        lambda path, jobs: [{}]*8 if path.name == 'development_run'
                        else [{'condition': 'K'}]*32 + [{'condition': 'K+S'}])
    monkeypatch.setattr(controller, 'gate', lambda name: True)
    monkeypatch.setattr(controller.run, 'development_executability', lambda: {'passed': True})
    monkeypatch.setattr(controller, 'stage', lambda name, phase: stages.append((name, phase)) or True)
    monkeypatch.setattr(controller, 'step', lambda name, args: steps.append(name) or True)
    monkeypatch.setattr(controller, 'publish', lambda *a, **kw: None)
    controller.workflow('all')
    assert stages == [('formal', 'controls')]
    assert steps == ['formal_K_export', 'offline_analysis']


def test_formal_K_scope_stops_before_controls_even_when_gate_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.run, 'ROOT', tmp_path)
    monkeypatch.setattr(controller.run, 'REPORT', tmp_path)
    monkeypatch.setattr(controller.run, 'material_rows', lambda name: [])
    monkeypatch.setattr(controller.run, 'completed_records',
                        lambda path, jobs: [{}]*8 if path.name == 'development_run' else [])
    monkeypatch.setattr(controller.run, 'development_executability', lambda: {'passed': True})
    stages, steps, statuses = [], [], []
    monkeypatch.setattr(controller, 'stage', lambda name, phase='K': stages.append((name, phase)) or True)
    monkeypatch.setattr(controller, 'step', lambda name, args: steps.append(name) or True)
    monkeypatch.setattr(controller, 'publish', lambda status, **kw: statuses.append((status, kw)))
    (tmp_path / 'formal_K_gate.json').write_text(json.dumps({'passed': True}))
    controller.workflow('formal_K')
    assert stages == [('formal', 'K')]
    assert steps == ['formal_K_export']
    assert statuses[-1][0] == 'FORMAL_K_COMPLETE'
    assert not statuses[-1][1]['formal_controls_authorized']


def test_all_scope_failed_formal_K_blocks_controls_but_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.run, 'REPORT', tmp_path)
    monkeypatch.setattr(controller.run, 'material_rows', lambda name: [])
    monkeypatch.setattr(controller.run, 'completed_records',
                        lambda path, jobs: [{}]*8 if path.name == 'development_run'
                        else [{'condition': 'K'}]*32)
    monkeypatch.setattr(controller.run, 'development_executability', lambda: {'passed': True})
    stages, steps, statuses = [], [], []
    monkeypatch.setattr(controller, 'stage', lambda *args: stages.append(args) or True)
    monkeypatch.setattr(controller, 'step', lambda name, args: steps.append(name) or True)
    monkeypatch.setattr(controller, 'publish', lambda status, **kw: statuses.append(status))
    (tmp_path / 'formal_K_gate.json').write_text(json.dumps(
        {'passed': False, 'feasible_success_rate': .25, 'lower': .4, 'upper': .8}))
    controller.workflow('all')
    assert not stages
    assert steps == ['formal_K_export']
    assert statuses == ['NEEDS_REGISTERED_SHORTENING']


def test_control_continuation_preserves_frozen_K_export(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.run, 'ROOT', tmp_path)
    monkeypatch.setattr(controller.run, 'REPORT', tmp_path / 'reports')
    marker = controller.run.REPORT / 'formal_K/provenance.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{"scope":"frozen K checkpoint"}\n')
    before = marker.read_bytes()
    monkeypatch.setattr(controller.run, 'material_rows', lambda name: [])
    monkeypatch.setattr(controller.run, 'completed_records',
                        lambda path, jobs: [{}]*8 if path.name == 'development_run'
                        else [{'condition': 'K'}]*32)
    monkeypatch.setattr(controller.run, 'development_executability', lambda: {'passed': True})
    monkeypatch.setattr(controller, 'gate', lambda name: True)
    stages, steps = [], []
    monkeypatch.setattr(controller, 'stage', lambda name, phase='K': stages.append((name, phase)) or True)
    monkeypatch.setattr(controller, 'step', lambda name, args: steps.append(name) or True)
    monkeypatch.setattr(controller, 'publish', lambda *args, **kw: None)
    controller.workflow('all')
    assert stages == [('formal', 'controls')]
    assert steps == ['offline_analysis']
    assert marker.read_bytes() == before
