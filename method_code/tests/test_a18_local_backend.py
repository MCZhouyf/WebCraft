"""Synthetic/local-metadata tests only; no model load or GPU allocation."""
import json

import pytest

from arbiter.llm.gateway import GatewayError
from scripts.a18_local_backend import IDENTITY, check_context, validate_request
from scripts.prepare_a18_local_model import LOCK, VENDOR


def test_registered_decoding_is_not_silently_downgraded():
    kwargs = {'temperature': 0.0, 'thinking': 'disabled', 'json_object': True, 'max_tokens': 4096}
    validate_request(kwargs)
    for key, value in (('temperature', 0.1), ('thinking', 'enabled'),
                       ('json_object', False), ('max_tokens', 512)):
        with pytest.raises(GatewayError):
            validate_request({**kwargs, key: value})


def test_context_overflow_is_not_truncation():
    check_context(1000, 4096, 40960)
    with pytest.raises(GatewayError, match='no_truncation'):
        check_context(40000, 4096, 40960)


def test_identity_and_hash_lock_are_explicitly_quantized():
    assert IDENTITY.startswith('Qwen/Qwen3-14B@') and IDENTITY.endswith(':nf4-dq-bf16')
    lock = json.loads(LOCK.read_text())
    assert len(lock['weights']) == 8
    assert sum(r['bytes'] for r in lock['weights'].values()) == 29536665640
    assert lock['packages']['torch'] == '2.1.2+cu121'
    assert lock['model_calls'] == 0


def test_generic_json_grammar_keeps_unicode_without_forcing_action_answers(monkeypatch):
    monkeypatch.syspath_prepend(str(VENDOR))
    from lmformatenforcer import CharacterLevelParserConfig, JsonSchemaParser
    text = '{"label":"在当前世界有效","unrelated":true}'
    parser = JsonSchemaParser({'type': 'object'}, config=CharacterLevelParserConfig(
        alphabet=''.join(sorted(set(text + 'abcdefghijklmnopqrstuvwxyz0123456789, :{}[]"')))))
    for char in text:
        assert char in parser.get_allowed_characters()
        parser = parser.add_character(char)
    assert parser.can_end()


def test_generic_object_mode_is_not_top_level_array(monkeypatch):
    monkeypatch.syspath_prepend(str(VENDOR))
    from lmformatenforcer import JsonSchemaParser
    parser = JsonSchemaParser({'type': 'object'})
    assert '{' in parser.get_allowed_characters()
    assert '[' not in parser.get_allowed_characters()
