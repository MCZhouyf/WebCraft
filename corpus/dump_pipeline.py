"""Stream MediaWiki XML dumps into dated, item-scoped corpus snapshots."""
from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

import zstandard


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
# WikiTeam used zstd --long=31 for this dump (2 GiB decode window).
ZSTD_MAX_WINDOW = 2**31

TITLE_OVERRIDES = {
    "coal_block": "Block of Coal",
    "diamond_block": "Block of Diamond",
    "gold_block": "Block of Gold",
    "iron_block": "Block of Iron",
    "lapis_block": "Block of Lapis Lazuli",
    "raw_gold_block": "Block of Raw Gold",
    "raw_iron_block": "Block of Raw Iron",
    "redstone_block": "Block of Redstone",
    "chest_minecart": "Minecart with Chest",
    "furnace_minecart": "Minecart with Furnace",
    "hopper_minecart": "Minecart with Hopper",
    "oak_chest_boat": "Boat with Chest",
    "oak_wood": "Wood",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if local_name(child.tag) == name:
            return child.text
    return None


def descendant_text(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if local_name(child.tag) == name:
            return child.text
    return None


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").strip()).casefold()


@dataclass
class Revision:
    revid: int
    timestamp: str
    text: str


@dataclass
class Page:
    title: str
    namespace: int
    redirect: str | None
    revisions: list[Revision]


def iter_pages(stream: BinaryIO) -> Iterator[Page]:
    for _, element in ET.iterparse(stream, events=("end",)):
        if local_name(element.tag) != "page":
            continue
        title = child_text(element, "title") or ""
        namespace = int(child_text(element, "ns") or -1)
        redirect = None
        revisions: list[Revision] = []
        for child in element:
            name = local_name(child.tag)
            if name == "redirect":
                redirect = child.attrib.get("title")
            elif name == "revision":
                raw_id = child_text(child, "id")
                timestamp = child_text(child, "timestamp")
                text = descendant_text(child, "text")
                if raw_id and timestamp:
                    revisions.append(Revision(int(raw_id), timestamp, text or ""))
        yield Page(title, namespace, redirect, revisions)
        element.clear()


def open_zst(path: Path):
    compressed = path.open("rb")
    reader = zstandard.ZstdDecompressor(
        max_window_size=ZSTD_MAX_WINDOW
    ).stream_reader(compressed)
    return compressed, reader


def scan_catalog(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    titles: dict[str, str] = {}
    redirects: dict[str, str] = {}
    compressed, reader = open_zst(path)
    try:
        for page in iter_pages(reader):
            if page.namespace != 0:
                continue
            titles[normalize_title(page.title)] = page.title
            if page.redirect:
                redirects[normalize_title(page.title)] = page.redirect
    finally:
        reader.close()
        compressed.close()
    return titles, redirects


def resolve_items(
    manifest: dict, titles: dict[str, str], redirects: dict[str, str]
) -> list[dict[str, str | bool | None]]:
    resolved = []
    for row in manifest["items"]:
        item_id = row["item_id"]
        candidate = TITLE_OVERRIDES.get(item_id, row["candidate_title"])
        actual = titles.get(normalize_title(candidate))
        redirect_from = None
        if actual and normalize_title(actual) in redirects:
            redirect_from = actual
            target = redirects[normalize_title(actual)]
            actual = titles.get(normalize_title(target), target)
        resolved.append(
            {
                "item_id": item_id,
                "candidate_title": candidate,
                "page_title": actual,
                "verified_in_dump": actual is not None,
                "redirect_from": redirect_from,
            }
        )
    return resolved


def extract_snapshots(
    dump_path: Path,
    page_titles: set[str],
    cutoffs: dict[str, datetime],
    output_dir: Path,
) -> dict[str, dict[str, dict]]:
    wanted = {normalize_title(title): title for title in page_titles}
    best: dict[str, dict[str, Revision]] = {label: {} for label in cutoffs}
    compressed, reader = open_zst(dump_path)
    try:
        for page in iter_pages(reader):
            key = normalize_title(page.title)
            if page.namespace != 0 or key not in wanted:
                continue
            canonical_title = wanted[key]
            for label, cutoff in cutoffs.items():
                eligible = [r for r in page.revisions if parse_timestamp(r.timestamp) <= cutoff]
                if not eligible:
                    continue
                revision = max(eligible, key=lambda r: parse_timestamp(r.timestamp))
                previous = best[label].get(canonical_title)
                if previous is None or parse_timestamp(revision.timestamp) > parse_timestamp(
                    previous.timestamp
                ):
                    best[label][canonical_title] = revision
    finally:
        reader.close()
        compressed.close()
    records: dict[str, dict[str, dict]] = {label: {} for label in cutoffs}
    for label, pages in best.items():
        for page_title, revision in pages.items():
            relative = Path(label) / f"{revision.revid}.wikitext"
            destination = output_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(revision.text, encoding="utf-8")
            records[label][page_title] = {
                "page_title": page_title,
                "revid": revision.revid,
                "timestamp": revision.timestamp,
                "wikitext": str(relative),
                "source_site": "minecraft_fandom_via_internet_archive",
            }
    return records


def write_mapping_reports(
    mapping: list[dict[str, str | bool | None]],
    output_json: Path,
    export_titles: Path,
    unmapped_csv: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    titles = sorted(
        {str(row["page_title"] or row["candidate_title"]) for row in mapping},
        key=str.casefold,
    )
    export_titles.write_text("\n".join(titles) + "\n", encoding="utf-8")
    with unmapped_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("item_id", "candidate_page_title", "status", "reason"))
        for row in mapping:
            if row["verified_in_dump"]:
                continue
            writer.writerow(
                (
                    row["item_id"],
                    row["candidate_title"],
                    "needs_current_export_validation",
                    "Title absent from the 2023-08-22 Fandom history dump",
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--export-titles", type=Path, required=True)
    parser.add_argument("--unmapped-report", type=Path, required=True)
    parser.add_argument("--snapshots-dir", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--reuse-mapping", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.reuse_mapping:
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        catalog_count = None
    else:
        titles, redirects = scan_catalog(args.dump)
        mapping = resolve_items(manifest, titles, redirects)
        catalog_count = len(titles)
    write_mapping_reports(
        mapping, args.mapping, args.export_titles, args.unmapped_report
    )
    verified_titles = {
        str(row["page_title"]) for row in mapping if row["verified_in_dump"]
    }
    release_times = json.loads((ROOT / "corpus/release_times.json").read_text())
    cutoffs = {
        version: parse_timestamp(data["corpus_cutoff"])
        for version, data in release_times.items()
        if version in {"1.16.5", "1.19"}
    }
    records = extract_snapshots(args.dump, verified_titles, cutoffs, args.snapshots_dir)
    payload = {
        "dump": args.dump.name,
        "dump_cutoff": "2023-08-22",
        "source": "Internet Archive WikiTeam3",
        "snapshots": records,
    }
    args.snapshot_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot_manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "catalog_titles": catalog_count,
                "mapped_items": sum(bool(row["verified_in_dump"]) for row in mapping),
                "unmapped_items": sum(not row["verified_in_dump"] for row in mapping),
                "export_titles": len(
                    {str(row["page_title"] or row["candidate_title"]) for row in mapping}
                ),
                "snapshots": {key: len(value) for key, value in records.items()},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
