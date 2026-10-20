"""LLM claim extraction and deterministic evaluation against private gold claims."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from corpus.gold_claims import NameResolver, iter_templates, parse_template, trim_layout
from corpus.llm_client import BudgetedLLMClient


ROOT = Path(__file__).resolve().parents[1]
TARGET_TYPES = {"recipe", "smelting", "chest_loot"}
SYSTEM_PROMPT = """Extract claims from MediaWiki templates, returning JSON only as
{"claims":[...]}. Every claim has source_page_title copied exactly from its Page title
line, claim_type (recipe, smelting, or chest_loot), subject_item (canonical lowercase
snake_case ID), payload, polarity, and tense.

Process each explicit Crafting, Smelting, or LootChestItem template once and do not add
inverse recipes or world knowledge. Ignore Bedrock/Education-only templates. For Crafting:
subject_item is Output; payload has output_count, station="crafting_grid", ingredients,
and layout. Named A1/B1/C1/A2/B2/C2/A3/B3/C3 templates are shaped: layout must be exactly
nine row-major values/null, and ingredients must repeat an item once for every occupied
slot. Never deduplicate repeated ingredients. Positional Crafting ingredients are
shapeless, so layout=null and each positional ingredient is one entry. If an ingredient
is only an unresolved generic placeholder such as "Any Planks", "Any stone-tier block",
or "Matching ...", omit that template; never expand a placeholder using outside
knowledge.

For Smelting: the first positional parameter contains input alternatives and the second
is the output. subject_item must always be the output, never an input ore/tool. Emit one
claim per template and preserve all explicit semicolon-separated inputs in one ingredients
list. Do not expand brace placeholders such as {Any golden tools}; keep only explicitly
named canonical inputs. payload has output_count, station="furnace", ingredients, and
layout=null. For LootChestItem use claim_type="chest_loot", its item as subject_item, and
payload={}. Use polarity="positive" and tense="current" unless the template explicitly
says otherwise. No prose and no markdown."""

PROSE_SYSTEM_PROMPT = """Extract only explicit block-drop claims from the supplied
MediaWiki prose and return JSON only as {"claims":[...]}. Each claim must contain
source_label copied exactly, source_page_title copied exactly, claim_type="drop",
subject_item as a canonical lowercase snake_case ID and payload.drops as a JSON array
of canonical lowercase snake_case IDs (even when there is only one drop),
payload.silk_touch as true or false, polarity, tense (current or historical), and
source_span copied verbatim from the supplied text. Extract both current prose and
History statements when explicit. Do not infer an absent drop, use outside knowledge,
or paraphrase source_span. A sentence saying "instead of itself" supports the named
new drop but does not create a separate current claim that the block drops itself.
No markdown."""

CHUNK_SYSTEM_PROMPT = """Extract explicit factual claims independently from each
supplied text chunk. Return JSON only as
{"chunks":[{"source_key":"...","claims":[...]}]}. Return exactly one chunk object
for every source_key, including claims=[] when no supported claim is present.

Each claim must contain exactly: claim_type, subject_item, payload, polarity, tense,
span_text. Supported claim_type values are recipe, smelting, chest_loot, drop,
craftability, name, and id. subject_item must be a canonical lowercase snake_case item
or block ID. payload
must contain only the structured content explicitly stated in the span. For recipe and
smelting claims use ingredients (with repetitions), output_count, station, and layout
(nine row-major IDs/null for an explicit shaped recipe, otherwise null). For drop use
drops and silk_touch. For chest_loot use an empty object. For name or id use the
explicit old/new name or identifier fields.

span_text must be one exact contiguous substring copied from that chunk, without
paraphrase. Use polarity positive/negative and tense current/historical/future. Extract
only explicit claims; do not use outside knowledge, infer from absence, infer inverse
recipes, or combine facts across chunks. Treat each chunk in isolation. No markdown."""


def _json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response is not a JSON object")
    return value


def _canonical(value: Any, resolver: NameResolver) -> str | None:
    if not isinstance(value, str):
        return None
    direct = value.casefold().removeprefix("minecraft:")
    if direct in resolver.ids:
        return direct
    return resolver.resolve(value.replace("_", " "))


def normalize_claim(raw: Any, resolver: NameResolver) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("claim_type") not in {
        "recipe", "smelting", "chest_loot", "drop", "name", "id", "craftability"
    }:
        return None
    subject = _canonical(raw.get("subject_item"), resolver)
    if subject is None:
        return None
    claim_type = raw["claim_type"]
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    if claim_type in {"recipe", "smelting"}:
        ingredients = [
            item
            for value in payload.get("ingredients", [])
            if (item := _canonical(value, resolver)) is not None
        ]
        try:
            count = int(payload.get("output_count", 1))
        except (TypeError, ValueError):
            return None
        station = "furnace" if claim_type == "smelting" else "crafting_grid"
        layout = payload.get("layout")
        if isinstance(layout, list) and len(layout) == 9:
            layout = [_canonical(value, resolver) if value is not None else None for value in layout]
        else:
            layout = None
        if not ingredients:
            return None
        payload = {
            "ingredients": ingredients,
            "layout": layout,
            "output_count": count,
            "station": station,
        }
    elif claim_type == "drop":
        raw_drops = payload.get("drops", [])
        if isinstance(raw_drops, str):
            raw_drops = [raw_drops]
        drops = [
            item
            for value in raw_drops
            if (item := _canonical(value, resolver)) is not None
        ]
        if not drops or not isinstance(payload.get("silk_touch"), bool):
            return None
        payload = {"drops": drops, "silk_touch": payload["silk_touch"]}
    claim = {
        "claim_type": claim_type,
        "subject_item": subject,
        "payload": payload,
        "polarity": raw.get("polarity") if raw.get("polarity") in {"positive", "negative"} else "positive",
        "tense": raw.get("tense") if raw.get("tense") in {"current", "historical", "future"} else "current",
    }
    if isinstance(raw.get("source_page_title"), str):
        claim["source_page_title"] = raw["source_page_title"].strip()
    if isinstance(raw.get("source_label"), str):
        claim["source_label"] = raw["source_label"].strip()
    if isinstance(raw.get("source_span"), str):
        claim["source_span"] = raw["source_span"].strip()
    return claim


def extract_claims(
    text: str,
    client: BudgetedLLMClient | None = None,
    *,
    purpose: str = "claim_extraction",
    max_tokens: int = 1800,
) -> list[dict[str, Any]]:
    owned = client is None
    client = client or BudgetedLLMClient()
    try:
        content = client.complete(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            purpose=purpose,
        )
        resolver = NameResolver()
        values = _json_object(content).get("claims", [])
        return [claim for value in values if (claim := normalize_claim(value, resolver))]
    finally:
        if owned:
            client.close()


def extract_prose_claims(
    text: str,
    client: BudgetedLLMClient | None = None,
    *,
    purpose: str = "p0_prose_extraction",
    max_tokens: int = 2400,
) -> list[dict[str, Any]]:
    owned = client is None
    client = client or BudgetedLLMClient()
    try:
        content = client.complete(
            [
                {"role": "system", "content": PROSE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            purpose=purpose,
        )
        resolver = NameResolver()
        values = _json_object(content).get("claims", [])
        return [claim for value in values if (claim := normalize_claim(value, resolver))]
    finally:
        if owned:
            client.close()


def extract_chunk_claims(
    chunks: list[tuple[str, str]],
    client: BudgetedLLMClient | None = None,
    *,
    purpose: str = "claims_cache_chunk_extraction",
    max_tokens: int = 8000,
) -> dict[str, list[dict[str, Any]]]:
    """Extract claims for opaque-keyed chunks without adding source/version context."""
    owned = client is None
    client = client or BudgetedLLMClient()
    payload = "\n\n".join(
        f"SOURCE_KEY: {source_key}\n<CHUNK>\n{text}\n</CHUNK>"
        for source_key, text in chunks
    )
    try:
        content = client.complete(
            [
                {"role": "system", "content": CHUNK_SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            purpose=purpose,
        )
        response = _json_object(content).get("chunks", [])
        resolver = NameResolver()
        text_by_key = dict(chunks)
        results: dict[str, list[dict[str, Any]]] = {}
        for row in response if isinstance(response, list) else []:
            if not isinstance(row, dict) or row.get("source_key") not in text_by_key:
                continue
            source_key = row["source_key"]
            normalized = []
            for raw in row.get("claims", []) if isinstance(row.get("claims"), list) else []:
                claim = normalize_claim(raw, resolver)
                span = raw.get("span_text") if isinstance(raw, dict) else None
                if claim is None or not isinstance(span, str) or not span:
                    continue
                if span not in text_by_key[source_key]:
                    continue
                claim.pop("source_page_title", None)
                claim.pop("source_label", None)
                claim.pop("source_span", None)
                claim["span_text"] = span
                normalized.append(claim)
            results[source_key] = normalized
        return results
    finally:
        if owned:
            client.close()


def relevant_template_text(text: str, page_title: str) -> str:
    history = re.search(r"^==+\s*History\s*==+\s*$", text, re.I | re.M)
    cutoff = history.start() if history else len(text)
    blocks = []
    for offset, body in iter_templates(text[:cutoff]):
        name, _, _ = parse_template(body)
        if name in {"crafting", "smelting", "lootchestitem"}:
            heading_start = text.rfind("\n==", 0, offset)
            heading_end = text.find("\n", heading_start + 1) if heading_start >= 0 else -1
            heading = text[heading_start + 1 : heading_end] if heading_end >= 0 else ""
            blocks.append(f"{heading}\n{{{{{body}}}}}")
    return f"Page title: {page_title}\n\n" + "\n\n".join(blocks)


def _signature(claim: dict[str, Any]) -> tuple[Any, ...]:
    kind = claim["claim_type"]
    if kind == "chest_loot":
        return (kind, claim["subject_item"])
    payload = claim["payload"]
    layout = payload.get("layout")
    if layout is not None:
        ingredient_shape = trim_layout(layout)
    else:
        ingredient_shape = tuple(sorted(payload.get("ingredients", [])))
    return (
        kind,
        claim["subject_item"],
        int(payload.get("output_count", 1)),
        payload.get("station"),
        ingredient_shape,
    )


def evaluate(gold: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[str, Any]:
    gold_counts = Counter(_signature(x) for x in gold)
    predicted_counts = Counter(_signature(x) for x in predicted)
    matched = sum((gold_counts & predicted_counts).values())
    predicted_n, gold_n = sum(predicted_counts.values()), sum(gold_counts.values())
    return {
        "gold": gold_n,
        "predicted": predicted_n,
        "matched": matched,
        "accuracy": matched / predicted_n if predicted_n else 0.0,
        "recall": matched / gold_n if gold_n else 0.0,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--gold-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    snapshots = json.loads(args.snapshot_manifest.read_text())["snapshots"]
    client = BudgetedLLMClient()
    results: dict[str, Any] = {}
    progress_path = args.output_dir / "a2_progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    try:
        for version, pages in snapshots.items():
            tag = "t1" if version == "1.16.5" else "t2"
            gold_all = [json.loads(line) for line in (args.gold_dir / f"claims_gold_{tag}.jsonl").read_text().splitlines()]
            gold = [
                row for row in gold_all
                if row["claim_type"] in TARGET_TYPES and row["tense"] == "current"
            ]
            relevant_titles = {row["source"]["page_title"] for row in gold}
            output_path = args.output_dir / f"claims_llm_{tag}.jsonl"
            predicted = (
                [json.loads(line) for line in output_path.read_text().splitlines()]
                if output_path.exists()
                else []
            )
            completed_titles = set(progress.get(tag, []))
            pending = sorted(relevant_titles - completed_titles)
            for start in range(0, len(pending), 5):
                batch_titles = pending[start : start + 5]
                snippets = []
                for title in batch_titles:
                    metadata = pages[title]
                    raw = (args.raw_root / metadata["wikitext"]).read_text()
                    snippets.append(relevant_template_text(raw, title))
                batch_text = "\n\n----- NEXT PAGE -----\n\n".join(snippets)
                extracted = extract_claims(
                    batch_text,
                    client,
                    purpose=f"a2_{tag}_structured",
                    max_tokens=5000,
                )
                for claim in extracted:
                    title = claim.pop("source_page_title", "")
                    if title not in batch_titles:
                        continue
                    metadata = pages[title]
                    claim["source"] = {
                        "site": metadata["source_site"],
                        "page_title": title,
                        "revid": metadata["revid"],
                        "timestamp": metadata["timestamp"],
                        "timepoint": version,
                    }
                    predicted.append(claim)
                write_jsonl(output_path, predicted)
                completed_titles.update(batch_titles)
                progress[tag] = sorted(completed_titles)
                write_json(progress_path, progress)
            results[version] = evaluate(gold, predicted)
    finally:
        client.close()
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
