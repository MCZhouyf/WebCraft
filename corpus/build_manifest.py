"""Build frozen P0/P1 item manifests through the read-only oracle planner."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oracle.planner import OraclePlanner, VERSIONS


SEPARATOR_ITEMS = {
    "bundle",
    "chain",
    "cobbled_deepslate",
    "copper_ingot",
    "copper_pickaxe",
    "iron_chain",
    "iron_ingot",
    "iron_nugget",
    "iron_ore",
    "leather",
    "lodestone",
    "name_tag",
    "netherite_ingot",
    "paper",
    "raw_iron",
    "saddle",
    "scute",
    "stick",
    "stone_pickaxe",
    "string",
    "turtle_scute",
}

TITLE_OVERRIDES = {
    "chest_minecart": "Minecart with Chest",
    "hopper_minecart": "Minecart with Hopper",
    "furnace_minecart": "Minecart with Furnace",
    "oak_chest_boat": "Boat with Chest",
    "coal_ore": "Coal Ore",
    "redstone_ore": "Redstone Ore",
    "lapis_ore": "Lapis Lazuli Ore",
}


def candidate_title(item: str) -> str:
    return TITLE_OVERRIDES.get(item, item.replace("_", " ").title())


def load_tasks() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (ROOT / "data/tasks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build(scope: str) -> dict[str, Any]:
    selected = [t for t in load_tasks() if scope == "p1" or t["split"] == "dev"]
    items = set(SEPARATOR_ITEMS)
    per_task: dict[str, dict[str, list[str]]] = {}
    for task in selected:
        per_task[task["id"]] = {}
        target = task["target"]["item"]
        for version in VERSIONS:
            planner = OraclePlanner(version)
            needed, _, sources = planner._closure({target})
            closure = set(needed) | {target}
            for source in sources:
                closure.update((source["block"], source["output"]))
            per_task[task["id"]][version] = sorted(closure)
            items.update(closure)
    return {
        "scope": scope,
        "task_count": len(selected),
        "task_ids": [task["id"] for task in selected],
        "separator_probe_count": 12,
        "items": [
            {"item_id": item, "candidate_title": candidate_title(item)}
            for item in sorted(items)
        ],
        "oracle_closures": per_task,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("p0", "p1"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unmapped-report", type=Path)
    parser.add_argument(
        "--unmapped-reason",
        default="Stopped before title mapping: source policy gate did not pass",
    )
    args = parser.parse_args()
    payload = build(args.scope)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if args.unmapped_report:
        args.unmapped_report.parent.mkdir(parents=True, exist_ok=True)
        with args.unmapped_report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("item_id", "candidate_page_title", "status", "reason"))
            for item in payload["items"]:
                writer.writerow(
                    (
                        item["item_id"],
                        item["candidate_title"],
                        "not_attempted",
                        args.unmapped_reason,
                    )
                )
    print(json.dumps({"scope": args.scope, "tasks": payload["task_count"], "items": len(payload["items"])}))


if __name__ == "__main__":
    main()
