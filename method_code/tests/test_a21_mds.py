import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_a21_materials_have_registered_public_contract_and_costs():
    manifest = json.loads((ROOT / 'artifacts/post_review/a21_mds_20261018/materials/manifest.json').read_text())
    assert len(manifest['instances']) == 32
    assert manifest['conditions'] == ['K', 'K+S', 'K+N', 'empty']
    feasible = 0
    for row in manifest['instances']:
        material = json.loads((ROOT / row['visible_path']).read_text())
        evaluator = json.loads((ROOT / row['evaluator_path']).read_text())
        assert set(material['conditions']) == {'K', 'K+S', 'K+N', 'empty'}
        for visible in material['conditions'].values():
            assert all(set(fact) == {'mechanism fact', 'source', 'timestamp'} for fact in visible)
        if evaluator['true_feasible']:
            feasible += 1
            assert 4 <= evaluator['oracle_primary_cost'] <= 10
            assert evaluator['oracle_action_count'] == len(evaluator['oracle_actions'])
        else:
            assert evaluator['oracle_primary_cost'] is None
            assert evaluator['oracle_actions'] == []
    assert feasible == 20


def test_a21_development_material_is_disjoint_from_formal_source_tasks():
    development = json.loads((ROOT / 'artifacts/post_review/a21_mds_20261018/development_materials/manifest.json').read_text())
    formal = json.loads((ROOT / 'artifacts/post_review/a21_mds_20261018/materials/manifest.json').read_text())
    assert len(development['instances']) == 8
    formal_sources = {row['source_task_id'] for row in formal['instances']}
    assert all(row['task_id'] not in formal_sources for row in development['instances'])


def test_a21_source_tasks_share_the_frozen_rename_family():
    manifest = json.loads((ROOT / 'artifacts/post_review/a21_mds_20261018/materials/manifest.json').read_text())
    materials = [json.loads((ROOT / row['visible_path']).read_text()) for row in manifest['instances']]
    assert len({row['source_task_id'] for row in materials}) == 8
    assert len({row['family_id'] for row in materials}) == 7
    rename_families = {row['family_id'] for row in materials
                       if row['source_task_id'] in {'extv2_012', 'extv2_013'}}
    assert rename_families == {'rename:chain_iron_chain'}
