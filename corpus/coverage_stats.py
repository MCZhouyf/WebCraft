"""Compute provisional assertion coverage and old/new prevalence statistics."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from corpus.gold_claims import (
    NameResolver,
    VERSIONS,
    extract_gold,
    multiset_options_match,
    normalized_truth_recipes,
    trim_layout,
    write_jsonl,
)


def conflict_matches_claim(assertion: dict[str, Any], claim: dict[str, Any]) -> bool:
    if assertion.get("kind") != "recipe" or claim["claim_type"] not in {"recipe", "smelting"}:
        return False
    payload = claim["payload"]
    if (
        claim["subject_item"] != assertion.get("output")
        or payload.get("output_count") != assertion.get("count", 1)
        or payload.get("station") != assertion.get("station")
    ):
        return False
    if assertion.get("ordered"):
        if payload.get("layout") is None:
            return False
        expected = []
        grid = assertion.get("grid", [])
        width = max((len(row) for row in grid), default=0)
        for row in grid:
            expected.append([set(cell) if cell else None for cell in row] + [None] * (width - len(row)))
        source_layout = payload.get("layout_options") or [
            [item] if item is not None else None for item in payload["layout"]
        ]
        actual = trim_layout(source_layout)
        if len(actual) != len(expected) or any(len(a) != len(e) for a, e in zip(actual, expected)):
            return False
        def matches(candidate):
            return all(
                (not value)
                if options is None
                else bool(value)
                and (
                    set(value) == set(options)
                    if len(value) > 1
                    else value[0] in options
                )
                for actual_row, expected_row in zip(actual, candidate)
                for value, options in zip(actual_row, expected_row)
            )

        return matches(expected) or matches([list(reversed(row)) for row in expected])
    if payload.get("layout") is not None:
        return False
    options = [set(slot) for slot in assertion.get("ingredients", [])]
    return multiset_options_match(payload.get("ingredients", []), options)


def provisional_claims(manifest: dict, raw_root: Path) -> list[dict]:
    resolver = NameResolver()
    recipes = {version: normalized_truth_recipes(version) for version in VERSIONS}
    claims = []
    for title, metadata in manifest["snapshots"].items():
        source = {
            "site": metadata["source_site"],
            "page_title": title,
            "revid": metadata["revid"],
            "timestamp": metadata["timestamp"],
            "timepoint": "fandom_2023",
        }
        text = (raw_root / metadata["wikitext"]).read_text()
        claims.extend(extract_gold(text, source, resolver, recipes))
    return claims


def record_claims(records: dict, raw_root: Path, timepoint: str) -> list[dict]:
    resolver = NameResolver()
    recipes = {version: normalized_truth_recipes(version) for version in VERSIONS}
    claims = []
    for title, metadata in records.items():
        source = {
            "site": metadata["source_site"],
            "page_title": title,
            "revid": metadata["revid"],
            "timestamp": metadata["timestamp"],
            "timepoint": timepoint,
        }
        text = (raw_root / metadata["wikitext"]).read_text()
        claims.extend(extract_gold(text, source, resolver, recipes))
    return claims


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--conflicts", type=Path, required=True)
    parser.add_argument("--gold-dir", type=Path, required=True)
    parser.add_argument("--provisional-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mcwiki-manifest", type=Path)
    parser.add_argument("--mcwiki-raw-root", type=Path)
    parser.add_argument("--formal", action="store_true")
    args = parser.parse_args()
    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines()]
    conflicts = {row["id"]: row for row in map(json.loads, args.conflicts.read_text().splitlines())}
    selected = sorted(
        {
            conflict_id
            for task in tasks
            for conflict_id in task["conflict_ids"]
            if conflicts[conflict_id]["grade"] == "A"
        }
    )
    dev_ids = {
        conflict_id
        for task in tasks
        if task["split"] == "dev"
        for conflict_id in task["conflict_ids"]
        if conflicts[conflict_id]["grade"] == "A"
    }
    claims = {
        "t1": [json.loads(line) for line in (args.gold_dir / "claims_gold_t1.jsonl").read_text().splitlines()],
        "t2": [json.loads(line) for line in (args.gold_dir / "claims_gold_t2.jsonl").read_text().splitlines()],
    }
    p3_manifest = json.loads(args.provisional_manifest.read_text())
    claims["provisional_2023"] = provisional_claims(p3_manifest, args.raw_root)
    write_jsonl(args.gold_dir / "claims_gold_provisional_2023.jsonl", claims["provisional_2023"])
    if args.formal:
        if not args.mcwiki_manifest or not args.mcwiki_raw_root:
            parser.error("--formal requires --mcwiki-manifest and --mcwiki-raw-root")
        minecraft = json.loads(args.mcwiki_manifest.read_text())
        claims["t3"] = record_claims(minecraft["t3"], args.mcwiki_raw_root, "1.21.11")
        claims["current"] = record_claims(minecraft["current"], args.mcwiki_raw_root, "current")
        write_jsonl(args.gold_dir / "claims_gold_t3.jsonl", claims["t3"])
        write_jsonl(args.gold_dir / "claims_gold_current.jsonl", claims["current"])
    rows = []
    for conflict_id in selected:
        conflict = conflicts[conflict_id]
        counts = {
            label: sum(
                claim["tense"] == "current"
                and conflict_matches_claim(conflict["assertion"], claim)
                for claim in values
            )
            for label, values in claims.items()
        }
        rows.append(
            {
                "conflict_id": conflict_id,
                "scope": "dev" if conflict_id in dev_ids else "eval",
                "main_type": conflict["main_type"],
                "assertion_kind": conflict["assertion"]["kind"],
                "truth_vector": conflict["truth_vector"],
                **{f"{label}_count": value for label, value in counts.items()},
                "old_new_proxy_covered": counts["t1"] > 0
                and counts["t3" if args.formal else "provisional_2023"] > 0,
            }
        )
    by_kind = {}
    for kind in sorted({row["assertion_kind"] for row in rows}):
        relevant = [row for row in rows if row["assertion_kind"] == kind]
        old = sum(row["t1_count"] for row in relevant)
        new_key = "t3_count" if args.formal else "provisional_2023_count"
        new = sum(row[new_key] for row in relevant)
        by_kind[kind] = {
            "assertions": len(relevant),
            "old_claims_t1": old,
            "new_claims_t3" if args.formal else "new_claims_provisional_2023": new,
            "old_to_new_ratio": old / new if new else None,
        }
    payload = {
        "status": "formal" if args.formal else "provisional",
        "definition": (
            "Exact structured assertion matches; T1 and T3 are old/new timestamp proxies."
            if args.formal
            else "Exact structured assertion matches; T1 and fandom_2023 are old/new timestamp proxies."
        ),
        "claims_by_snapshot": {label: len(values) for label, values in claims.items()},
        "assertions": len(rows),
        "dev_assertions": len(dev_ids),
        "old_new_proxy_covered": sum(row["old_new_proxy_covered"] for row in rows),
        "dev_old_new_proxy_covered": sum(
            row["old_new_proxy_covered"] for row in rows if row["scope"] == "dev"
        ),
        "by_kind": by_kind,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: payload[key] for key in (
        "assertions", "dev_assertions", "old_new_proxy_covered", "dev_old_new_proxy_covered", "by_kind"
    )}, sort_keys=True))


if __name__ == "__main__":
    main()
