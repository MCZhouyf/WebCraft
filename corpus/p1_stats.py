"""Produce P1 coverage, prevalence, and source-composition CSV tables."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from corpus.generate_pollution import VERSIONS, drop_truth, task_conflicts
from corpus.gold_claims import NameResolver


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def source_key(source: dict[str, Any]) -> tuple[str, str, int]:
    return (source["site"], source["page_title"], int(source["revid"]))


def claim_signature(claim: dict[str, Any]) -> str:
    body = {
        "claim_type": claim["claim_type"],
        "subject_item": claim["subject_item"],
        "payload": claim["payload"],
        "polarity": claim["polarity"],
        "tense": claim["tense"],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def manifest_records(path: Path, key: str | None = None) -> dict[str, dict[str, Any]]:
    value = json.loads(path.read_text())
    if key is None:
        return value["snapshots"]
    if "snapshots" in value:
        return value["snapshots"][key]
    return value[key]


def page_lookup(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {title.casefold(): metadata for title, metadata in records.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/corpus_stats")
    parser.add_argument(
        "--extra-titles",
        type=Path,
        default=ROOT / "reports/mcwiki_extra_titles_needed.txt",
    )
    args = parser.parse_args()
    conflicts = task_conflicts(ROOT / "data/tasks.jsonl", ROOT / "data/conflicts.jsonl")
    truth = {
        version: json.loads((ROOT / f"truth/{version}.json").read_text())
        for version in VERSIONS
    }
    labels = {
        "T1": ROOT / "corpus/private/claims_gold_t1.jsonl",
        "T2": ROOT / "corpus/private/claims_gold_t2.jsonl",
        "T3": ROOT / "corpus/private/claims_gold_t3.jsonl",
        "current": ROOT / "corpus/private/claims_gold_current.jsonl",
        "Fandom": ROOT / "corpus/private/claims_gold_provisional_2023.jsonl",
    }
    evidence: list[dict[str, Any]] = []
    seen = set()
    for label, path in labels.items():
        for claim in read_jsonl(path):
            if claim["tense"] != "current" or claim["claim_type"] not in {
                "recipe",
                "smelting",
            }:
                continue
            identity = (source_key(claim["source"]), claim_signature(claim))
            if identity in seen:
                continue
            seen.add(identity)
            evidence.append(
                {
                    "source": label,
                    "kind": "recipe",
                    "subject": claim["subject_item"],
                    "truth_vector": claim["truth_vector"],
                    "source_kind": "template_gold",
                }
            )

    mixed_docs = read_jsonl(ROOT / "corpus/indexes/formal_p1/mixed/documents.jsonl")
    docs = {row["chunk_id"]: row for row in mixed_docs}
    source_labels = {}
    history = json.loads(
        (ROOT / "corpus/manifests/fandom_history_snapshots.json").read_text()
    )["snapshots"]
    records_by_label = {
        "T1": history["1.16.5"],
        "T2": history["1.19"],
        "T3": manifest_records(
            ROOT / "corpus/manifests/mcwiki_export_snapshots.json", "t3"
        ),
        "current": manifest_records(
            ROOT / "corpus/manifests/mcwiki_export_snapshots.json", "current"
        ),
        "Fandom": manifest_records(
            ROOT / "corpus/manifests/fandom_2023_snapshot.json"
        ),
    }
    for label, records in records_by_label.items():
        for title, metadata in records.items():
            source_labels.setdefault(
                (metadata["source_site"], title, int(metadata["revid"])), label
            )

    cache_rows = read_jsonl(
        ROOT / "corpus/claims_cache/formal_p1/mixed/chunk_claims.jsonl"
    )
    for row in cache_rows:
        document = docs[row["chunk_id"]]
        revision = (
            document["source_site"],
            document["page_title"],
            int(document["revid"]),
        )
        label = source_labels.get(revision, "unknown")
        for claim in row["claims"]:
            if claim["claim_type"] != "drop":
                continue
            identity = (revision, claim_signature(claim))
            if identity in seen:
                continue
            seen.add(identity)
            vector = {
                version: drop_truth(claim, truth[version]) for version in VERSIONS
            }
            evidence.append(
                {
                    "source": label,
                    "kind": "drop",
                    "subject": claim["subject_item"],
                    "truth_vector": vector,
                    "source_kind": "llm_extraction",
                }
            )

    manifest = json.loads((ROOT / "corpus/manifests/p1.json").read_text())
    title_by_item = {row["item_id"]: row["candidate_title"] for row in manifest["items"]}
    resolver = NameResolver()
    source_pages = {
        label: page_lookup(records) for label, records in records_by_label.items()
    }
    coverage_rows = []
    extra_titles = set()
    for conflict in conflicts:
        assertion = conflict["assertion"]
        subject = assertion.get("output") or assertion["block"]
        kind = assertion["kind"]
        relevant = [
            row for row in evidence if row["subject"] == subject and row["kind"] == kind
        ]
        old = [
            row
            for row in relevant
            if row["truth_vector"].get("1.16.5") is True
            and row["truth_vector"].get("1.21.11") is False
        ]
        new = [
            row
            for row in relevant
            if row["truth_vector"].get("1.16.5") is False
            and row["truth_vector"].get("1.21.11") is True
        ]
        present = int(bool(old)) + int(bool(new))
        status = "two_sided" if present == 2 else "one_sided" if present == 1 else "uncovered"
        missing = []
        reasons = []
        title = title_by_item.get(subject, subject.replace("_", " ").title())
        for side, rows, endpoint, label in (
            ("old", old, "1.16.5", "T1"),
            ("new", new, "1.21.11", "T3"),
        ):
            if rows:
                continue
            missing.append(side)
            registry = set(truth[endpoint]["names"]["items"])
            if f"minecraft:{subject}" not in registry:
                reason = "(a)"
            elif title.casefold() not in source_pages[label]:
                reason = "(d)"
                if side == "new":
                    extra_titles.add(title)
            else:
                metadata = source_pages[label][title.casefold()]
                raw_root = (
                    ROOT / "corpus/raw/fandom_history"
                    if label == "T1"
                    else ROOT / "corpus/raw/mcwiki_manual_export"
                )
                raw_text = (raw_root / metadata["wikitext"]).read_text()
                redirect = re.match(
                    r"\s*#redirect\s*\[\[([^]#|]+)", raw_text, re.I
                )
                if redirect and redirect.group(1).strip().casefold() not in source_pages[label]:
                    reason = "(d)"
                    if side == "new":
                        extra_titles.add(redirect.group(1).strip())
                    reasons.append(f"{side}:{reason}")
                    continue
                text = raw_text.casefold()
                has_template = "{{crafting" in text or "{{smelting" in text
                has_prose = any(
                    word in text
                    for word in (
                        ("drop", "mined")
                        if kind == "drop"
                        else ("craft", "recipe")
                    )
                )
                if relevant and any(row["source_kind"] == "llm_extraction" for row in relevant):
                    reason = "(f)"
                elif has_template:
                    reason = "(b)"
                elif has_prose:
                    reason = "(c)"
                else:
                    reason = "(a)"
            reasons.append(f"{side}:{reason}")
        coverage_rows.append(
            {
                "conflict_id": conflict["id"],
                "mechanism_type": "/".join(
                    value
                    for value in (conflict["main_type"], conflict.get("subtype"))
                    if value
                ),
                "assertion_kind": kind,
                "subject_item": subject,
                "template_old_count": sum(r["source_kind"] == "template_gold" for r in old),
                "template_new_count": sum(r["source_kind"] == "template_gold" for r in new),
                "llm_old_count": sum(r["source_kind"] == "llm_extraction" for r in old),
                "llm_new_count": sum(r["source_kind"] == "llm_extraction" for r in new),
                "mixed_old_count": len(old),
                "mixed_new_count": len(new),
                "coverage": status,
                "missing_side": ";".join(missing),
                "missing_reason_code": ";".join(reasons),
            }
        )

    coverage_fields = list(coverage_rows[0])
    write_csv(args.output_dir / "coverage_p1.csv", coverage_rows, coverage_fields)
    write_csv(ROOT / "reports/coverage_p1.csv", coverage_rows, coverage_fields)
    args.extra_titles.write_text("".join(title + "\n" for title in sorted(extra_titles)))

    prevalence_rows = []
    synthetic = read_jsonl(ROOT / "corpus/private/pollution/instances.jsonl")
    for condition in ("mixed", "polluted"):
        for kind in ("recipe", "drop"):
            relevant = [row for row in evidence if row["kind"] == kind]
            old = sum(
                row["truth_vector"].get("1.16.5") is True
                and row["truth_vector"].get("1.21.11") is False
                for row in relevant
            )
            new = sum(
                row["truth_vector"].get("1.16.5") is False
                and row["truth_vector"].get("1.21.11") is True
                for row in relevant
            )
            false = (
                sum(row["core_claim"]["claim_type"] == kind for row in synthetic)
                if condition == "polluted"
                else 0
            )
            prevalence_rows.append(
                {
                    "condition": condition,
                    "claim_type": kind,
                    "old_claims": old,
                    "new_claims": new,
                    "old_to_new_ratio": old / new if new else "",
                    "synthetic_all_versions_false": false,
                }
            )
    write_csv(
        args.output_dir / "prevalence_by_type.csv",
        prevalence_rows,
        list(prevalence_rows[0]),
    )

    composition_rows = []
    synthetic_keys = {
        (
            "minecraft_wiki",
            row["page_title"],
            int(__import__("hashlib").sha256(row["instance_id"].encode()).hexdigest()[:8], 16),
        )
        for row in synthetic
    }
    for index_dir in sorted((ROOT / "corpus/indexes/formal_p1").iterdir()):
        documents = read_jsonl(index_dir / "documents.jsonl")
        pages: dict[tuple[str, str, int], str] = {}
        chunks = Counter()
        fixed_source = {
            "matched_1.16.5": "T1",
            "mismatched_old2new": "T1",
            "matched_1.19": "T2",
            "matched_1.21.11": "T3",
            "mismatched_new2old": "T3",
        }.get(index_dir.name)
        for row in documents:
            key = (row["source_site"], row["page_title"], int(row["revid"]))
            label = (
                "synthetic"
                if key in synthetic_keys
                else fixed_source or source_labels.get(key, "real_other")
            )
            pages[key] = label
            chunks[label] += 1
        page_counts = Counter(pages.values())
        for label in sorted(set(page_counts) | set(chunks)):
            composition_rows.append(
                {
                    "condition": index_dir.name,
                    "source": label,
                    "pages": page_counts[label],
                    "chunks": chunks[label],
                }
            )
    write_csv(
        args.output_dir / "source_composition.csv",
        composition_rows,
        list(composition_rows[0]),
    )
    statuses = Counter(row["coverage"] for row in coverage_rows)
    print(
        json.dumps(
            {
                "conflicts": len(coverage_rows),
                "coverage": dict(statuses),
                "extra_titles": sorted(extra_titles),
                "evidence_claims": len(evidence),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
