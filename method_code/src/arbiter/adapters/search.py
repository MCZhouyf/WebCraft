"""Public corpus adapter and pre-P0 empty development stub."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from arbiter.pinning import verify_webcraft_pin

from .webcraft import _validate_search_rows, load_webcraft_module


class EmptySearchBackend:
    synthetic = False

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        return []


class _ValidatedSearchBackend:
    def __init__(self, delegate: Any):
        self._delegate = delegate

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        return _validate_search_rows(self._delegate.search(query, k))


def load_search_backend(
    condition: str,
    index_dir: str,
    *,
    root: Path,
    pin_path: Path,
    allow_empty_stub: bool = False,
):
    pin = verify_webcraft_pin(pin_path, root, require_ready=False)
    if allow_empty_stub and not pin["corpus_p0_ready"]:
        return EmptySearchBackend()
    module = load_webcraft_module(
        "corpus.search_backend", root=root, pin_path=pin_path,
        allow_preview=True,
    )
    backend = module.SearchBackend(condition=condition, index_dir=index_dir)
    return _ValidatedSearchBackend(backend)
