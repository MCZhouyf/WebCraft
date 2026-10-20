"""Compile public cached claim payloads into canonical mechanism assertions."""
from __future__ import annotations

import ast
import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


def _literal_assignment(path: Path, symbol: str) -> Mapping[str, str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == symbol and value is not None:
            result = ast.literal_eval(value)
            if not isinstance(result, dict) or not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in result.items()
            ):
                raise RuntimeError(f"{path}:{symbol} is not a string mapping")
            return result
    raise RuntimeError(f"{path} does not define {symbol}")


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_display(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = value.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class CorpusItemNormalizer:
    """Read-only projection of the corpus normalizer's public alias mappings."""

    aliases: Mapping[str, str]
    provenance: tuple[tuple[str, str, str], ...]

    @classmethod
    def from_config(
        cls, root: Path, sources: Iterable[Mapping[str, str]],
    ) -> "CorpusItemNormalizer":
        aliases: dict[str, str] = {}
        provenance = []
        for source in sources:
            if set(source) != {"path", "sha256", "symbol"}:
                raise RuntimeError("invalid compiler normalizer source")
            path = root / source["path"]
            actual = _source_hash(path)
            if actual != source["sha256"]:
                raise RuntimeError(f"compiler normalizer hash mismatch: {source['path']}")
            values = _literal_assignment(path, source["symbol"])
            aliases.update({key.casefold(): value for key, value in values.items()})
            provenance.append((source["path"], actual, source["symbol"]))
        return cls(aliases, tuple(provenance))

    def item_id(self, value: str) -> str:
        cleaned = _normalize_display(value).removeprefix("minecraft:")
        key = re.sub(r"\s+", " ", cleaned.casefold().replace("-", " ")).strip()
        if key in self.aliases:
            return self.aliases[key]
        return re.sub(r"[^a-z0-9]+", "_", key).strip("_")


DEFAULT_NORMALIZER = CorpusItemNormalizer({}, ())


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _trim_grid(grid: list[list[tuple[str, ...]]]) -> list[list[tuple[str, ...]]]:
    while grid and not any(grid[0]):
        grid.pop(0)
    while grid and not any(grid[-1]):
        grid.pop()
    if not grid:
        return []
    while grid and not any(row[0] for row in grid):
        grid = [row[1:] for row in grid]
    while grid and not any(row[-1] for row in grid):
        grid = [row[:-1] for row in grid]
    return grid


def _canonical_grid(
    layout: list[Any], layout_options: Any, normalizer: CorpusItemNormalizer,
) -> list[list[tuple[str, ...]]]:
    options_valid = isinstance(layout_options, list) and len(layout_options) == 9
    cells: list[tuple[str, ...]] = []
    for index, value in enumerate(layout):
        raw_options = layout_options[index] if options_valid else None
        if isinstance(raw_options, list):
            choices = tuple(sorted({
                normalizer.item_id(item) for item in raw_options if isinstance(item, str)
            }))
        elif isinstance(value, str):
            choices = (normalizer.item_id(value),)
        else:
            choices = ()
        cells.append(choices)
    grid = _trim_grid([cells[offset:offset + 3] for offset in range(0, 9, 3)])
    mirror = [list(reversed(row)) for row in grid]
    return min((grid, mirror), key=_compact) if grid else []


def _explicit_tool_requirement(raw: Mapping[str, Any], public_text: str) -> str | None:
    if raw.get("claim_type") != "drop":
        return None
    text = f"{raw.get('span_text', '')} {public_text}".lower()
    pattern = (
        r"(?:must be mined with|requires|mined with|using)\s+(?:an?\s+)?"
        r"(wooden|stone|iron|diamond|netherite)?\s*pickaxe"
    )
    tiers = {"wooden": 0, "stone": 1, "iron": 2, "diamond": 3, "netherite": 4}
    matches = [(match.group(1) or "wooden") for match in re.finditer(pattern, text)]
    return min(matches, key=tiers.__getitem__) + "_pickaxe" if matches else None


def compile_claim_assertions(
    raw: Mapping[str, Any], public_text: str = "",
    mineable_nodes: set[str] | None = None,
    normalizer: CorpusItemNormalizer = DEFAULT_NORMALIZER,
) -> list[dict[str, Any]]:
    """Compile one contract-v2 claim; alternatives may yield multiple assertions."""
    kind = raw.get("claim_type")
    payload = raw.get("payload")
    subject = raw.get("subject_item")
    if not isinstance(payload, Mapping) or not isinstance(subject, str):
        return []
    subject_id = normalizer.item_id(subject)
    if kind == "drop":
        if mineable_nodes is not None and subject_id not in mineable_nodes:
            return []
        raw_drops = payload.get("drops")
        if not isinstance(raw_drops, list) or not raw_drops or not all(
            isinstance(value, str) for value in raw_drops
        ):
            return []
        drops = [normalizer.item_id(value) for value in raw_drops]
        identity_drops = [drops[0]] if all(value == drops[0] for value in drops) else drops
        assertion: dict[str, Any] = {
            "kind": "drop", "block": subject_id, "drops": identity_drops,
            "silk_touch": bool(payload.get("silk_touch", False)),
        }
        execution: dict[str, Any] = {}
        tool = _explicit_tool_requirement(raw, public_text)
        if tool:
            execution["requires"] = [normalizer.item_id(tool)]
        if len(identity_drops) == 1:
            execution["output_count"] = len(drops)
        if execution:
            assertion["_execution"] = execution
        return [assertion]
    if kind not in {"recipe", "smelting"}:
        return []
    raw_ingredients = payload.get("ingredients")
    if not isinstance(raw_ingredients, list) or not raw_ingredients or not all(
        isinstance(value, str) for value in raw_ingredients
    ):
        return []
    ingredients = [normalizer.item_id(value) for value in raw_ingredients]
    try:
        output_count = int(payload.get("output_count", 1))
    except (TypeError, ValueError):
        return []
    if output_count < 1:
        return []
    station = "furnace" if kind == "smelting" else "crafting_grid"
    base = {
        "kind": "recipe", "output": subject_id, "count": output_count,
        "station": station,
    }
    if kind == "smelting":
        return [{
            **base, "ordered": False, "recipe_type": "smelting",
            "ingredients": [[ingredient]],
        } for ingredient in dict.fromkeys(ingredients)]
    layout = payload.get("layout")
    if isinstance(layout, list) and len(layout) == 9:
        grid = _canonical_grid(layout, payload.get("layout_options"), normalizer)
        if not grid:
            return []
        return [{
            **base, "ordered": True, "recipe_type": "crafting_shaped",
            "grid": [[list(cell) for cell in row] for row in grid],
        }]
    options = sorted(([ingredient] for ingredient in ingredients), key=_compact)
    return [{
        **base, "ordered": False, "recipe_type": "crafting_shapeless",
        "ingredients": options,
    }]
