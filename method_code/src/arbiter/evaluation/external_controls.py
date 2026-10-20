"""Pure helpers for the post-review external-agent controls."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


WORLDS = ("1.16.5", "1.19", "1.21.11")
SINGLE_WORLD_CELLS = (
    ("matched_1.16.5", "1.16.5"),
    ("matched_1.19", "1.19"),
    ("matched_1.21.11", "1.21.11"),
    ("mismatched_old2new", "1.21.11"),
    ("mismatched_new2old", "1.16.5"),
)
CONDITION_CELLS = tuple(
    (condition, world) for condition in ("mixed", "polluted") for world in WORLDS
)
PUBLIC_SEARCH_FIELDS = {
    "text", "source_site", "page_title", "revid", "timestamp", "chunk_id",
}
_TOKEN = re.compile(r"[a-z0-9]+")


def public_task(task: dict[str, Any]) -> dict[str, Any]:
    """Project a task to the only fields accepted by the external loop."""
    return {
        "target": {
            "item": str(task["target"]["item"]),
            "count": int(task["target"]["count"]),
        },
        "standard_items": [dict(row) for row in task["standard_items"]],
        "task_supplements": [dict(row) for row in task.get("task_supplements", [])],
    }


def matrix_jobs(tasks: Iterable[dict[str, Any]], segment: str,
                methods: Iterable[str]) -> list[dict[str, Any]]:
    tasks = list(tasks)
    if segment == "oracle_knowledge":
        selected = [task for task in tasks if task.get("conflict_ids")]
        cells = SINGLE_WORLD_CELLS
    elif segment == "control_all_public":
        selected = [task for task in tasks if not task.get("conflict_ids")]
        cells = SINGLE_WORLD_CELLS
    elif segment == "conditions":
        selected = [task for task in tasks if task.get("conflict_ids")]
        cells = CONDITION_CELLS
    else:
        raise ValueError(f"unknown external-control segment: {segment}")
    expected = 22 if segment != "control_all_public" else 12
    if len(selected) != expected:
        raise ValueError(f"{segment} expected {expected} tasks, got {len(selected)}")
    jobs = [
        {
            "method": method,
            "condition": condition,
            "world": world,
            "task_id": task["id"],
            "task_type": task["type"],
        }
        for method in methods for condition, world in cells for task in selected
    ]
    return sorted(jobs, key=lambda row: (
        row["method"], row["condition"], row["world"], row["task_id"],
    ))


def _render_requirement(requirement: Any) -> str:
    subject = str(requirement.subject_item).replace("_", " ")
    payload = requirement.payload
    if requirement.mechanism_type == "drop":
        drops = ", ".join(str(value).replace("_", " ") for value in payload["drops"])
        return f"Breaking {subject} without Silk Touch drops {drops}."
    ingredients = ", ".join(
        str(value).replace("_", " ") for value in payload.get("ingredients", [])
    )
    count = int(payload.get("output_count", 1))
    if requirement.mechanism_type == "smelting":
        return f"Smelting {ingredients} in a furnace produces {count} {subject}."
    return f"Crafting recipe for {subject}, {count}: {ingredients}."


def _source_for(requirement: Any, gold: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        row for row in gold
        if row.get("subject_item") == requirement.subject_item
        and row.get("claim_type") == requirement.mechanism_type
        and isinstance(row.get("source"), dict)
    ]
    if candidates:
        return sorted(candidates, key=lambda row: str(row["source"].get("timestamp", "")))[-1]["source"]
    # Resource-node mechanisms have no corpus claim in the frozen gold cache.
    # The control still exposes the ordinary six public fields and no version.
    digest = hashlib.sha256(requirement.subject_item.encode()).hexdigest()
    return {
        "site": "minecraft_wiki",
        "page_title": requirement.subject_item.replace("_", " ").title(),
        "revid": int(digest[:8], 16),
        "timestamp": "2026-01-01T00:00:00Z",
    }


def oracle_search_rows(task: dict[str, Any], world: str, *, webcraft_root: Path,
                       requirements_from_task: Any) -> list[dict[str, Any]]:
    """Render every target-world oracle-plan mechanism as public search text."""
    gold: list[dict[str, Any]] = []
    for path in sorted((webcraft_root / "corpus/private").glob("claims_gold_*.jsonl")):
        gold.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    requirements = [row for row in requirements_from_task(task) if row.world == world]
    rows = []
    for requirement in requirements:
        source = _source_for(requirement, gold)
        text = _render_requirement(requirement)
        chunk_id = "oracle_control_" + hashlib.sha256(
            f"{task['id']}:{world}:{requirement.mechanism_id}".encode()
        ).hexdigest()[:20]
        row = {
            "text": text,
            "source_site": str(source["site"]),
            "page_title": str(source["page_title"]),
            "revid": source["revid"],
            "timestamp": str(source["timestamp"]),
            "chunk_id": chunk_id,
        }
        if set(row) != PUBLIC_SEARCH_FIELDS:
            raise RuntimeError("oracle projection violated public search schema")
        rows.append(row)
    return sorted(rows, key=lambda row: row["chunk_id"])


def lexical_search(rows: Iterable[dict[str, Any]], query: str, k: int) -> list[dict[str, Any]]:
    if type(k) is not int or not 1 <= k <= 10:
        raise ValueError("search k must be 1..10")
    terms = set(_TOKEN.findall(query.casefold().replace("_", " ")))
    ranked = []
    for row in rows:
        title = set(_TOKEN.findall(str(row["page_title"]).casefold().replace("_", " ")))
        body = set(_TOKEN.findall(str(row["text"]).casefold().replace("_", " ")))
        score = 3 * len(terms & title) + len(terms & body)
        ranked.append((-score, str(row["chunk_id"]), dict(row)))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in ranked[:k]]


@dataclass
class OracleSearchAdapter:
    """Replace only search observations while forwarding the seven other tools."""

    base: Any
    rows: list[dict[str, Any]]
    observations: list[dict[str, Any]]

    def __init__(self, base: Any, rows: list[dict[str, Any]]) -> None:
        self.base = base
        self.rows = rows
        self.observations = []

    def search(self, query: str, k: int, *, intent: str | None = None) -> list[dict[str, Any]]:
        # Call the public method so cost and sequence logging remain unchanged;
        # only the observation returned to the model is replaced.
        self.base.search(query, k, intent=intent)
        result = lexical_search(self.rows, query, k)
        self.observations.append({"query": query, "k": k, "result": result})
        return result

    def mine(self, node_type: str, *, intent: str | None = None) -> Any:
        return self.base.mine(node_type, intent=intent)

    def craft_grid(self, layout: list[str | None], *, intent: str | None = None) -> Any:
        return self.base.craft_grid(layout, intent=intent)

    def smelt(self, input_item: str, fuel: str, n: int, *, intent: str | None = None) -> Any:
        return self.base.smelt(input_item, fuel, n, intent=intent)

    def check_name(self, identifier: str, *, intent: str | None = None) -> Any:
        return self.base.check_name(identifier, intent=intent)

    def inventory(self, *, intent: str | None = None) -> Any:
        return self.base.inventory(intent=intent)

    def report_version(self, guess: str, *, intent: str | None = None) -> Any:
        return self.base.report_version(guess, intent=intent)

    def submit(self, result: Any, *, intent: str | None = None) -> Any:
        return self.base.submit(result, intent=intent)


def apply_oracle_search_observations(action_path: Path,
                                     observations: list[dict[str, Any]]) -> None:
    """Make the immutable trace reflect the search rows actually shown."""
    events = [json.loads(line) for line in action_path.read_text().splitlines() if line.strip()]
    searches = [event for event in events if event.get("method") == "search"]
    if len(searches) != len(observations):
        raise RuntimeError("oracle search observation/log count mismatch")
    cursor = iter(observations)
    for event in events:
        if event.get("method") == "search":
            observation = next(cursor)
            event["result"] = observation["result"]
            event["oracle_knowledge_projection"] = True
    action_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in events))
