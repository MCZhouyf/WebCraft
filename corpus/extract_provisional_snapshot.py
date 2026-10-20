"""Extract the latest 2023 WikiTeam3 revisions for provisional indexes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from corpus.dump_pipeline import extract_snapshots, parse_timestamp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text())
    titles = {row["page_title"] for row in mapping if row.get("verified_in_dump")}
    cutoff = "2023-08-22T23:59:59+00:00"
    snapshots = extract_snapshots(
        args.dump,
        titles,
        {"fandom_2023": parse_timestamp(cutoff)},
        args.output_dir,
    )["fandom_2023"]
    payload = {
        "source": "Internet Archive WikiTeam3",
        "dump": args.dump.name,
        "cutoff": cutoff,
        "provisional": True,
        "t3_source": "fandom_2023",
        "snapshots": snapshots,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"provisional_pages": len(snapshots)}, sort_keys=True))


if __name__ == "__main__":
    main()
