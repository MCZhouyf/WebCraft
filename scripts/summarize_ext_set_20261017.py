#!/usr/bin/env python3
"""Post-run EXT tabulation with method-actual query exposure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_ext_set_20261017 as ext  # noqa: E402


def main() -> int:
    ext.verify_freeze()
    tasks = ext.jsonl(ext.TASK_PATH)
    records = ext.jsonl(ext.OUTPUT / "run/all_public/episodes.jsonl")
    rows = ext.episode_rows(records, tasks)
    corrected = 0
    for row in rows:
        if row["stale_specific_query_exposure"] and row["search_calls"] == 0:
            row["stale_specific_query_exposure"] = 0
            corrected += 1

    ext.csv_write(ext.OUTPUT / "ext_episode_metrics.csv", rows, list(rows[0]))
    mechanism_table = ext.aggregate(
        rows, ("independent_mechanism_id", "condition", "world", "method")
    )
    ext.csv_write(
        ext.OUTPUT / "ext_results_by_mechanism.csv",
        mechanism_table,
        list(mechanism_table[0]),
    )
    overall = ext.aggregate(rows, ("condition", "world", "method"))
    ext.csv_write(ext.OUTPUT / "ext_results_overall.csv", overall, list(overall[0]))
    judgments = ext.judgments(rows)
    ext.csv_write(
        ext.OUTPUT / "mechanism_judgments.csv", judgments, list(judgments[0])
    )
    ext.write_report(rows, mechanism_table, judgments)
    report = (ext.OUTPUT / "REPORT.md").read_text()
    report = report.replace(
        "Query and execution exposure are likewise restricted to that ID.",
        "Query and execution exposure are likewise restricted to that ID; "
        "query exposure requires a positive method-level search count.",
    )
    report += (
        "\n## Post-run aggregation correction\n\n"
        f"The evidence cache is prebuilt for every method. The initial tabulator therefore "
        f"marked {corrected} Parametric/Oracle method-task rows as query-exposed despite a "
        "method-level search count of zero. This report and the CSV tables require "
        "`search_calls > 0` for query exposure. Episode outcomes, costs, plan-level "
        "acceptance, and all per-mechanism judgments are unchanged.\n"
    )
    (ext.OUTPUT / "REPORT.md").write_text(report)
    amendment = {
        "schema_version": 1,
        "analysis_script_sha256": ext.sha256(Path(__file__)),
        "rule": "stale_specific_query_exposure requires search_calls > 0",
        "corrected_episode_rows": corrected,
        "affected_fields": ["stale_specific_query_exposure"],
        "unaffected_fields": [
            "SR", "feasible_SR", "overconservative", "execution_failure",
            "plan_acceptance_stale_any", "plan_acceptance_independent",
            "stale_specific_execution_exposure", "real_cost",
        ],
    }
    (ext.OUTPUT / "analysis_revision.json").write_text(
        json.dumps(amendment, indent=2, sort_keys=True) + "\n"
    )
    hashes = {
        str(path.relative_to(ROOT)): ext.sha256(path)
        for path in sorted(ext.OUTPUT.rglob("*"))
        if path.is_file() and path.name != "OUTPUT_HASHES.json"
    }
    (ext.OUTPUT / "OUTPUT_HASHES.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({
        "status": "EXT_SUMMARY_COMPLETE",
        "episodes": len(rows),
        "query_rows_corrected": corrected,
        "output_files_hashed": len(hashes),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
