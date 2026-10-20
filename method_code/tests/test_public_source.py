"""Packaging checks only; no model calls or simulator episodes."""
import json
from pathlib import Path

import pytest

from scripts.a21_author_glm_config import AuthorConfigError, load_author_config


def test_environment_loader(monkeypatch):
    monkeypatch.setenv("ARB_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ARB_LLM_API_KEYS_JSON", json.dumps(["mock-a", "mock-b"]))
    assert load_author_config()["api_keys"] == ["mock-a", "mock-b"]


def test_environment_loader_rejects_duplicates(monkeypatch):
    monkeypatch.setenv("ARB_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ARB_LLM_API_KEYS_JSON", json.dumps(["mock-a", "mock-a"]))
    with pytest.raises(AuthorConfigError):
        load_author_config()


def test_cp_vb_source_available():
    import w13_runtime
    import w14_runtime

    expected = Path(__file__).resolve().parents[1]
    assert w13_runtime.ARB == expected
    assert w14_runtime.ARB == expected
    solve, digest = w14_runtime.cp_policy()
    assert callable(solve)
    assert len(digest) == 64
    assert callable(w13_runtime.VBRAG.solve)
