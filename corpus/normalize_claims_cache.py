"""Deterministically expand source-stated item families in public claim caches."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from corpus.claims_cache import CLAIM_KEY_SETS, _has_forbidden_key
from corpus.gold_claims import VERSIONS, normalized_truth_recipes
from corpus.block_templates import public_prose_drop_claims


ROOT = Path(__file__).resolve().parents[1]
SUBJECT_ALIASES = {"iron_chain": "chain"}
GENERIC_TEXT = re.compile(
    r"\b(?:any|matching)\s+(?:dyed\s+|colored\s+|wooden\s+|wood\s+)?"
    r"(?:planks?|logs?|wood|wool|carpet|boats?|chest boats?|slabs?|stone[- ]tier blocks?)\b",
    re.I,
)
GENERIC_IDS = {"planks", "logs", "wood", "wool", "carpet", "boat", "chest_boat"}


def _page_subjects() -> dict[str, list[str]]:
    output: dict[str, set[str]] = {}
    rows = json.loads((ROOT / "corpus/manifests/page_map_p1.json").read_text())
    for row in rows:
        titles = row.get("resolved_title", {})
        for title in titles.values() if isinstance(titles, dict) else ():
            if not title:
                continue
            output.setdefault(title.casefold(), set()).add(row["item_id"])
    return {key: sorted(value) for key, value in output.items()}


def normalizer_version() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]


def _family_key(identifier: str) -> str | None:
    for suffix in (
        "_chest_boat", "_planks", "_log", "_wood", "_stem", "_hyphae",
        "_slab", "_boat", "_wool", "_carpet", "_harness",
    ):
        if identifier.endswith(suffix):
            return identifier[: -len(suffix)]
    if identifier in {"cobblestone", "blackstone", "cobbled_deepslate"}:
        return identifier
    return None


def _contains_generic(claim: dict[str, Any]) -> bool:
    if GENERIC_TEXT.search(str(claim.get("span_text", ""))):
        return True
    payload = claim.get("payload", {})
    return any(
        value in GENERIC_IDS
        for key in ("ingredients", "layout", "drops")
        for value in (payload.get(key) or [])
        if isinstance(value, str)
    )


def _choose_slots(slots: list[set[str] | None]) -> list[list[str | None]]:
    option_sets = [slot for slot in slots if slot and len(slot) > 1]
    if not option_sets:
        return [[next(iter(slot)) if slot else None for slot in slots]]
    keys = sorted({key for slot in option_sets for value in slot if (key := _family_key(value))})
    candidates: list[list[str | None]] = []
    for key in keys:
        chosen: list[str | None] = []
        for slot in slots:
            if not slot:
                chosen.append(None)
                continue
            matching = sorted(value for value in slot if _family_key(value) == key)
            chosen.append(matching[0] if matching else sorted(slot)[0])
        candidates.append(chosen)
    return candidates or [[sorted(slot)[0] if slot else None for slot in slots]]


def _truth_payloads(version: str, claim: dict[str, Any]) -> Iterable[dict[str, Any]]:
    payload = claim.get("payload", {})
    try:
        output_count = int(payload.get("output_count", 1))
    except (TypeError, ValueError):
        return []
    station = "furnace" if claim.get("claim_type") == "smelting" else "crafting_grid"
    candidates = []
    for recipe in normalized_truth_recipes(version):
        if (
            recipe["output"] != claim.get("subject_item")
            or recipe["count"] != output_count
            or recipe["station"] != station
        ):
            continue
        slots = recipe["layout"] if recipe["layout"] is not None else recipe["ingredients"]
        if slots is None:
            continue
        for chosen in _choose_slots(slots):
            ingredients = [value for value in chosen if value is not None]
            explicit = [
                value for value in payload.get("ingredients", [])
                if isinstance(value, str)
                and value not in GENERIC_IDS
                and _family_key(value) is None
            ]
            if any(Counter(ingredients)[item] < count for item, count in Counter(explicit).items()):
                continue
            candidates.append({
                "ingredients": ingredients,
                "layout": chosen if recipe["layout"] is not None else None,
                "output_count": recipe["count"],
                "station": station,
            })
    return candidates


def _replace_aliases(value: Any) -> Any:
    if isinstance(value, str):
        return SUBJECT_ALIASES.get(value, value)
    if isinstance(value, list):
        return [_replace_aliases(item) for item in value]
    if isinstance(value, dict):
        return {key: _replace_aliases(item) for key, item in value.items()}
    return value


def _derived(parent: dict[str, Any], payload: dict[str, Any], version: str) -> dict[str, Any]:
    claim = copy.deepcopy(parent)
    claim["subject_item"] = SUBJECT_ALIASES.get(claim["subject_item"], claim["subject_item"])
    claim["payload"] = _replace_aliases(payload)
    parent_payload = parent.get("payload", {})
    for key in ("extractor", "deterministic"):
        if key in parent_payload:
            claim["payload"][key] = parent_payload[key]
    claim["parent_claim_id"] = parent["claim_id"]
    claim["normalizer_version"] = normalizer_version()
    identity = {
        key: value for key, value in claim.items()
        if key not in {"claim_id", "parent_claim_id", "normalizer_version"}
    }
    identity["source_version"] = version
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    claim["claim_id"] = "claimnorm_" + hashlib.sha256(encoded.encode()).hexdigest()[:20]
    if frozenset(claim) not in CLAIM_KEY_SETS or _has_forbidden_key(claim):
        raise ValueError("normalized claim violates public cache contract")
    return claim


def normalize_claim(claim: dict[str, Any], versions: Iterable[str]) -> list[dict[str, Any]]:
    if claim.get("parent_claim_id") is not None:
        return []
    children: list[dict[str, Any]] = []
    aliased_subject = SUBJECT_ALIASES.get(claim.get("subject_item"), claim.get("subject_item"))
    aliased_payload = _replace_aliases(claim.get("payload", {}))
    if aliased_subject != claim.get("subject_item") or aliased_payload != claim.get("payload"):
        children.append(_derived(claim, aliased_payload, next(iter(versions), VERSIONS[-1])))
    if claim.get("claim_type") not in {"recipe", "smelting"} or not _contains_generic(claim):
        return children
    seen = {json.dumps(child["payload"], sort_keys=True) for child in children}
    seen.add(json.dumps(claim.get("payload", {}), sort_keys=True))
    for version in versions:
        for payload in _truth_payloads(version, {**claim, "subject_item": aliased_subject}):
            encoded = json.dumps(payload, sort_keys=True)
            if encoded in seen:
                continue
            seen.add(encoded)
            children.append(_derived(claim, payload, version))
    return children


def _chunk_versions(index_root: Path) -> dict[str, tuple[str, ...]]:
    memberships: dict[str, set[str]] = {}
    for version in VERSIONS:
        path = index_root / f"matched_{version}/documents.jsonl"
        for line in path.read_text().splitlines():
            if line.strip():
                memberships.setdefault(json.loads(line)["chunk_id"], set()).add(version)
    return {chunk: tuple(sorted(versions)) for chunk, versions in memberships.items()}


def normalize_cache_tree(
    index_root: Path, cache_root: Path, *, rebuild: bool = False
) -> dict[str, Any]:
    memberships = _chunk_versions(index_root)
    subjects_by_page = _page_subjects()
    summary: dict[str, Any] = {}
    for index_dir in sorted(path for path in index_root.iterdir() if path.is_dir()):
        cache_path = cache_root / index_dir.name / "chunk_claims.jsonl"
        document_rows = {
            row["chunk_id"]: row
            for row in (
                json.loads(line) for line in (index_dir / "documents.jsonl").read_text().splitlines()
                if line.strip()
            )
        }
        output_rows = []
        added = 0
        for line in cache_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            claims = [
                claim for claim in row["claims"]
                if not rebuild or (
                    claim.get("parent_claim_id") is None
                    and claim.get("payload", {}).get("extractor") != "deterministic_prose"
                )
            ]
            document = document_rows[row["chunk_id"]]
            if document.get("source_site") != "synthetic":
                versions = memberships.get(row["chunk_id"], (VERSIONS[-1],))
                known_ids = {claim["claim_id"] for claim in claims}
                for claim in list(claims):
                    for child in normalize_claim(claim, versions):
                        if child["claim_id"] not in known_ids:
                            known_ids.add(child["claim_id"])
                            claims.append(child)
                            added += 1
                known_ids = {claim["claim_id"] for claim in claims}
                page_title = document.get("page_title", "")
                for claim in public_prose_drop_claims(
                    document["text"], page_title, row["chunk_id"],
                    subjects_by_page.get(page_title.casefold(), []),
                ) if page_title else ():
                    if claim["claim_id"] not in known_ids:
                        known_ids.add(claim["claim_id"])
                        claims.append(claim)
                        added += 1
            output_rows.append({"chunk_id": row["chunk_id"], "claims": claims})
        cache_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in output_rows)
        )
        summary[index_dir.name] = {"chunks": len(output_rows), "claims_added": added}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        {
            "normalizer_version": normalizer_version(),
            "indexes": normalize_cache_tree(
                args.index_root, args.cache_root, rebuild=args.rebuild
            ),
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
