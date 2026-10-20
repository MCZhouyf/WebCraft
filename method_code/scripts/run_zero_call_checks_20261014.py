"""Deterministic log-only checks requested on 2026-10-14."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.evaluation.consequence_htr import action_signature, inventory_dependencies
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_resource_nodes


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
OUT = ROOT / "reports/main/post_review/htr_consequence"
KEY_FIELDS = ("reading_mode", "condition", "world", "task_id", "seed")


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"empty output: {path}")
    fields = list(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise ValueError(f"inconsistent fields: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def replace_section(path: Path, name: str, body: str) -> None:
    start = f"<!-- {name}:start -->"
    end = f"<!-- {name}:end -->"
    text = path.read_text().rstrip()
    section = f"{start}\n{body.rstrip()}\n{end}"
    if start in text:
        prefix, tail = text.split(start, 1)
        _, suffix = tail.split(end, 1)
        text = prefix.rstrip() + "\n\n" + section + suffix
    else:
        text += "\n\n" + section
    path.write_text(text.rstrip() + "\n")


def pipeline_records() -> dict[tuple[Any, ...], dict[str, Any]]:
    datasets = {
        "all_public": (
            jsonl(ROOT / "artifacts/main/all_public/episodes.jsonl")
            + jsonl(ROOT / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl")
        ),
        "llm_only": (
            jsonl(ROOT / "artifacts/main/llm_only/episodes.jsonl")
            + jsonl(ROOT / "artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl")
        ),
        "oracle_reading": jsonl(ROOT / "artifacts/post_review/oracle_reading/episodes.jsonl"),
    }
    index = {}
    for mode, records in datasets.items():
        for record in records:
            if record["method"] != "verify_all":
                continue
            key = (mode, record["condition"], record["world"], record["task_id"], int(record["seed"]))
            if key in index:
                raise RuntimeError(f"duplicate Verify-All episode: {key}")
            index[key] = record
    return index


def predicted_product(trace: dict[str, Any]) -> str:
    try:
        signature = json.loads(trace.get("action_signature", "null"))
    except json.JSONDecodeError:
        return ""
    if isinstance(signature, list) and len(signature) >= 3 and isinstance(signature[2], str):
        return signature[2].removeprefix("minecraft:")
    return ""


def continue_dependency_audit() -> list[dict[str, Any]]:
    source = [
        row for row in csv_rows(OUT / "verify_all_after_negative.csv")
        if row["next_behavior"] == "continue_original_plan"
    ]
    if len(source) != 138:
        raise RuntimeError(f"expected 138 continue events, got {len(source)}")
    records = pipeline_records()
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    output = []
    for row in source:
        key = (
            row["reading_mode"], row["condition"], row["world"], row["task_id"],
            int(row["seed"]),
        )
        record = records[key]
        event_index = int(row["event_index"])
        semantic = [
            (index, event) for index, event in enumerate(record["events"])
            if event.get("method") in ACTION_METHODS
        ]
        positions = [position for position, (index, _) in enumerate(semantic) if index == event_index]
        if len(positions) != 1:
            raise RuntimeError(f"probe event does not align once: {key + (event_index,)}")
        position = positions[0]
        mechanisms = list(record.get("used_mechanism_ids", []))
        traces = list(record.get("policy_trace", []))
        if len(semantic) != len(mechanisms) or len(semantic) != len(traces):
            raise RuntimeError(f"semantic trace alignment changed: {key}")
        mechanism = mechanisms[position]
        if mechanism != row["mechanism_id"]:
            raise RuntimeError(f"negative-probe mechanism changed: {key + (event_index,)}")
        trace = traces[position]
        if trace.get("intent") != "probe" or trace.get("observed") is not False:
            raise RuntimeError(f"source event is no longer a negative probe: {key + (event_index,)}")

        probe_event = record["events"][event_index]
        prediction = predicted_product(trace)
        flow = inventory_dependencies(record["events"]).get(event_index, [])
        predicted_consumers = sorted({
            index for index, item, _ in flow if prediction and item == prediction
        })
        actual_consumers = sorted({index for index, _, _ in flow})
        same_mechanism = sorted({
            index for later_position, (index, _) in enumerate(semantic[position + 1:], position + 1)
            if mechanisms[later_position] == mechanism
        })
        same_environment_action = sorted({
            index for index, event in semantic[position + 1:]
            if action_signature(event, nodes) == action_signature(probe_event, nodes)
        })
        dependent = sorted(set(predicted_consumers) | set(same_mechanism))
        actual_products = sorted({
            str(item.get("name", "")).removeprefix("minecraft:")
            for item in probe_event.get("item_flow", {}).get("produced", [])
            if item.get("name")
        })
        output.append({
            **{name: row[name] for name in KEY_FIELDS},
            "probe_event_index": event_index,
            "mechanism_id": mechanism,
            "probe_method": probe_event.get("method", ""),
            "predicted_product": prediction,
            "actual_probe_products": ";".join(actual_products),
            "later_semantic_actions": len(semantic) - position - 1,
            "same_environment_action_indices": ";".join(map(str, same_environment_action)),
            "later_same_mechanism_indices": ";".join(map(str, same_mechanism)),
            "predicted_product_flow_consumers": ";".join(map(str, predicted_consumers)),
            "actual_output_flow_consumers": ";".join(map(str, actual_consumers)),
            "dependent_later_action_indices": ";".join(map(str, dependent)),
            "dependency_count": len(dependent),
            "dependency_verdict": "independent" if not dependent else "dependent",
        })
    return output


def disagreement_summary() -> tuple[list[dict[str, Any]], int, int]:
    disagreements = csv_rows(OUT / "disagreements.csv")
    matrices = csv_rows(OUT / "consistency_matrix.csv")
    totals = [row for row in matrices if row["method_scope"] == "__all_methods__"]
    total_episodes = sum(int(row["episodes"]) for row in totals)
    off_diagonal = sum(
        int(row["episodes"]) for row in totals
        if row["plan_source"] != row["consequence"]
    )
    if off_diagonal != len(disagreements):
        raise RuntimeError("existing disagreement table and matrix no longer agree")
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in disagreements:
        grouped.setdefault(row["reason"], []).append(row)
    output = []
    for reason, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        directions = {(row["HTR_commit_plan_source"], row["HTR_commit_c"]) for row in rows}
        if len(directions) != 1:
            raise RuntimeError(f"mixed disagreement direction: {reason}")
        plan, consequence = directions.pop()
        output.append({
            "reason": reason,
            "HTR_commit_plan_source": plan,
            "HTR_commit_c": consequence,
            "instances": len(rows),
            "share_of_disagreements": len(rows) / len(disagreements),
            "share_of_all_episodes": len(rows) / total_episodes,
        })
    return output, len(disagreements), total_episodes


def probe_then_parametric_disclosure() -> str:
    execution = ROOT / "src/arbiter/evaluation/p6_execution.py"
    core = ROOT / "src/arbiter/agent/core.py"
    execution_text = execution.read_text()
    core_text = core.read_text()
    required = (
        'if job.method in {"parametric", "probe_then_parametric"}',
        'if job.method == "probe_then_rag"',
        '"verify_all", "probe_then_parametric"',
    )
    if any(value not in execution_text for value in required):
        raise RuntimeError("Probe-then-Parametric dispatch changed")
    if 'step = plans[0].steps[0]' not in core_text or 'Decision("probe"' not in core_text:
        raise RuntimeError("generic all-plan-dependency probing path changed")
    execution_hash = hashlib.sha256(execution.read_bytes()).hexdigest()
    core_hash = hashlib.sha256(core.read_bytes()).hexdigest()
    return "\n".join([
        "## Probe-then-Parametric information inputs", "",
        "- **Probe target source:** the method loads the frozen parametric-prior operators and uses the shared "
        "`VERIFY_ALL_EXECUTION` path. Each probe target is the first mechanism dependency of the current "
        "planner candidate (`plans[0].steps[0]`); it is not selected from a hard-coded separator list.",
        "- **Outcome adjudication:** the public action result is compared with that one operator's expected "
        "output and updates only its mechanism belief. There is no probe-outcome-to-version classification.",
        "- **Version information:** the Probe-then-Parametric dispatch does not call "
        "`_probe_then_rag_identify`, `_probe_then_rag_filter_bundle`, or `report_version`, and it configures no "
        "time anchors. The hard-coded version labels elsewhere in `p6_execution.py` belong to the disjoint "
        "Probe-then-RAG branch.",
        "- **Conclusion:** Probe-then-Parametric contains neither hard-coded version labels nor a "
        "probe-to-version mapping on its runtime path. It probes individual dependencies from the frozen "
        "parametric prior and is version-blind under the disclosure standard used above.", "",
        f"- `p6_execution.py` SHA-256: `{execution_hash}`",
        f"- `agent/core.py` SHA-256: `{core_hash}`",
    ])


def main() -> None:
    dependencies = continue_dependency_audit()
    dependency_count = sum(int(row["dependency_count"] > 0) for row in dependencies)
    summary, disagreement_count, total_episodes = disagreement_summary()
    write_csv(OUT / "verify_all_continue_dependency.csv", dependencies)
    write_csv(OUT / "disagreement_reason_summary.csv", summary)
    replace_section(
        OUT / "probe_then_rag_inputs.md", "probe-then-parametric-audit",
        probe_then_parametric_disclosure(),
    )
    reason_lines = [
        "## Follow-up zero-call checks", "",
        f"- Verify-All dependent continuations: {dependency_count}/138. "
        + ("继续的是计划中与被否定说法无关的部分。" if dependency_count == 0 else
           "These cases are plan-source HTR_commit exceptions and are listed in the dependency table."),
        f"- Commitment-definition disagreements: {disagreement_count}/{total_episodes} "
        f"({disagreement_count / total_episodes:.6f}).", "",
        "| Reason | Plan-source | Consequence | Count | Share of disagreements |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {row['reason']} | {row['HTR_commit_plan_source']} | {row['HTR_commit_c']} | "
            f"{row['instances']} | {row['share_of_disagreements']:.4%} |"
            for row in summary
        ], "",
        "These counts aggregate the already exported disagreement reasons; no episode was rescored.",
    ]
    replace_section(OUT / "REPORT.md", "follow-up-zero-call-checks", "\n".join(reason_lines))
    print(json.dumps({
        "status": "ZERO_CALL_CHECKS_COMPLETE",
        "continue_events": len(dependencies),
        "dependent_continue_events": dependency_count,
        "disagreements": disagreement_count,
        "pipeline_episodes": total_episodes,
        "llm_calls": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
