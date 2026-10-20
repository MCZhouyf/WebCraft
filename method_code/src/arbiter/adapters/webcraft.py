"""Narrow wrappers around WebCraft public modules and semantic methods."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Sequence

from arbiter.pinning import verify_webcraft_pin


PUBLIC_METHODS = (
    "mine", "craft_grid", "smelt", "check_name", "inventory",
    "report_version", "submit", "search",
)


def load_webcraft_module(
    name: str, *, root: Path, pin_path: Path, allow_preview: bool = False,
):
    """Import a pinned public module without copying or patching it."""
    verify_webcraft_pin(
        pin_path, root, require_ready=True, allow_preview=allow_preview,
    )
    resolved = str(root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    return importlib.import_module(name)


class PublicSemanticAdapter:
    """Expose only the eight public methods; evaluator capabilities are never accepted."""

    def __init__(self, api: Any):
        missing = [name for name in PUBLIC_METHODS if not callable(getattr(api, name, None))]
        if missing:
            raise TypeError(f"semantic API is missing public methods: {missing}")
        self.__api = api
        self.__node_ordinals: dict[str, int] = {}

    def mine(self, node_type: str, *, intent: str | None = None) -> dict[str, Any]:
        name = node_type.removeprefix("minecraft:")
        ordinal = self.__node_ordinals.get(name, 0) + 1
        self.__node_ordinals[name] = ordinal
        if name == "oak_log":
            node_prefix = "log"
        elif name == "cobblestone":
            node_prefix = "cobble"
        else:
            node_prefix = name.removesuffix("_ore")
        return self.__api.mine(f"{node_prefix}_{ordinal}", intent=intent)

    def craft_grid(self, layout: Sequence[str | None], *,
                   intent: str | None = None) -> dict[str, Any]:
        return self.__api.craft_grid(layout, intent=intent)

    def smelt(self, input_item: str, fuel: str, n: int, *,
              intent: str | None = None) -> dict[str, Any]:
        return self.__api.smelt(input_item, fuel, n, intent=intent)

    def check_name(self, identifier: str, *, intent: str | None = None) -> dict[str, Any]:
        return self.__api.check_name(identifier, intent=intent)

    def inventory(self, *, intent: str | None = None) -> list[dict[str, Any]]:
        return self.__api.inventory(intent=intent)

    def report_version(self, guess: str, *, intent: str | None = None) -> dict[str, Any]:
        return self.__api.report_version(guess, intent=intent)

    def submit(self, result: Any, *, intent: str | None = None) -> dict[str, Any]:
        return self.__api.submit(result, intent=intent)

    def search(self, query: str, k: int, *,
               intent: str | None = None) -> list[dict[str, Any]]:
        rows = self.__api.search(query, k, intent=intent)
        return _validate_search_rows(rows)


def _validate_search_rows(rows: Any) -> list[dict[str, Any]]:
    fields = {"text", "source_site", "page_title", "revid", "timestamp", "chunk_id"}
    if not isinstance(rows, list):
        raise TypeError("search must return a list")
    if any(not isinstance(row, dict) or set(row) != fields for row in rows):
        raise ValueError("search result violates the public corpus schema")
    return rows
