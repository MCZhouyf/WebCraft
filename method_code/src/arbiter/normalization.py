"""Canonical mechanism IDs compatible with WebCraft conflict records."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def canonical_assertion(assertion: dict[str, Any]) -> str:
    return json.dumps(assertion, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def mechanism_id(assertion: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_assertion(assertion).encode("utf-8")).hexdigest()
    return f"cf_{digest[:16]}"


@dataclass(frozen=True)
class ProbeSpec:
    mechanism_id: str
    method: str
    params: dict[str, Any]
    canonical_mechanism: str


def _first(options: Any) -> str | None:
    if not options:
        return None
    if isinstance(options, str):
        return options
    return sorted(options)[0]


def generate_probe(assertion: dict[str, Any]) -> dict[str, Any]:
    kind = assertion.get("kind")
    if kind == "drop":
        return {"method": "mine", "params": {"node_type": assertion["block"]}}
    if kind == "name":
        return {"method": "check_name", "params": {"identifier": assertion["identifier"]}}
    if kind == "recipe" and assertion.get("station") == "crafting_grid":
        if assertion.get("ordered"):
            rows = assertion.get("grid", [])
            layout = [_first(cell) for row in rows for cell in row]
            layout = (layout + [None] * 9)[:9]
        else:
            layout = [_first(slot) for slot in assertion.get("ingredients", [])]
            layout = (layout + [None] * 9)[:9]
        return {"method": "craft_grid", "params": {"layout": layout}}
    if kind == "recipe" and assertion.get("station") == "furnace":
        ingredients = assertion.get("ingredients") or assertion.get("input") or []
        return {
            "method": "smelt",
            "params": {"input_item": _first(ingredients), "fuel": "coal", "n": 1},
        }
    raise ValueError(f"assertion has no semantic probe: {kind!r}")


def assertion_to_probe(
    assertion: dict[str, Any], probe: dict[str, Any] | None = None,
) -> ProbeSpec:
    selected = probe or generate_probe(assertion)
    method = selected.get("method")
    params = selected.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        raise ValueError("probe must contain method and params")
    return ProbeSpec(mechanism_id(assertion), method, params, canonical_assertion(assertion))


def probe_to_assertion(spec: ProbeSpec) -> dict[str, Any]:
    assertion = json.loads(spec.canonical_mechanism)
    if mechanism_id(assertion) != spec.mechanism_id:
        raise ValueError("probe mechanism integrity check failed")
    return assertion
