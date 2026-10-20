"""Record explicit infobox fields and source evidence from local snapshots."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from corpus.block_templates import extract_block_claims, is_block_template
from corpus.gold_claims import NameResolver, iter_templates, parse_template
from corpus.redirect_audit import ROOT, sources


def audit(root: Path = ROOT) -> dict:
    resolver = NameResolver()
    selected = sources(root)
    resolved_path = root / "corpus/manifests/redirect_resolved_snapshots.json"
    resolved = json.loads(resolved_path.read_text()) if resolved_path.exists() else {}
    rows, totals = [], Counter()
    for label, (records, raw_root) in selected.items():
        pages = {title: (record, raw_root) for title, record in records.items()}
        pages.update({title: (record, root / "corpus/raw/redirect_resolved")
                      for title, record in resolved.get(label, {}).items()})
        for title, (record, directory) in pages.items():
            text = (directory / record["wikitext"]).read_text()
            claims = extract_block_claims(text, title, resolver)
            totals.update(c["claim_type"] for c in claims)
            for offset, body in iter_templates(text):
                name, _, fields = parse_template(body)
                if not is_block_template(name):
                    continue
                rows.append({"source": label, "page_title": title, "revid": record["revid"],
                    "template_name": name, "drops_field_present": "drops" in fields or "drop" in fields,
                    "drops_raw": fields.get("drops", fields.get("drop", "")),
                    "tool_raw": fields.get("tool", ""), "hardness_raw": fields.get("hardness", ""),
                    "template_drop_claims": sum(c["claim_type"] == "drop" for c in claims),
                    "template_tool_claims": sum(c["claim_type"] == "tool_requirement" for c in claims),
                    "evidence_json": json.dumps(text[offset:offset + len(body) + 4][:200])})
    destination = root / "reports/corpus_stats/block_infobox_audit.csv"
    with destination.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {"infoboxes": len(rows), "with_explicit_drops": sum(r["drops_field_present"] for r in rows),
            "claims_by_type": dict(totals)}


if __name__ == "__main__":
    print(json.dumps(audit(), sort_keys=True))
