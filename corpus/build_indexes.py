"""Render local Wiki snapshots, chunk them, and build portable BM25 indexes."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from corpus.gold_claims import iter_templates, normalize_display, parse_template
from corpus.search_backend import tokenize
from corpus.block_templates import is_mechanism_template


CONTROL_KEYS = {
    "type", "head", "foot", "showdescription", "description", "nocat", "group", "name"
}


def render_template(body: str) -> str:
    name, positional, named = parse_template(body)
    if name == "crafting":
        output = normalize_display(named.get("output", ""))
        ingredients = [
            normalize_display(value)
            for key, value in named.items()
            if re.fullmatch(r"[abc][123]", key) and normalize_display(value)
        ] or [normalize_display(value) for value in positional if normalize_display(value)]
        return f"Crafting recipe for {output}: " + ", ".join(ingredients) if output else ""
    if name == "smelting":
        values = [normalize_display(value) for value in positional if normalize_display(value)]
        return f"Smelting recipe for {values[1]}: inputs {values[0]}" if len(values) >= 2 else ""
    if name == "lootchestitem" and positional:
        return f"Chest loot includes {normalize_display(positional[0])}."
    if name == "history":
        values = [normalize_display(value) for value in positional]
        values.extend(
            normalize_display(value) for key, value in named.items() if key not in CONTROL_KEYS
        )
        return "Historical claim: " + " ".join(value for value in values if value)
    return ""


def wikitext_to_text(value: str) -> str:
    replacements = []
    for offset, body in iter_templates(value):
        replacements.append((offset, offset + len(body) + 4, render_template(body)))
    for start, end, rendered in reversed(replacements):
        value = value[:start] + rendered + value[end:]
    value = re.sub(r"\{\|.*?\|\}", " ", value, flags=re.S)
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\{\{[^{}]*\}\}", " ", value)
    value = re.sub(r"\[\[(?:File|Image|Category):[^]]+\]\]", " ", value, flags=re.I)
    value = re.sub(r"\[\[[^]|]+\|([^]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^]]+)\]\]", r"\1", value)
    value = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"^=+\s*(.*?)\s*=+\s*$", r"\1.", value, flags=re.M)
    value = value.replace("'''", "").replace("''", "")
    value = html.unescape(value)
    value = re.sub(r"\b(?:truth_vector|synthetic)\b", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def chunks(text: str, size: int = 300, overlap: int = 40) -> list[str]:
    words = text.split()
    if not words:
        return []
    output = []
    step = size - overlap
    for start in range(0, len(words), step):
        output.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
    return output


def load_snapshot(manifest: dict, raw_root: Path, label: str | None = None) -> list[dict]:
    pages = manifest["snapshots"][label] if label else manifest["snapshots"]
    rows = []
    for title, metadata in pages.items():
        rows.append(
            {
                "text": (raw_root / metadata["wikitext"]).read_text(),
                "source_site": metadata["source_site"],
                "page_title": title,
                "revid": metadata["revid"],
                "timestamp": metadata["timestamp"],
            }
        )
    return rows


def load_records(records: dict, raw_root: Path) -> list[dict]:
    rows = []
    for title, metadata in records.items():
        rows.append(
            {
                "text": (raw_root / metadata["wikitext"]).read_text(),
                "source_site": metadata["source_site"],
                "page_title": title,
                "revid": metadata["revid"],
                "timestamp": metadata["timestamp"],
            }
        )
    return rows


def load_pollution(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        private = json.loads(line)
        identity = private["instance_id"]
        rows.append(
            {
                "text": private["page_text"],
                "source_site": "minecraft_wiki",
                "page_title": private["page_title"],
                "revid": int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16),
                "timestamp": private["timestamp"],
            }
        )
    return rows


def unique_sources(sources: list[dict]) -> list[dict]:
    unique = {}
    for row in sources:
        key = (row["source_site"], row["page_title"], row["revid"])
        unique[key] = row
    return list(unique.values())


def build_index(output: Path, sources: list[dict], accepted: list[str], metadata: dict) -> dict:
    documents = []
    for source in sources:
        from corpus.redirect_audit import redirect_target

        if redirect_target(source["text"]):
            raise ValueError(f'unresolved redirect cannot enter an index: {source["page_title"]}')
        rendered = wikitext_to_text(source["text"])
        text_chunks = [(str(index), text) for index, text in enumerate(chunks(rendered))]
        # Keep infobox source spans intact in dedicated chunks. Existing prose
        # chunks retain their identities for cross-generation extraction reuse.
        for offset, body in iter_templates(source["text"]):
            name, _, _ = parse_template(body)
            if is_mechanism_template(name):
                text_chunks.append((f"template:{offset}", source["text"][offset:offset + len(body) + 4]))
        for index, text in text_chunks:
            identity = f'{source["source_site"]}|{source["revid"]}|{index}'
            documents.append(
                {
                    "text": text,
                    "source_site": source["source_site"],
                    "page_title": source["page_title"],
                    "revid": source["revid"],
                    "timestamp": source["timestamp"],
                    "chunk_id": hashlib.sha256(identity.encode()).hexdigest()[:20],
                }
            )
    term_sets = [set(tokenize(row["text"])) for row in documents]
    document_frequency = Counter(term for terms in term_sets for term in terms)
    lengths = [len(tokenize(row["text"])) for row in documents]
    output.mkdir(parents=True, exist_ok=True)
    (output / "documents.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in documents)
    )
    (output / "bm25.json").write_text(
        json.dumps(
            {
                "average_document_length": sum(lengths) / len(lengths),
                "document_frequency": dict(document_frequency),
            },
            sort_keys=True,
        )
        + "\n"
    )
    config = {
        "accepted_conditions": accepted,
        "documents": len(documents),
        "pages": len(sources),
        **metadata,
    }
    (output / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    return {"documents": len(documents), "pages": len(sources)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-manifest", type=Path, required=True)
    parser.add_argument("--provisional-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mcwiki-manifest", type=Path)
    parser.add_argument("--mcwiki-raw-root", type=Path)
    parser.add_argument("--resolved-manifest", type=Path)
    parser.add_argument("--resolved-raw-root", type=Path)
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--pollution-instances", type=Path)
    args = parser.parse_args()
    history = json.loads(args.history_manifest.read_text())
    provisional_manifest = json.loads(args.provisional_manifest.read_text())
    t1 = load_snapshot(history, args.raw_root, "1.16.5")
    t2 = load_snapshot(history, args.raw_root, "1.19")
    p3 = load_snapshot(provisional_manifest, args.raw_root)
    if args.formal:
        if not args.mcwiki_manifest or not args.mcwiki_raw_root:
            parser.error("--formal requires --mcwiki-manifest and --mcwiki-raw-root")
        if args.resolved_manifest:
            if not args.resolved_raw_root:
                parser.error("--resolved-manifest requires --resolved-raw-root")
            resolved = json.loads(args.resolved_manifest.read_text())
            t1 = load_records(resolved["T1"], args.resolved_raw_root)
            t2 = load_records(resolved["T2"], args.resolved_raw_root)
            p3 = load_records(resolved["Fandom2023"], args.resolved_raw_root)
            t3 = load_records(resolved["T3"], args.resolved_raw_root)
            current = load_records(resolved["current"], args.resolved_raw_root)
        else:
            minecraft = json.loads(args.mcwiki_manifest.read_text())
            t3 = load_records(minecraft["t3"], args.mcwiki_raw_root)
            current = load_records(minecraft["current"], args.mcwiki_raw_root)
        definitions = {
            "matched_1.16.5": (t1, ["matched", "matched_1.16.5"]),
            "matched_1.19": (t2, ["matched", "matched_1.19"]),
            "matched_1.21.11": (t3, ["matched", "matched_1.21.11"]),
            "mismatched_old2new": (t1, ["mismatched_old2new"]),
            "mismatched_new2old": (t3, ["mismatched_new2old"]),
            "mixed": (unique_sources(t1 + t2 + t3 + current + p3), ["mixed"]),
        }
        if args.pollution_instances:
            mixed = definitions["mixed"][0]
            definitions["polluted"] = (
                mixed + load_pollution(args.pollution_instances),
                ["polluted"],
            )
        index_metadata = {"provisional": False, "condition_name": None}
    else:
        definitions = {
            "matched_1.16.5": (t1, ["matched", "matched_1.16.5"]),
            "matched_1.19": (t2, ["matched", "matched_1.19"]),
            "mismatched_old2new": (t1, ["mismatched_old2new"]),
            "mismatched_new2old": (p3, ["mismatched_new2old"]),
            "mixed": (t1 + t2 + p3, ["mixed"]),
        }
        index_metadata = {"provisional": True, "t3_source": "fandom_2023", "condition_name": None}
    summary = {}
    for name, (sources, accepted) in definitions.items():
        summary[name] = build_index(
            args.output_root / name,
            sources,
            accepted,
            {**index_metadata, "condition_name": name},
        )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
