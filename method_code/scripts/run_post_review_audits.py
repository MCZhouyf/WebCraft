"""Deterministic A2--A5 post-review audits over saved episode records."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from arbiter.evaluation.p6_execution import _claims, _compiler


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
REPORTS = ROOT / "reports/main/post_review"
MAIN = ROOT / "artifacts/main"
ORACLE = ROOT / "artifacts/post_review/oracle_reading"
POST = ROOT / "artifacts/post_run_probe_then_rag"
MECHANISM_ACTIONS = {"mine", "craft_grid", "smelt", "check_name"}


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write(path: Path, values: list[dict[str, Any]]) -> None:
    if not values:
        raise ValueError(f"empty report: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(values)


def evidence_for(mode: str, record: dict[str, Any]) -> dict[str, Any] | None:
    if mode == "oracle_reading":
        path = ORACLE / "evidence" / f"{record['condition']}__{record['task_id']}.json"
    else:
        path = MAIN / mode / "evidence" / f"{record['condition']}__{record['task_id']}__{record['seed']}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def aggregation(method: str, bundle: dict[str, Any], normalizer: Any) -> Any | None:
    if method in {"parametric", "probe_then_parametric", "oracle"}:
        return None
    derived, _ = _claims(bundle, normalizer=normalizer)
    if not derived:
        return None
    from arbiter.agent.aggregation import arbiter, majority_rag, naive_rag, truthfinder_rag, verify_all
    if method in {"naive_rag", "arbiter_shell_naive", "probe_then_rag"}:
        return naive_rag(derived)
    if method == "majority_rag": return majority_rag(derived)
    if method == "truthfinder_rag": return truthfinder_rag(derived)
    if method == "verify_all": return verify_all(derived)
    if method == "arbiter":
        sources = {(d.hit["source_site"], d.hit["page_title"], d.hit["revid"]) for d in derived}
        return arbiter(derived) if len(sources) >= 2 else naive_rag(derived)
    return None


def action_mechanisms(record: dict[str, Any]) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    events = [event for event in record["events"] if event.get("method") in MECHANISM_ACTIONS]
    mechanisms = record.get("used_mechanism_ids", [])
    traces = record.get("policy_trace", [])
    if len(events) != len(mechanisms):
        raise ValueError("saved action/mechanism alignment mismatch")
    return [(event, mechanism, traces[index] if index < len(traces) else {})
            for index, (event, mechanism) in enumerate(zip(events, mechanisms))]


def main() -> None:
    if str(WEBCRAFT) not in sys.path: sys.path.insert(0, str(WEBCRAFT))
    normalizer, _ = _compiler(WEBCRAFT)
    conflicts = {row["id"]: row["truth_vector"] for row in rows(WEBCRAFT / "data/conflicts.jsonl")}
    datasets = {
        "all_public": rows(MAIN / "all_public/episodes.jsonl") + rows(POST / "all_public/episodes.jsonl"),
        "llm_only": rows(MAIN / "llm_only/episodes.jsonl") + rows(POST / "llm_only/episodes.jsonl"),
        "oracle_reading": rows(ORACLE / "episodes.jsonl"),
    }
    iaa_detail, behavior_detail, ic_detail = [], [], []
    answer_cache: dict[tuple[str, str, str, str], dict[str, bool | str]] = {}
    for mode, episodes in datasets.items():
        for record in episodes:
            method = record["method"]
            cache_key = (mode, record["condition"], record["task_id"], method)
            if cache_key not in answer_cache:
                bundle = evidence_for(mode, record)
                result = aggregation(method, bundle, normalizer) if bundle else None
                answer_cache[cache_key] = {
                    conflict_id: (
                        result.belief.mechanism_probability(conflict_id) >= 0.5
                        if result is not None and conflict_id in result.belief.phi else "unknown"
                    )
                    for conflict_id in record["conflict_mechanism_ids"]
                }
            answers = answer_cache[cache_key]
            observed = {mechanism: bool(event.get("success"))
                        for event, mechanism, trace in action_mechanisms(record)
                        if trace.get("intent") == "probe"}
            for conflict_id in record["conflict_mechanism_ids"]:
                truth = conflicts[conflict_id][record["world"]]
                answer: bool | str = "unknown"
                if method.startswith("probe_then") and conflict_id in observed:
                    answer = observed[conflict_id]
                else:
                    answer = answers[conflict_id]
                iaa_detail.append({
                    "reading_mode": mode, "method": method, "condition": record["condition"],
                    "world": record["world"], "task_id": record["task_id"], "seed": record["seed"],
                    "claim_id": conflict_id, "answer": answer, "truth": truth,
                    "correct": int(answer != "unknown" and answer == truth),
                    "unknown": int(answer == "unknown"),
                })
            committed: set[str] = set()
            stale_all = stale_commit = disagreements = 0
            for index, (event, mechanism, trace) in enumerate(action_mechanisms(record)):
                vector = conflicts.get(mechanism)
                stale = bool(vector and vector.get(record["world"]) is False
                             and any(value is True for value in vector.values()))
                behavioral_probe = bool(mechanism in conflicts and mechanism not in committed)
                declared_probe = event.get("intent") == "probe"
                disagreements += int(behavioral_probe != declared_probe)
                stale_all += int(stale)
                stale_commit += int(stale and not behavioral_probe)
                if not behavioral_probe: committed.add(mechanism)
                behavior_detail.append({
                    "reading_mode": mode, "method": method, "condition": record["condition"],
                    "world": record["world"], "task_id": record["task_id"], "seed": record["seed"],
                    "action_index": index, "mechanism_id": mechanism, "stale": int(stale),
                    "behavioral_probe": int(behavioral_probe), "declared_probe": int(declared_probe),
                    "label_disagreement": int(behavioral_probe != declared_probe),
                })
            record["_behavioral_htr_all"] = int(bool(stale_all))
            record["_behavioral_htr_commit"] = int(bool(stale_commit))
            record["_label_disagreements"] = disagreements
            reports = []
            total = 0.0
            for event in record["events"]:
                total += float(event["cost"]["primary"])
                if event.get("method") == "report_version":
                    reports.append((event.get("params", {}).get("guess_sha256"), total))
            if method.startswith("probe_then"):
                expected = hashlib.sha256(record["world"].encode()).hexdigest()
                correct = [cost for guess, cost in reports if guess == expected]
                ic_detail.append({"reading_mode": mode, "method": method, "condition": record["condition"],
                                  "world": record["world"], "task_id": record["task_id"], "seed": record["seed"],
                                  "report_events": len(reports), "IC": correct[-1] if correct else ""})
    write(REPORTS / "iaa_query_inventory.csv", iaa_detail)
    write(REPORTS / "behavioral_htr_actions.csv", behavior_detail)
    write(REPORTS / "ic_replay_detail.csv", ic_detail)
    iaa_summary = []
    behavioral_summary = []
    for mode, episodes in datasets.items():
        by_method = defaultdict(list)
        for row in iaa_detail:
            if row["reading_mode"] == mode: by_method[row["method"]].append(row)
        for method, values in sorted(by_method.items()):
            iaa_summary.append({"reading_mode": mode, "method": method, "queries": len(values),
                                "accuracy": sum(v["correct"] for v in values) / len(values),
                                "unknown_rate": sum(v["unknown"] for v in values) / len(values)})
        grouped = defaultdict(list)
        for row in episodes: grouped[row["method"]].append(row)
        for method, values in sorted(grouped.items()):
            behavioral_summary.append({
                "reading_mode": mode, "method": method, "episodes": len(values),
                "behavioral_HTR_commit": sum(v["_behavioral_htr_commit"] for v in values) / len(values),
                "behavioral_HTR_all": sum(v["_behavioral_htr_all"] for v in values) / len(values),
                "self_reported_HTR_commit": sum(v["metrics"]["HTR_commit"] for v in values) / len(values),
                "self_reported_HTR_all": sum(v["metrics"]["HTR_all"] for v in values) / len(values),
                "intent_label_disagreements": sum(v["_label_disagreements"] for v in values),
            })
    write(REPORTS / "iaa_replay.csv", iaa_summary)
    write(REPORTS / "behavioral_htr_summary.csv", behavioral_summary)
    ic_summary = []
    grouped_ic = defaultdict(list)
    for row in ic_detail: grouped_ic[(row["reading_mode"], row["method"])].append(row)
    for (mode, method), values in sorted(grouped_ic.items()):
        found = [float(v["IC"]) for v in values if v["IC"] != ""]
        ic_summary.append({"reading_mode": mode, "method": method, "episodes": len(values),
                           "identified": len(found), "IC_mean": sum(found) / len(found) if found else ""})
    write(REPORTS / "ic_replay.csv", ic_summary)
    source = ROOT / "reports/main/acceptance_exports/aggregation_candidates.csv"
    target = REPORTS / "aggregation_rule_consistency.csv"
    shutil.copyfile(source, target)
    (REPORTS / "AUDIT_README.md").write_text(
        "Post-review A2--A5 deterministic replay. IAA queries are task conflict IDs; "
        "answers are reconstructed from the frozen aggregator over the saved evidence bundle, "
        "with saved probe outcomes taking precedence for Probe-then methods. Behavioral probe "
        "classification is independent of declared intent: a conflict mechanism is a probe until "
        "a prior executed non-probe step depends on that same mechanism. Aggregation consistency "
        "is the fixed all-public candidate audit copied without modification.\n"
    )


if __name__ == "__main__": main()
