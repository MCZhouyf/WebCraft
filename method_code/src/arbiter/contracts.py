"""Public runtime contracts shared by the agent and execution backends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, TypedDict, runtime_checkable


@dataclass(frozen=True)
class ItemStack:
    name: str
    count: int


@dataclass(frozen=True)
class Goal:
    item: str
    count: int = 1


class SearchHit(TypedDict):
    text: str
    source_site: str
    page_title: str
    revid: str
    timestamp: str | None
    chunk_id: str


@runtime_checkable
class SemanticAPI(Protocol):
    """The only environment object visible to an ARBITER runtime agent."""

    def mine(self, node_type: str, *, intent: str | None = None) -> dict[str, Any]: ...
    def craft_grid(self, layout: Sequence[str | None], *, intent: str | None = None) -> dict[str, Any]: ...
    def smelt(self, input_item: str, fuel: str, n: int, *, intent: str | None = None) -> dict[str, Any]: ...
    def check_name(self, identifier: str, *, intent: str | None = None) -> dict[str, Any]: ...
    def inventory(self, *, intent: str | None = None) -> list[dict[str, Any]]: ...
    def report_version(self, guess_sha256: str, *, intent: str | None = None) -> dict[str, Any]: ...
    def submit(self, result: Any, *, intent: str | None = None) -> dict[str, Any]: ...
    def search(self, query: str, k: int, *, intent: str | None = None) -> list[SearchHit]: ...


METHOD_COSTS: dict[str, int | str] = {
    "mine": 1,
    "craft_grid": 1,
    "smelt": "requested_positive_integer_n",
    "check_name": 1,
    "inventory": 0,
    "report_version": 0,
    "submit": 0,
    "search": 0,
}


def primary_cost(method: str, params: dict[str, Any]) -> int:
    if method == "smelt":
        n = params.get("n")
        return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 1
    charge = METHOD_COSTS.get(method)
    if not isinstance(charge, int):
        raise ValueError(f"unknown semantic method: {method}")
    return charge
