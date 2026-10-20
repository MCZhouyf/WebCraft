"""Public CDS evidence adapter and boundary-question scoring for A8."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BOUNDARY_INSTRUCTION = "\n".join((
    "For each candidate mechanism fact below, judge whether it is valid in the current world.",
    "Use only the task evidence and tool observations from this episode.",
    "Return exactly one JSON object with this schema:",
    '{"judgments":[{"index":1,"label":"在当前世界有效|无效|不确定"}]}',
    "Include every candidate index exactly once and no other fields.",
))
LABELS = {"在当前世界有效": True, "无效": False, "不确定": None}
_TOKEN = re.compile(r"[a-z0-9]+")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_w7a_freeze(webcraft_root: Path) -> dict[str, Any]:
    manifest_path = webcraft_root / "config/ext_v2/freeze_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    bad = []
    for relative, expected in manifest["inputs"].items():
        path = webcraft_root / relative
        actual = digest(path) if path.is_file() else None
        if actual != expected:
            bad.append(relative)
    if bad:
        raise RuntimeError(f"W7a freeze mismatch: {bad[:10]}")
    return manifest


def public_rows(material: dict[str, Any], condition: str) -> list[dict[str, str]]:
    if condition not in {"K", "K+S", "S", "empty", "K+N"}:
        raise ValueError(f"unknown CDS condition: {condition}")
    rows = [dict(row) for row in material["conditions"][condition]]
    allowed = {"mechanism fact", "source", "timestamp"}
    if any(set(row) != allowed or not all(isinstance(row[key], str) for key in allowed) for row in rows):
        raise RuntimeError("CDS visible row violated the frozen three-field contract")
    return rows


def lexical_search(rows: Iterable[dict[str, str]], query: str, k: int) -> list[dict[str, str]]:
    if type(k) is not int or not 1 <= k <= 10:
        raise ValueError("search k must be 1..10")
    terms = set(_TOKEN.findall(query.casefold().replace("_", " ")))
    ranked = []
    for index, row in enumerate(rows):
        fact = set(_TOKEN.findall(row["mechanism fact"].casefold().replace("_", " ")))
        source = set(_TOKEN.findall(row["source"].casefold().replace("_", " ")))
        score = 3 * len(terms & fact) + len(terms & source)
        if score:
            ranked.append((-score, index, dict(row)))
    ranked.sort(key=lambda value: (value[0], value[1]))
    return [row for _, _, row in ranked[:k]]


@dataclass
class CDSKnowledgeAdapter:
    """Inject frozen public facts and constrain search to those same facts."""

    base: Any
    rows: list[dict[str, str]]
    observations: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    _initial: bool

    def __init__(self, base: Any, rows: list[dict[str, str]]) -> None:
        self.base = base
        self.rows = rows
        self.observations = []
        self.trace = []
        self._initial = True

    def _record(self, action: str, args: dict[str, Any], result: Any) -> Any:
        self.trace.append({"action": action, "args": args, "observation": result})
        return result

    def search(self, query: str, k: int, *, intent: str | None = None) -> list[dict[str, str]]:
        self.base.search(query, k, intent=intent)
        result = lexical_search(self.rows, query, k)
        self.observations.append({"query": query, "k": k, "result": result})
        return self._record("search", {"query": query, "k": k}, result)

    def inventory(self, *, intent: str | None = None) -> Any:
        inventory = self.base.inventory(intent=intent)
        result = ({"inventory": inventory, "diagnostic_mechanism_facts": self.rows}
                  if self._initial else inventory)
        self._initial = False
        return self._record("inventory", {}, result)

    def mine(self, node_type: str, *, intent: str | None = None) -> Any:
        return self._record("mine", {"node_type": node_type}, self.base.mine(node_type, intent=intent))

    def craft_grid(self, layout: list[str | None], *, intent: str | None = None) -> Any:
        return self._record("craft_grid", {"layout": layout}, self.base.craft_grid(layout, intent=intent))

    def smelt(self, input_item: str, fuel: str, n: int, *, intent: str | None = None) -> Any:
        return self._record("smelt", {"input_item": input_item, "fuel": fuel, "n": n},
                            self.base.smelt(input_item, fuel, n, intent=intent))

    def check_name(self, identifier: str, *, intent: str | None = None) -> Any:
        return self._record("check_name", {"identifier": identifier},
                            self.base.check_name(identifier, intent=intent))

    def submit(self, result: Any, *, intent: str | None = None) -> Any:
        return self._record("submit", {"result": result}, self.base.submit(result, intent=intent))


def replace_search_results(action_path: Path, observations: list[dict[str, Any]]) -> None:
    events = [json.loads(line) for line in action_path.read_text().splitlines() if line.strip()]
    if sum(event.get("method") == "search" for event in events) != len(observations):
        raise RuntimeError("CDS search observation/log count mismatch")
    cursor = iter(observations)
    with action_path.open("w") as handle:
        for event in events:
            if event.get("method") == "search":
                event["result"] = next(cursor)["result"]
                event["cds_public_projection"] = True
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def parse_boundary(content: str, count: int) -> list[bool | None]:
    output: list[bool | None] = [None] * count
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return output
    if not isinstance(payload, dict) or set(payload) != {"judgments"} or not isinstance(payload["judgments"], list):
        return output
    seen: set[int] = set()
    duplicated: set[int] = set()
    for row in payload["judgments"]:
        if (not isinstance(row, dict) or set(row) != {"index", "label"}
                or type(row["index"]) is not int
                or not 1 <= row["index"] <= count or row["label"] not in LABELS):
            continue
        if row["index"] in seen:
            duplicated.add(row["index"])
            output[row["index"] - 1] = None
            continue
        seen.add(row["index"])
        output[row["index"] - 1] = LABELS[row["label"]]
    for index in duplicated:
        output[index - 1] = None
    return output


def boundary_truth(material: dict[str, Any], evaluator: dict[str, Any]) -> list[bool]:
    truth_by_hash = {row["candidate_text_sha256"]: bool(row["truth"])
                     for row in evaluator["boundary_truth"]}
    result = []
    for candidate in material["boundary_question"]:
        value = truth_by_hash.get(hashlib.sha256(candidate["mechanism fact"].encode()).hexdigest())
        if value is None:
            raise RuntimeError("visible boundary candidate has no evaluator truth")
        result.append(value)
    return result


def boundary_metrics(predictions: list[bool | None], truth: list[bool],
                     candidates: list[dict[str, str]]) -> dict[str, Any]:
    if len(predictions) != len(truth) or len(truth) != len(candidates):
        raise ValueError("boundary vectors differ in length")
    answered = [index for index, value in enumerate(predictions) if value is not None]
    correct = sum(predictions[index] == truth[index] for index in answered)
    newest = max(row["timestamp"] for row in candidates)
    newest_predictions = [row["timestamp"] == newest for row in candidates]
    true_count = sum(truth)
    majority_value = true_count > len(truth) / 2
    if true_count * 2 == len(truth):
        majority_value = False
    return {
        "boundary_items": len(truth), "boundary_answered": len(answered),
        "boundary_correct": correct,
        "BIA": correct / len(answered) if answered else None,
        "boundary_uncertain_rate": (len(truth) - len(answered)) / len(truth),
        "newest_timestamp_BIA": sum(a == b for a, b in zip(newest_predictions, truth)) / len(truth),
        "majority_class_BIA": sum(majority_value == value for value in truth) / len(truth),
    }
