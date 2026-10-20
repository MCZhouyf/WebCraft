"""Import author-downloaded Minecraft Wiki Special:Export XML files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from corpus.dump_pipeline import Page, iter_pages, parse_timestamp


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_requested(path: Path) -> list[str]:
    titles = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if len(titles) != len(set(titles)):
        raise ValueError("duplicate requested title")
    return titles


def read_exports(paths: list[Path], requested: set[str]) -> tuple[dict[str, Page], dict[str, str]]:
    pages: dict[str, Page] = {}
    hashes: dict[str, str] = {}
    for path in paths:
        hashes[path.name] = sha256(path)
        with path.open("rb") as stream:
            for page in iter_pages(stream):
                if page.namespace != 0 or page.title not in requested:
                    continue
                previous = pages.get(page.title)
                if previous is None:
                    pages[page.title] = page
                else:
                    previous.revisions.extend(page.revisions)
    return pages, hashes


def revision_record(page: Page, revision, relative: Path, source_kind: str) -> dict:
    return {
        "page_title": page.title,
        "revid": revision.revid,
        "timestamp": revision.timestamp,
        "wikitext": str(relative),
        "source_site": "minecraft_wiki",
        "source_kind": source_kind,
    }


def write_revision(root: Path, label: str, page: Page, revision, source_kind: str) -> dict:
    relative = Path(label) / f"{revision.revid}.wikitext"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(revision.text, encoding="utf-8")
    return revision_record(page, revision, relative, source_kind)


def import_exports(
    current_paths: list[Path],
    history_paths: list[Path],
    requested_titles: list[str],
    cutoff: str,
    raw_output: Path,
) -> tuple[dict, list[dict]]:
    requested = set(requested_titles)
    current_pages, current_hashes = read_exports(current_paths, requested)
    missing_current = sorted(requested - current_pages.keys(), key=str.casefold)
    if missing_current:
        raise ValueError(f"current export missing {len(missing_current)} requested titles")
    current_records = {}
    for title in requested_titles:
        page = current_pages[title]
        if len(page.revisions) != 1:
            raise ValueError(f"current export must contain one revision: {title}")
        current_records[title] = write_revision(
            raw_output, "current", page, page.revisions[0], "special_export_current"
        )

    history_pages, history_hashes = read_exports(history_paths, requested)
    cutoff_dt = parse_timestamp(cutoff)
    t3_records = {}
    manual_needed = []
    for title in requested_titles:
        current_page = current_pages[title]
        current = current_page.revisions[0]
        if parse_timestamp(current.timestamp) <= cutoff_dt:
            t3_records[title] = write_revision(
                raw_output, "t3", current_page, current, "special_export_current"
            )
            t3_records[title]["t3_equals_current"] = True
            continue
        history_page = history_pages.get(title)
        eligible = [
            revision
            for revision in (history_page.revisions if history_page else [])
            if parse_timestamp(revision.timestamp) <= cutoff_dt
        ]
        if eligible:
            revision = max(eligible, key=lambda row: parse_timestamp(row.timestamp))
            t3_records[title] = write_revision(
                raw_output, "t3", history_page, revision, "special_export_history"
            )
            t3_records[title]["t3_equals_current"] = revision.revid == current.revid
        else:
            manual_needed.append(
                {
                    "page_title": title,
                    "current_revid": current.revid,
                    "current_timestamp": current.timestamp,
                    "target_cutoff": cutoff,
                    "status": "needs_history_export_or_manual_oldid",
                }
            )
    manifest = {
        "cutoff": cutoff,
        "current_exports": current_hashes,
        "history_exports": history_hashes,
        "current": current_records,
        "t3": t3_records,
        "manual_needed_count": len(manual_needed),
    }
    return manifest, manual_needed


def write_manual_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "page_title",
        "current_revid",
        "current_timestamp",
        "target_cutoff",
        "status",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, nargs="+", required=True)
    parser.add_argument("--history", type=Path, nargs="*", default=[])
    parser.add_argument("--titles", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manual-report", type=Path, required=True)
    args = parser.parse_args()
    release_times = json.loads((ROOT / "corpus/release_times.json").read_text())
    cutoff = release_times["1.21.11"]["corpus_cutoff"]
    manifest, manual = import_exports(
        args.current,
        args.history,
        load_requested(args.titles),
        cutoff,
        args.raw_output,
    )
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    write_manual_csv(args.manual_report, manual)
    print(
        json.dumps(
            {
                "current": len(manifest["current"]),
                "t3": len(manifest["t3"]),
                "manual_needed": len(manual),
                "history_files": len(manifest["history_exports"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
