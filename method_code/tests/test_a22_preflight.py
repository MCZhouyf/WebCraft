import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'a22_preflight', Path(__file__).resolve().parents[1] / 'scripts/preflight_a22_pf_rag.py',
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def bundle(text='A public recipe.'):
    return {
        'task_id': 'pla_001', 'condition': 'mixed', 'reading_mode': 'all_public',
        'claim_source': 'precomputed_cache',
        'reader': {'search_count': 1, 'search_events': [{}]},
        'search_rows': [{
            'text': text, 'source_site': 'public', 'page_title': 'Example',
            'revid': '42', 'timestamp': '2026-10-01', 'chunk_id': 'chunk',
        }],
    }


def test_scan_reports_public_versions_without_mutating_text():
    data = bundle('Changed in 1.16.5; now 1.21.11.')
    row, matches, issues = module.audit_bundle(data, 'example.json')
    assert row['version_occurrences'] == 2
    assert {r['version_text'] for r in matches} == {'1.16.5', '1.21.11'}
    assert not issues
    assert data['search_rows'][0]['text'] == 'Changed in 1.16.5; now 1.21.11.'


def test_no_version_is_not_inferred_from_condition_or_world():
    row, matches, issues = module.audit_bundle(bundle(), 'example.json')
    assert row['version_occurrences'] == 0
    assert not matches and not issues


def test_rejects_non_dev_inputs():
    data = bundle()
    data['task_id'] = 'heldout'
    with pytest.raises(ValueError, match='non-development'):
        module.audit_bundle(data, 'example.json')


def test_rejects_extra_public_fields_and_search_overrun():
    data = bundle()
    data['search_rows'][0]['hidden'] = True
    data['reader']['search_events'] = [{}] * 61
    _, _, issues = module.audit_bundle(data, 'example.json')
    assert 'search_field_contract' in issues
    assert 'search_count_mismatch_or_over_budget' in issues
