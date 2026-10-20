"""Compile offline benchmark records into the same public mechanism statements."""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from arbiter.agent.decision import ContextBelief
from arbiter.agent.planning import MechanismOperator
from arbiter.normalization import mechanism_id


def item_id(value: str | None) -> str | None:
    return value.removeprefix("minecraft:") if isinstance(value, str) else value


def _planner_module(root: Path):
    name = "webcraft_operator_source"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, root / "oracle/planner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def compile_world_statements(root: Path, release: str) -> list[MechanismOperator]:
    data = json.loads((root / f"truth/{release}.json").read_text())
    planner = _planner_module(root)
    operators: list[MechanismOperator] = []
    for block, spec in planner.SOURCE_SPECS.items():
        produced = planner.guaranteed_source(release, block)
        if not produced:
            continue
        output, count = item_id(produced[0]), int(produced[1])
        assertion = {"block": block, "drops": [output], "kind": "drop", "silk_touch": False}
        operators.append(MechanismOperator(
            mechanism_id(assertion), "mine", {"node_type": block}, (), output, count,
            probability=0.99, requires=(() if spec["tool"] is None else (spec["tool"],)),
        ))
    for recipe in data["recipes"]:
        station = recipe.get("station")
        if station not in {"crafting_grid", "furnace"}:
            continue
        output = item_id(recipe["result"].get("item"))
        if not output:
            continue
        count = int(recipe["result"].get("count", 1))
        if recipe.get("ordered"):
            grid, slots, options = [], [], []
            for row_index, row in enumerate(recipe.get("pattern", [])):
                grid_row = []
                for column_index, symbol in enumerate(row):
                    choices = [] if symbol == " " else sorted(map(item_id, recipe["key"].get(symbol, [])))
                    grid_row.append(choices)
                    if choices:
                        slots.append(row_index * 3 + column_index)
                        options.append(tuple(choices))
                grid.append(grid_row)
            assertion = {
                "count": count, "grid": grid, "kind": "recipe", "ordered": True,
                "output": output, "recipe_type": recipe["type"], "station": station,
            }
        else:
            options = [tuple(sorted(map(item_id, slot))) for slot in recipe.get("ingredients", [])]
            slots = list(range(len(options)))
            assertion = {
                "count": count, "ingredients": [list(x) for x in options], "kind": "recipe",
                "ordered": False, "output": output, "recipe_type": recipe["type"], "station": station,
            }
        if not options or any(not x for x in options):
            continue
        operators.append(MechanismOperator(
            mechanism_id(assertion), "craft_grid" if station == "crafting_grid" else "smelt",
            {"fuel": "coal"} if station == "furnace" else {}, tuple(options), output, count,
            probability=0.99, requires=(("coal",) if station == "furnace" else ()),
            layout_slots=tuple(slots),
        ))
    return operators


def certain_belief(operators: list[MechanismOperator]) -> ContextBelief:
    return ContextBelief(np.array([1.0]), {
        operator.mechanism_id: np.array([operator.probability]) for operator in operators
    })

