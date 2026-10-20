"""Evaluator-only audit of oracle-plan mechanisms against public claim caches.

This module is deliberately outside the agent/search path.  It reads frozen oracle
plans and truth to decide whether cached claims can support each true plan step.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
WORLDS = ("1.16.5", "1.19", "1.21.11")
READING_MODES = ("all_public", "llm_only")
FAMILY_MARKERS = (
    "any ",
    "matching ",
    "planks",
    "logs",
    "wood",
    "wool",
    "carpet",
    "boat",
    "chest boat",
)


@dataclass(frozen=True)
class Requirement:
    task_id: str
    world: str
    mechanism_id: str
    mechanism_type: str
    subject_item: str
    payload: dict[str, Any]


def _identity(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "solv_" + hashlib.sha256(encoded.encode()).hexdigest()[:20]


def _trim_layout(layout: list[Any]) -> tuple[tuple[Any, ...], ...]:
    if len(layout) != 9:
        return ()
    rows = [layout[offset : offset + 3] for offset in range(0, 9, 3)]
    occupied_rows = [index for index, row in enumerate(rows) if any(x is not None for x in row)]
    occupied_cols = [index for index in range(3) if any(row[index] is not None for row in rows)]
    if not occupied_rows or not occupied_cols:
        return ()
    return tuple(
        tuple(rows[row][column] for column in range(min(occupied_cols), max(occupied_cols) + 1))
        for row in range(min(occupied_rows), max(occupied_rows) + 1)
    )


def requirements_from_task(task: dict[str, Any]) -> list[Requirement]:
    requirements: list[Requirement] = []
    for world in WORLDS:
        plan = task.get("oracle_plans", {}).get(world)
        if not plan or not plan.get("feasible"):
            continue
        seen: set[str] = set()
        for action in plan.get("actions", []):
            method = action["method"]
            expected = action["expected"]
            if method == "craft_grid":
                mechanism_type = "recipe"
                payload = {
                    "ingredients": [item for item in action["layout"] if item],
                    "layout": action["layout"],
                    "output_count": int(expected["count"]),
                    "station": "crafting_grid",
                }
                subject = expected["name"]
            elif method == "smelt":
                mechanism_type = "smelting"
                payload = {
                    "ingredients": [action["input"]],
                    "output_count": int(expected["count"]) // int(action["n"]),
                    "station": "furnace",
                }
                subject = expected["name"]
            elif method == "mine":
                mechanism_type = "drop"
                payload = {
                    "drops": [expected["name"]],
                    "silk_touch": False,
                }
                subject = action["node_type"]
            else:
                continue
            body = {
                "mechanism_type": mechanism_type,
                "subject_item": subject,
                "payload": payload,
            }
            mechanism_id = _identity(body)
            if mechanism_id in seen:
                continue
            seen.add(mechanism_id)
            requirements.append(
                Requirement(task["id"], world, mechanism_id, mechanism_type, subject, payload)
            )
    return requirements


def load_requirements(tasks_path: Path) -> list[Requirement]:
    rows = [json.loads(line) for line in tasks_path.read_text().splitlines() if line.strip()]
    return [requirement for task in rows for requirement in requirements_from_task(task)]


def _claim_is_current_positive(claim: dict[str, Any]) -> bool:
    return claim.get("polarity") == "positive" and claim.get("tense") == "current"


def claim_matches(requirement: Requirement, claim: dict[str, Any], *, subject: bool = True) -> bool:
    if not _claim_is_current_positive(claim):
        return False
    if subject and claim.get("subject_item") != requirement.subject_item:
        return False
    payload = claim.get("payload")
    if not isinstance(payload, dict):
        return False
    if requirement.mechanism_type == "drop":
        if claim.get("claim_type") != "drop":
            return False
        if payload.get("conditional_variants") or payload.get("silk_touch"):
            return False
        drops = payload.get("drops")
        return isinstance(drops, list) and requirement.payload["drops"][0] in drops
    if claim.get("claim_type") != requirement.mechanism_type:
        return False
    try:
        if int(payload.get("output_count", 1)) != requirement.payload["output_count"]:
            return False
    except (TypeError, ValueError):
        return False
    ingredients = payload.get("ingredients")
    if not isinstance(ingredients, list):
        return False
    if requirement.mechanism_type == "smelting":
        return requirement.payload["ingredients"][0] in ingredients
    actual_layout = payload.get("layout")
    if isinstance(actual_layout, list) and len(actual_layout) == 9:
        options = payload.get("layout_options")
        if isinstance(options, list) and len(options) == 9:
            expected = _trim_layout(requirement.payload["layout"])
            actual = _trim_layout(options)
            if len(actual) != len(expected) or any(
                len(actual_row) != len(expected_row)
                for actual_row, expected_row in zip(actual, expected)
            ):
                return False
            return all(
                expected_item is None
                if actual_options is None
                else expected_item in actual_options
                for expected_row, actual_row in zip(expected, actual)
                for expected_item, actual_options in zip(expected_row, actual_row)
            )
        return _trim_layout(actual_layout) == _trim_layout(requirement.payload["layout"])
    return Counter(ingredients) == Counter(requirement.payload["ingredients"])


def _family_related(requirement: Requirement, claims: Iterable[dict[str, Any]]) -> bool:
    for claim in claims:
        if claim.get("claim_type") != requirement.mechanism_type:
            continue
        if claim.get("subject_item") != requirement.subject_item:
            continue
        text = claim.get("span_text", "").casefold()
        payload_text = json.dumps(claim.get("payload", {}), sort_keys=True).casefold()
        if any(marker in text or f'"{marker.strip()}"' in payload_text for marker in FAMILY_MARKERS):
            return True
    return False


def classify_missing(
    requirement: Requirement,
    cache_claims: list[dict[str, Any]],
    page_claims: list[dict[str, Any]],
) -> str:
    if _family_related(requirement, cache_claims):
        return "family_generic"
    if any(claim_matches(requirement, claim, subject=False) for claim in cache_claims):
        return "subject_normalization"
    if any(claim_matches(requirement, claim) for claim in page_claims):
        return "page_level_chunk_missing"
    return "page_level_absent"


def load_cache(path: Path) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            claims.extend(json.loads(line)["claims"])
    return claims


def llm_extractor_version(root: Path = ROOT) -> str:
    return hashlib.sha256((root / "corpus/extract_claims.py").read_bytes()).hexdigest()[:8]


def claims_for_reading_mode(
    claims: Iterable[dict[str, Any]], mode: str, *, llm_version: str
) -> list[dict[str, Any]]:
    if mode not in READING_MODES:
        raise ValueError(f"unknown reading mode: {mode}")
    selected = []
    for claim in claims:
        is_llm = claim.get("extractor_version") == llm_version
        if (
            mode == "all_public"
            or mode == "llm_only" and is_llm
        ):
            selected.append(claim)
    return selected


def load_page_claims(private_root: Path) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    paths = sorted(private_root.glob("claims_gold_*.jsonl"))
    paths.extend(sorted(private_root.glob("claims_llm*.jsonl")))
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                claims.append(json.loads(line))
    return claims


def page_claims_for_world(
    claims: list[dict[str, Any]], world: str, *, matched: bool
) -> list[dict[str, Any]]:
    aliases = {"1.16.5": {"1.16.5", "T1"}, "1.19": {"1.19", "T2"},
               "1.21.11": {"1.21.11", "T3", "current"}}
    selected = []
    for claim in claims:
        vector = claim.get("truth_vector")
        if isinstance(vector, dict) and vector.get(world) is False:
            continue
        timepoint = claim.get("source", {}).get("timepoint")
        if matched and timepoint not in aliases[world]:
            continue
        selected.append(claim)
    return selected


def validate_requirements(requirements: list[Requirement], root: Path) -> None:
    truths = {world: json.loads((root / f"truth/{world}.json").read_text()) for world in WORLDS}
    recipes = {
        world: {recipe["id"]: recipe for recipe in truth["recipes"]}
        for world, truth in truths.items()
    }
    drops = {
        world: {
            row["block"].removeprefix("minecraft:"): {
                item.removeprefix("minecraft:") for item in row["no_silk_touch_items"]
            }
            for row in truth["drops"]
        }
        for world, truth in truths.items()
    }
    task_rows = {
        row["id"]: row
        for row in (json.loads(line) for line in (root / "data/tasks.jsonl").read_text().splitlines())
    }
    for requirement in requirements:
        plan = task_rows[requirement.task_id]["oracle_plans"][requirement.world]
        if requirement.mechanism_type == "drop":
            output = requirement.payload["drops"][0]
            if output not in drops[requirement.world].get(requirement.subject_item, set()):
                raise ValueError(f"oracle drop is absent from truth: {requirement}")
            continue
        matching_actions = [
            action
            for action in plan["actions"]
            if action["method"] in {"craft_grid", "smelt"}
            and action["expected"]["name"] == requirement.subject_item
        ]
        if not matching_actions or not any(
            action["recipe_id"] in recipes[requirement.world] for action in matching_actions
        ):
            raise ValueError(f"oracle recipe is absent from truth: {requirement}")


def audit_condition(
    requirements: list[Requirement], claims: list[dict[str, Any]],
    page_claims: list[dict[str, Any]], *, matched: bool = False,
) -> dict[str, tuple[bool, str, str]]:
    result: dict[str, tuple[bool, str, str]] = {}
    for requirement in requirements:
        hit = next((claim for claim in claims if claim_matches(requirement, claim)), None)
        key = f"{requirement.task_id}:{requirement.world}:{requirement.mechanism_id}"
        if hit:
            result[key] = (True, str(hit["claim_id"]), "")
        else:
            relevant_pages = page_claims_for_world(
                page_claims, requirement.world, matched=matched
            )
            result[key] = (
                False, "", classify_missing(requirement, claims, relevant_pages)
            )
    return result


def summarize(
    requirements: list[Requirement], results: dict[str, dict[str, tuple[bool, str, str]]]
) -> dict[str, Any]:
    task_worlds = sorted({(row.task_id, row.world) for row in requirements})
    summary: dict[str, Any] = {}
    for condition, values in results.items():
        hits = sum(hit for hit, _, _ in values.values())
        closed = 0
        for task_id, world in task_worlds:
            keys = [
                f"{row.task_id}:{row.world}:{row.mechanism_id}"
                for row in requirements
                if row.task_id == task_id and row.world == world
            ]
            closed += bool(keys) and all(values[key][0] for key in keys)
        summary[condition] = {
            "mechanisms_hit": hits,
            "mechanisms_total": len(requirements),
            "mechanism_coverage": hits / len(requirements),
            "task_worlds_closed": closed,
            "task_worlds_total": len(task_worlds),
            "task_world_closure": closed / len(task_worlds),
            "missing_reasons": dict(
                Counter(reason for hit, _, reason in values.values() if not hit)
            ),
        }
    return summary


def p6_gap_crosscheck(path: Path, requirements: list[Requirement], mixed) -> dict[str, Any]:
    if not path.is_dir():
        return {"available": False}
    p6_demands: set[str] = set()
    for evidence in sorted((path / "evidence").glob("mixed__*.json")):
        bundle = json.loads(evidence.read_text())
        for event in bundle.get("reader", {}).get("search_events", []):
            if event.get("phase") in {"mip_gap", "gap_closure"}:
                p6_demands.add(str(event.get("demand_item")))
    audit_missing = {
        requirement.subject_item
        for requirement in requirements
        if requirement.task_id in {
            "eff_007", "fea_002", "fea_009", "pla_001",
            "pla_003", "pla_008", "pla_009", "pla_011",
        }
        and not mixed[f"{requirement.task_id}:{requirement.world}:{requirement.mechanism_id}"][0]
    }
    return {
        "available": True,
        "p6_gap_items": sorted(p6_demands),
        "audit_missing_subjects": sorted(audit_missing),
        "intersection": sorted(p6_demands & audit_missing),
        "p6_only": sorted(p6_demands - audit_missing),
        "audit_only": sorted(audit_missing - p6_demands),
    }


def missing_evidence(
    requirement: Requirement, reason: str, root: Path, *, matched: bool
) -> tuple[str, str, str, str]:
    if not reason:
        return "", "", "", ""
    categories = {
        "page_level_absent": "true_silence",
        "page_level_chunk_missing": "page_level_chunk_missing",
        "family_generic": "family_generic",
        "subject_normalization": "subject_normalization",
    }
    label = {"1.16.5": "T1", "1.19": "T2", "1.21.11": "T3"}[requirement.world]
    page_map = json.loads((root / "corpus/manifests/page_map_p1.json").read_text())
    mapping = next(
        (row for row in page_map if row["item_id"] == requirement.subject_item), None
    )
    if not mapping:
        return categories.get(reason, reason), "", "", ""
    title = mapping.get("resolved_title", {}).get(label)
    manifest = json.loads(
        (root / "corpus/manifests/redirect_resolved_snapshots.json").read_text()
    )
    metadata = manifest.get(label, {}).get(title, {}) if title else {}
    if not metadata:
        return "redirect_unresolved", title or "", "", ""
    raw_path = root / "corpus/raw/redirect_resolved" / metadata["wikitext"]
    text = raw_path.read_text()
    from corpus.build_indexes import wikitext_to_text

    rendered = wikitext_to_text(text)
    markers = ("Obtaining. Breaking.", "Crafting.", "Smelting", "Breaking.")
    offsets = [rendered.find(marker) for marker in markers if rendered.find(marker) >= 0]
    start = min(offsets) if offsets else 0
    excerpt = rendered[start:start + 200]
    return categories.get(reason, reason), title, str(metadata.get("revid", "")), excerpt


def write_csv(
    path: Path, requirements: list[Requirement], results, root: Path
) -> None:
    columns = [
        "task_id", "world", "mechanism_id", "mechanism_type", "subject_item",
        "required_payload",
    ]
    for condition in ("matched", "mixed"):
        for mode in READING_MODES:
            columns.extend((f"{condition}_{mode}_hit", f"{condition}_{mode}_claim_id"))
        columns.extend((
            f"{condition}_all_public_missing_reason",
            f"{condition}_all_public_missing_category",
            f"{condition}_all_public_evidence_page",
            f"{condition}_all_public_evidence_revid",
            f"{condition}_all_public_evidence_excerpt",
        ))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for requirement in requirements:
            key = f"{requirement.task_id}:{requirement.world}:{requirement.mechanism_id}"
            matched = results["matched_all_public"][key]
            mixed = results["mixed_all_public"][key]
            matched_evidence = missing_evidence(
                requirement, matched[2], root, matched=True
            )
            mixed_evidence = missing_evidence(
                requirement, mixed[2], root, matched=False
            )
            row = {
                "task_id": requirement.task_id,
                "world": requirement.world,
                "mechanism_id": requirement.mechanism_id,
                "mechanism_type": requirement.mechanism_type,
                "subject_item": requirement.subject_item,
                "required_payload": json.dumps(requirement.payload, sort_keys=True),
                "matched_all_public_missing_reason": matched[2],
                "matched_all_public_missing_category": matched_evidence[0],
                "matched_all_public_evidence_page": matched_evidence[1],
                "matched_all_public_evidence_revid": matched_evidence[2],
                "matched_all_public_evidence_excerpt": matched_evidence[3],
                "mixed_all_public_missing_reason": mixed[2],
                "mixed_all_public_missing_category": mixed_evidence[0],
                "mixed_all_public_evidence_page": mixed_evidence[1],
                "mixed_all_public_evidence_revid": mixed_evidence[2],
                "mixed_all_public_evidence_excerpt": mixed_evidence[3],
            }
            for condition in ("matched", "mixed"):
                for mode in READING_MODES:
                    value = results[f"{condition}_{mode}"][key]
                    row[f"{condition}_{mode}_hit"] = str(value[0]).lower()
                    row[f"{condition}_{mode}_claim_id"] = value[1]
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--index-version", choices=("formal", "formal_p1"), required=True)
    parser.add_argument("--p6-diagnostics", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    requirements = load_requirements(root / "data/tasks.jsonl")
    validate_requirements(requirements, root)
    cache_root = root / "corpus/claims_cache"
    if args.index_version == "formal_p1":
        cache_root /= "formal_p1"
    page_claims = load_page_claims(root / "corpus/private")
    matched_claims = {
        world: load_cache(cache_root / f"matched_{world}/chunk_claims.jsonl")
        for world in WORLDS
    }
    llm_version = llm_extractor_version(root)
    mixed_claims = load_cache(cache_root / "mixed/chunk_claims.jsonl")
    results = {}
    for mode in READING_MODES:
        matched_results = {}
        for requirement in requirements:
            key = f"{requirement.task_id}:{requirement.world}:{requirement.mechanism_id}"
            selected = claims_for_reading_mode(
                matched_claims[requirement.world], mode, llm_version=llm_version
            )
            matched_results[key] = audit_condition(
                [requirement], selected, page_claims, matched=True
            )[key]
        results[f"matched_{mode}"] = matched_results
        results[f"mixed_{mode}"] = audit_condition(
            requirements,
            claims_for_reading_mode(mixed_claims, mode, llm_version=llm_version),
            page_claims,
        )
    output = root / f"reports/corpus_stats/solvability_{args.index_version}.csv"
    write_csv(output, requirements, results, root)
    report = {
        "index_version": args.index_version,
        "output": str(output),
        "summary": summarize(requirements, results),
    }
    if args.p6_diagnostics:
        report["p6_crosscheck"] = p6_gap_crosscheck(
            args.p6_diagnostics, requirements, results["mixed_all_public"]
        )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
