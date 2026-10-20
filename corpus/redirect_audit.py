"""Resolve selected historical redirects without consulting present-day redirects."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from xml.parsers import expat
from pathlib import Path

from corpus.dump_pipeline import iter_pages, open_zst, parse_timestamp
from corpus.gold_claims import iter_templates

ROOT = Path(__file__).resolve().parents[1]
REDIRECT = re.compile(r"^\s*#redirect\s*:?\s*\[\[([^]|]+)", re.I)


def title_key(title: str) -> str:
    title = re.sub(r"\s+", " ", title.split("#", 1)[0].replace("_", " ")).strip()
    return title[:1].upper() + title[1:]


def redirect_target(text: str) -> str | None:
    text = re.sub(r"<!--.*?-->", "", text.lstrip("\ufeff"), flags=re.S)
    match = REDIRECT.match(text)
    return title_key(match.group(1)) if match else None


def body_length(text: str) -> int:
    for offset, body in reversed(list(iter_templates(text))):
        text = text[:offset] + text[offset + len(body) + 4:]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return len(text.strip())


def sources(root: Path) -> dict:
    manifest = root / "corpus/manifests"
    history = json.loads((manifest / "fandom_history_snapshots.json").read_text())
    fandom = json.loads((manifest / "fandom_2023_snapshot.json").read_text())
    wiki = json.loads((manifest / "mcwiki_export_snapshots.json").read_text())
    return {
        "T1": (history["snapshots"]["1.16.5"], root / "corpus/raw/fandom_history"),
        "T2": (history["snapshots"]["1.19"], root / "corpus/raw/fandom_history"),
        "Fandom2023": (fandom["snapshots"], root / "corpus/raw/fandom_history"),
        "T3": (wiki["t3"], root / "corpus/raw/mcwiki_manual_export"),
        "current": (wiki["current"], root / "corpus/raw/mcwiki_manual_export"),
    }


def save_catalog_revision(db, source, title, revision) -> None:
    db.execute(
        "INSERT INTO revisions VALUES (?,?,?,?,?) ON CONFLICT(source,title) DO UPDATE SET "
        "revid=excluded.revid,timestamp=excluded.timestamp,text=excluded.text "
        "WHERE excluded.timestamp > revisions.timestamp OR "
        "(excluded.timestamp = revisions.timestamp AND excluded.revid > revisions.revid)",
        (source, title_key(title), revision.revid, revision.timestamp, revision.text),
    )


def scan_selected_stream(reader, wanted, cutoffs):
    """SAX parsing avoids building trees for millions of irrelevant revisions."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack, captured, best = [], [], {}
    state = {"title": "", "ns": -1, "selected": False, "revision": {}, "capture": None}

    def start(name, attrs):
        name = name.rsplit("}", 1)[-1]
        stack.append(name)
        if name == "page":
            state.update(title="", ns=-1, selected=False)
        elif name == "revision":
            state["selected"] = state["ns"] == 0 and title_key(state["title"]) in wanted
            state["revision"] = {}
        capture = (name in {"title", "ns"} and len(stack) >= 2 and stack[-2] == "page") or (
            state["selected"] and name in {"id", "timestamp", "text"}
            and (stack[-2] == "revision" or name == "text"))
        if capture:
            state["capture"] = name
            captured.clear()
            parser.CharacterDataHandler = captured.append

    def end(name):
        name = name.rsplit("}", 1)[-1]
        if state["capture"] == name:
            value = "".join(captured)
            if name == "title":
                state["title"] = value
            elif name == "ns":
                state["ns"] = int(value)
            else:
                state["revision"][name] = value
            state["capture"] = None
            captured.clear()
            parser.CharacterDataHandler = None
        if name == "revision" and state["selected"]:
            revision = state["revision"]
            if revision.get("id") and revision.get("timestamp"):
                from corpus.dump_pipeline import Revision
                timestamp = parse_timestamp(revision["timestamp"])
                for label, cutoff in cutoffs.items():
                    key = (label, title_key(state["title"]))
                    previous = best.get(key)
                    if timestamp <= cutoff and (previous is None or (revision["timestamp"], int(revision["id"])) > (previous.timestamp, previous.revid)):
                        best[key] = Revision(int(revision["id"]), revision["timestamp"], revision.get("text", ""))
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    total = 0
    while block := reader.read(1024 * 1024):
        parser.Parse(block, False)
        total += len(block)
        if total % (512 * 1024 * 1024) == 0:
            print(json.dumps({"scanned_xml_mib": total // (1024 * 1024), "selected_records": len(best)}), flush=True)
    parser.Parse(b"", True)
    return best


def build_local_catalog(root: Path, destination: Path, refresh: bool = False) -> None:
    """One streaming pass preserves cutoff-selected main-namespace pages locally."""
    releases = json.loads((root / "corpus/release_times.json").read_text())
    cutoffs = {"T1": releases["1.16.5"]["corpus_cutoff"],
               "T2": releases["1.19"]["corpus_cutoff"],
               "Fandom2023": "2023-08-22T23:59:59+00:00"}
    cutoffs = {label: parse_timestamp(value) for label, value in cutoffs.items()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as db:
        db.execute("CREATE TABLE IF NOT EXISTS revisions (source TEXT, title TEXT, revid INTEGER, timestamp TEXT, text TEXT, PRIMARY KEY(source,title))")
        db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        if refresh:
            db.execute("DELETE FROM state")
            db.execute("DELETE FROM revisions")
            db.commit()
        if db.execute("SELECT value FROM state WHERE key='complete'").fetchone():
            return
        wanted = set()
        for records, raw in sources(root).values():
            for title, record in records.items():
                wanted.add(title_key(title))
                if target := redirect_target((raw / record["wikitext"]).read_text()):
                    wanted.add(target)
        mapping = json.loads((root / "corpus/manifests/page_map_p1.json").read_text())
        wanted.update(title_key(row["page_title"] or row["candidate_title"]) for row in mapping)
        dump = root / "corpus/raw/internet_archive/minecraft.fandom.com-20230822-history.xml.zst"
        scanned = set()
        while pending := wanted - scanned:
            compressed, reader = open_zst(dump)
            try:
                best = scan_selected_stream(reader, pending, cutoffs)
            finally:
                reader.close()
                compressed.close()
            for (source, title), revision in best.items():
                save_catalog_revision(db, source, title, revision)
                if target := redirect_target(revision.text):
                    wanted.add(target)
            scanned.update(pending)
            db.commit()
        db.execute("INSERT OR REPLACE INTO state VALUES ('scanned_titles',?)", (json.dumps(sorted(scanned)),))
        db.execute("INSERT OR REPLACE INTO state VALUES ('complete','true')")
        db.commit()


def resolve_chain(title: str, lookup) -> tuple[str, dict | None, str, list[str]]:
    chain = []
    while title_key(title) not in chain:
        title = title_key(title)
        chain.append(title)
        record = lookup(title)
        if record is None:
            return title, None, "target_missing", chain
        target = redirect_target(record["text"])
        if target is None:
            return title, record, "resolved" if body_length(record["text"]) >= 200 else "shell", chain
        title = target
    return title, None, "redirect_cycle", chain


def import_redirect_exports(root: Path, paths: list[Path]) -> dict:
    """Accept local author exports, with timestamp selection rather than History guesses."""
    from corpus.import_mcwiki_export import sha256

    cutoff = parse_timestamp(json.loads((root / "corpus/release_times.json").read_text())["1.21.11"]["corpus_cutoff"])
    best = {"T3": {}, "current": {}}
    for path in paths:
        with path.open("rb") as stream:
            for page in iter_pages(stream):
                if page.namespace != 0:
                    continue
                title = title_key(page.title)
                for label in best:
                    eligible = [r for r in page.revisions if label == "current" or parse_timestamp(r.timestamp) <= cutoff]
                    if not eligible:
                        continue
                    revision = max(eligible, key=lambda r: parse_timestamp(r.timestamp))
                    previous = best[label].get(title)
                    if previous and parse_timestamp(previous["timestamp"]) >= parse_timestamp(revision.timestamp):
                        continue
                    relative = Path(label) / f"{revision.revid}.wikitext"
                    destination = root / "corpus/raw/redirect_exports" / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(revision.text)
                    best[label][title] = {"page_title": page.title, "revid": revision.revid,
                        "timestamp": revision.timestamp, "wikitext": str(relative), "source_site": "minecraft_wiki"}
    manifest = {"exports": {p.name: sha256(p) for p in paths}, "snapshots": best}
    (root / "corpus/manifests/redirect_export_snapshots.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {label: len(records) for label, records in best.items()}


def validate_selected_pages(root: Path, labels=None) -> None:
    rows = list(csv.DictReader((root / "reports/redirect_audit.csv").open()))
    snapshots = json.loads((root / "corpus/manifests/redirect_resolved_snapshots.json").read_text())
    failures = []
    items = json.loads((root / "corpus/manifests/page_map_p1.json").read_text())
    expected = {(item["item_id"], label) for item in items
                for label in (labels or {"T1", "T2", "Fandom2023", "T3", "current"})}
    actual = [(row["item_id"], row["source"]) for row in rows if not labels or row["source"] in labels]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError("Unresolved selected pages: incomplete or duplicate item/source audit rows")
    for row in rows:
        if labels and row["source"] not in labels:
            continue
        if row["status"] == "absent_at_cutoff":
            continue
        record = snapshots[row["source"]].get(row["target_title"])
        if row["status"] != "resolved" or record is None:
            failures.append(f'{row["source"]}:{row["item_id"]}:{row["status"]}')
            continue
        text = (root / "corpus/raw/redirect_resolved" / record["wikitext"]).read_text()
        if redirect_target(text) or body_length(text) < 200:
            failures.append(f'{row["source"]}:{row["item_id"]}:invalid_body')
    if failures:
        raise ValueError("Unresolved selected pages: " + ", ".join(failures))


def audit(root: Path, catalog: Path | None = None) -> dict:
    mapping_path = root / "corpus/manifests/page_map_p1.json"
    mapping = json.loads(mapping_path.read_text())
    db = sqlite3.connect(catalog) if catalog and catalog.exists() else None
    if db and not db.execute("SELECT value FROM state WHERE key='complete'").fetchone():
        db.close()
        db = None
    rows, targets, resolved_records = [], set(), {}
    for label, (records, raw_root) in sources(root).items():
        available = {title_key(title): {**r, "text": (raw_root / r["wikitext"]).read_text()}
                     for title, r in records.items()}
        export_manifest = root / "corpus/manifests/redirect_export_snapshots.json"
        if label in {"T3", "current"} and export_manifest.exists():
            extra = json.loads(export_manifest.read_text())["snapshots"][label]
            available.update({title_key(title): {**r, "text": (root / "corpus/raw/redirect_exports" / r["wikitext"]).read_text()}
                              for title, r in extra.items()})
        def lookup(title):
            if title in available:
                return available[title]
            if db and label in {"T1", "T2", "Fandom2023"}:
                found = db.execute("SELECT revid,timestamp,text FROM revisions WHERE source=? AND title=?", (label,title)).fetchone()
                if found:
                    return {"page_title": title, "revid": found[0], "timestamp": found[1], "text": found[2],
                            "source_site": "minecraft_fandom_via_internet_archive"}
            return None
        resolved_records[label] = {}
        for item in mapping:
            original = item["page_title"] or item["candidate_title"]
            selected = lookup(title_key(original))
            resolved, record, status, chain = resolve_chain(original, lookup)
            if selected is None:
                # Absence is recorded separately from an unresolved redirect target.
                complete = db and db.execute("SELECT value FROM state WHERE key='complete'").fetchone()
                status = "absent_at_cutoff" if complete and label in {"T1", "T2", "Fandom2023"} else "no_selected_revision"
            if selected and status == "target_missing" and label in {"T3", "current"}:
                targets.add(resolved)
            row = {"item_id": item["item_id"], "source": label,
                   "selected_revid": selected["revid"] if selected else "",
                   "is_redirect": bool(selected and redirect_target(selected["text"])),
                   "target_title": resolved, "status": status,
                   "body_characters": body_length(selected["text"]) if selected else 0,
                   "resolved_revid": record["revid"] if record else "",
                   "redirect_chain": " -> ".join(chain)}
            rows.append(row)
            item.setdefault("resolved_title", {})[label] = resolved if record else None
            item.setdefault("resolution_status", {})[label] = status
            if record and status == "resolved":
                relative = Path(label) / f'{record["revid"]}.wikitext'
                output = root / "corpus/raw/redirect_resolved" / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(record["text"])
                resolved_records[label][resolved] = {k: v for k, v in record.items() if k != "text"}
                resolved_records[label][resolved]["wikitext"] = str(relative)
    if db:
        db.close()
    report = root / "reports/redirect_audit.csv"
    with report.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (root / "reports/mcwiki_redirect_targets_needed.txt").write_text("".join(t + "\n" for t in sorted(targets)))
    mapping_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    (root / "corpus/manifests/redirect_resolved_snapshots.json").write_text(json.dumps(resolved_records, indent=2, sort_keys=True) + "\n")
    from collections import Counter
    return {"rows": len(rows), "sources": {label: dict(Counter(r["status"] for r in rows if r["source"] == label)) for label in resolved_records},
            "requested_targets": sorted(targets)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--recover-local", action="store_true")
    parser.add_argument("--refresh-catalog", action="store_true")
    parser.add_argument("--export", type=Path, nargs="+")
    args = parser.parse_args()
    catalog = args.root / "corpus/raw/redirect_resolved/catalog.sqlite"
    if args.recover_local:
        build_local_catalog(args.root, catalog, args.refresh_catalog)
    if args.export:
        print(json.dumps({"imported": import_redirect_exports(args.root, args.export)}), flush=True)
    print(json.dumps(audit(args.root, catalog), indent=2), flush=True)


if __name__ == "__main__":
    main()
