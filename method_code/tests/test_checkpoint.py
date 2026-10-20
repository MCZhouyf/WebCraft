import pytest

import arbiter.checkpoint as checkpoint


def test_explicit_authorized_total_overrides_reserve(monkeypatch):
    monkeypatch.setattr(checkpoint, "effective_available_bytes", lambda: 2 * 1024**3)
    monkeypatch.delenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES", raising=False)
    with pytest.raises(RuntimeError):
        checkpoint.require_memory(512 * 1024**2, reserve_bytes=2 * 1024**3)
    monkeypatch.setenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES", str(2 * 1024**3))
    assert checkpoint.require_memory(512 * 1024**2, reserve_bytes=2 * 1024**3) == 2 * 1024**3


def test_memory_requirement_matches_authorized_total(monkeypatch):
    monkeypatch.delenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES", raising=False)
    assert checkpoint.memory_requirement(10, 20) == 30
    monkeypatch.setenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES", "25")
    assert checkpoint.memory_requirement(10, 20) == 25
    assert checkpoint.memory_requirement(30, 20) == 30
    monkeypatch.setenv("ARB_AUTHORIZED_MEMORY_TOTAL_BYTES", "invalid")
    with pytest.raises(ValueError):
        checkpoint.memory_requirement(10, 20)
