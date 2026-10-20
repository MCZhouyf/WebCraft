import json
from pathlib import Path

import pytest

from corpus.block_templates import (
    extract_block_claims,
    public_prose_drop_claims,
    public_template_claims,
)
from corpus.build_indexes import build_index
from corpus.claims_cache import CLAIM_KEYS, _has_forbidden_key
from corpus.gold_claims import NameResolver, extract_gold


def test_explicit_self_drop_and_stone_tool_preserve_source():
    text = '{{Block2|drops=Itself|tool=stone pickaxe|hardness=3}}'
    claims = extract_block_claims(text, 'Iron Ore')
    drop = next(c for c in claims if c['claim_type'] == 'drop')
    tool = next(c for c in claims if c['claim_type'] == 'tool_requirement')
    assert drop['payload']['drops'] == ['iron_ore']
    assert drop['payload']['conditions']['tool_class'] == 'pickaxe'
    assert tool['payload']['minimum_tool_material'] == 'stone'
    assert all(c['span_text'] == text for c in claims)


def test_explicit_raw_iron_and_oak_log_drops():
    assert extract_block_claims('{{Infobox block|drops=[[Raw Iron]]}}', 'Iron Ore')[0]['payload']['drops'] == ['raw_iron']
    assert extract_block_claims('{{Block|drops=Itself}}', 'Oak Log')[0]['payload']['drops'] == ['oak_log']


def test_family_page_uses_explicit_infobox_item_names():
    claims = extract_block_claims('{{Block|invimage=Oak Log|invimage2=Birch Log|drops=Itself}}', 'Log')
    assert {c['subject_item'] for c in claims} == {'oak_log', 'birch_log'}
    assert all(c['payload']['drops'] == [c['subject_item']] for c in claims)


def test_missing_drop_is_not_default_self_and_hardness_is_not_tier():
    claims = extract_block_claims('{{Block|tool=stone pickaxe|hardness=3}}', 'Iron Ore')
    assert [c['claim_type'] for c in claims] == ['tool_requirement']
    assert extract_block_claims('{{Block|hardness=3}}', 'Oak Log') == []


def test_conditional_drop_variants_are_retained():
    raw = '[[Raw Iron]] {{only|java}};Itself with [[Silk Touch]]'
    drop = extract_block_claims('{{Block|drops=' + raw + '}}', 'Iron Ore')[0]
    assert drop['payload']['raw_drops'] == raw
    assert drop['payload']['conditional_variants'] is True
    assert len(drop['payload']['variants']) == 2


def test_inline_drop_tool_condition_is_preserved():
    raw = 'Itself when mined with a stone pickaxe'
    claim = extract_block_claims('{{Block|drops=' + raw + '}}', 'Iron Ore')[0]
    assert claim['payload']['conditions']['drop_tool']['minimum_tool_material'] == 'stone'
    assert claim['payload']['raw_drops'] == raw


def test_conditional_drop_does_not_satisfy_ordinary_mining_audit():
    from corpus.solvability_audit import Requirement, claim_matches

    requirement = Requirement('task', '1.21.11', 'mechanism', 'drop', 'iron_ore',
                              {'drops': ['iron_ore'], 'silk_touch': False})
    claim = extract_block_claims('{{Block|drops=Itself with [[Silk Touch]]}}', 'Iron Ore')[0]
    assert not claim_matches(requirement, claim)


def test_template_public_contract_and_original_span(tmp_path):
    text = '{{Block|drops=Itself|tool=axe}}'
    build_index(tmp_path, [{'text': text + '\n' + 'A log is a block. ' * 100,
                'page_title': 'Oak Log', 'source_site': 'minecraft_wiki', 'revid': 123,
                'timestamp': '2021-01-01T00:00:00Z'}], ['matched'], {})
    docs = [json.loads(line) for line in (tmp_path / 'documents.jsonl').read_text().splitlines()]
    template_chunk = next(d for d in docs if d['text'] == text)
    claims = public_template_claims(template_chunk['text'], 'Oak Log', template_chunk['chunk_id'])
    assert len(claims) == 2
    for claim in claims:
        assert set(claim) == CLAIM_KEYS
        assert not _has_forbidden_key(claim)
        assert claim['span_text'] in template_chunk['text']
        assert claim['payload']['extractor'] == 'template'
        assert claim['payload']['deterministic'] is True


def test_gold_integration_does_not_leak_annotations_into_public():
    text = '{{Block|drops=Itself|tool=axe}}'
    resolver = NameResolver()
    gold = extract_gold(text, {'page_title': 'Oak Log'}, resolver, {})
    assert len(gold) == 2
    assert 'truth_vector' in gold[0]
    public = public_template_claims(text, 'Oak Log', 'chunk-test')
    assert all('truth_vector' not in c for c in public)


def test_actual_iron_ore_snapshots_have_tool_but_no_explicit_drop_field():
    root = Path(__file__).resolve().parents[2]
    if not (root / 'corpus/raw').is_dir():
        pytest.skip('raw corpus snapshots are intentionally absent from the public export')
    from corpus.redirect_audit import sources
    for label, (records, raw) in sources(root).items():
        text = (raw / records['Iron Ore']['wikitext']).read_text()
        claims = extract_block_claims(text, 'Iron Ore')
        assert any(c['claim_type'] == 'tool_requirement' for c in claims), label
        assert not any(c['claim_type'] == 'drop' for c in claims), label


def test_crafting_template_is_a_deterministic_public_claim():
    text = '{{Crafting|A1=Iron Ingot|B1=Iron Ingot|C1=Iron Ingot|B2=Stick|B3=Stick|Output=Iron Pickaxe}}'
    claims = public_template_claims(text, 'Pickaxe', 'chunk-recipe', ('1.21.11',))
    assert len(claims) == 1
    assert claims[0]['subject_item'] == 'iron_pickaxe'
    assert claims[0]['payload']['ingredients'].count('iron_ingot') == 3
    assert claims[0]['span_text'] == text
    assert claims[0]['payload']['extractor'] == 'template'
    assert claims[0]['payload']['deterministic'] is True


def test_matching_boat_and_slab_templates_resolve_to_oak_variants():
    boat = '{{Crafting|A2=Matching Overworld Planks|C2=Matching Overworld Planks|A3=Matching Overworld Planks|B3=Matching Overworld Planks|C3=Matching Overworld Planks|Output=Matching Boat}}'
    slab = '{{Crafting|A3=Matching Planks|B3=Matching Planks|C3=Matching Planks|Output=Matching Wooden Slab,6}}'
    boat_claim = public_template_claims(boat, 'Boat', 'chunk-boat', ('1.21.11',))[0]
    slab_claim = public_template_claims(slab, 'Wooden Slab', 'chunk-slab', ('1.21.11',))[0]
    assert boat_claim['subject_item'] == 'oak_boat'
    assert boat_claim['payload']['ingredients'] == ['oak_planks'] * 5
    assert slab_claim['subject_item'] == 'oak_slab'
    assert slab_claim['payload']['ingredients'] == ['oak_planks'] * 3


def test_explicit_prose_self_and_item_drops_are_public_claims():
    text = (
        'Logs and stems drop themselves when broken with any tool. '
        'When mined without Silk Touch, iron ore drops a single raw iron.'
    )
    log = public_prose_drop_claims(text, 'Log', 'chunk-log', ['oak_log'])
    assert any(c['subject_item'] == 'oak_log' and c['payload']['drops'] == ['oak_log'] for c in log)
    ore = public_prose_drop_claims(text, 'Iron Ore', 'chunk-ore', ['iron_ore'])
    assert any(c['subject_item'] == 'iron_ore' and c['payload']['drops'] == ['raw_iron'] for c in ore)
    assert all(c['span_text'] in text for c in log + ore)


def test_prose_drop_parser_rejects_negated_and_unrelated_drops():
    text = (
        'Minecarts with TNT do not drop when detonated. '
        'A zombie has a chance to drop iron boots.'
    )
    assert public_prose_drop_claims(text, 'Minecart', 'chunk-minecart', ['minecart']) == []
    assert public_prose_drop_claims(text, 'Boots', 'chunk-boots', ['leather_boots']) == []
