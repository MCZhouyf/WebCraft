"""Extract the diagnosed P0 prose-only Iron Ore drop claims with span validation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from corpus.extract_claims import extract_prose_claims, write_jsonl
from corpus.gold_claims import VERSIONS
from corpus.llm_client import BudgetedLLMClient


ROOT = Path(__file__).resolve().parents[1]


def drop_truth(claim: dict[str, Any], version: str) -> bool:
    truth = json.loads((ROOT / f"truth/{version}.json").read_text())
    expected = None
    for row in truth["drops"]:
        if row["block"].removeprefix("minecraft:") == claim["subject_item"]:
            key = (
                "silk_touch_items"
                if claim["payload"]["silk_touch"]
                else "no_silk_touch_items"
            )
            expected = sorted(item.removeprefix("minecraft:") for item in row[key])
            break
    return expected == sorted(claim["payload"]["drops"])


def relevant_passage(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lower = line.casefold()
        if ("iron ore" in lower or "raw iron" in lower) and "drop" in lower:
            lines.append(re.sub(r"\s+", " ", line).strip())
    return "\n".join(dict.fromkeys(line for line in lines if line))


def sources() -> list[tuple[str, dict[str, Any], Path]]:
    history = json.loads(
        (ROOT / "corpus/manifests/fandom_history_snapshots.json").read_text()
    )["snapshots"]
    minecraft = json.loads(
        (ROOT / "corpus/manifests/mcwiki_export_snapshots.json").read_text()
    )
    fandom = json.loads(
        (ROOT / "corpus/manifests/fandom_2023_snapshot.json").read_text()
    )["snapshots"]
    return [
        ("T1", history["1.16.5"]["Iron Ore"], ROOT / "corpus/raw/fandom_history"),
        ("T2", history["1.19"]["Iron Ore"], ROOT / "corpus/raw/fandom_history"),
        ("T3", minecraft["t3"]["Iron Ore"], ROOT / "corpus/raw/mcwiki_manual_export"),
        (
            "current",
            minecraft["current"]["Iron Ore"],
            ROOT / "corpus/raw/mcwiki_manual_export",
        ),
        ("Fandom", fandom["Iron Ore"], ROOT / "corpus/raw/fandom_history"),
    ]


def main() -> None:
    records: dict[str, tuple[dict[str, Any], str]] = {}
    blocks = []
    for label, metadata, raw_root in sources():
        text = (raw_root / metadata["wikitext"]).read_text()
        passage = relevant_passage(text)
        if not passage:
            raise RuntimeError(f"no diagnosed prose found for {label}")
        records[label] = (metadata, passage)
        blocks.append(f"Source label: {label}\nPage title: Iron Ore\n{passage}")
    client = BudgetedLLMClient()
    try:
        extracted = extract_prose_claims(
            "\n\n----- NEXT SOURCE -----\n\n".join(blocks), client
        )
    finally:
        client.close()
    claims = []
    seen = set()
    for claim in extracted:
        label = claim.pop("source_label", "")
        title = claim.pop("source_page_title", "")
        span = claim.get("source_span", "")
        if label not in records or title != "Iron Ore" or not span:
            continue
        metadata, passage = records[label]
        if span not in passage:
            continue
        if claim["subject_item"] != "iron_ore" or claim["polarity"] != "positive":
            continue
        claim["source"] = {
            "site": metadata["source_site"],
            "page_title": title,
            "revid": metadata["revid"],
            "timestamp": metadata["timestamp"],
            "timepoint": label,
        }
        claim["source_kind"] = "llm_extraction"
        claim["truth_vector"] = {
            version: drop_truth(claim, version) for version in VERSIONS
        }
        body = json.dumps(claim, sort_keys=True, separators=(",", ":"))
        claim["claim_id"] = "llm_" + hashlib.sha256(body.encode()).hexdigest()[:16]
        signature = (label, claim["tense"], body)
        if signature not in seen:
            claims.append(claim)
            seen.add(signature)
    if not any(row["truth_vector"]["1.16.5"] for row in claims):
        raise RuntimeError("LLM extraction omitted the diagnosed old iron-ore drop")
    if not any(row["truth_vector"]["1.21.11"] for row in claims):
        raise RuntimeError("LLM extraction omitted the diagnosed new iron-ore drop")
    write_jsonl(ROOT / "corpus/private/claims_llm.jsonl", claims)
    print(
        json.dumps(
            {
                "claims": len(claims),
                "sources": sorted({row["source"]["timepoint"] for row in claims}),
            }
        )
    )


if __name__ == "__main__":
    main()
