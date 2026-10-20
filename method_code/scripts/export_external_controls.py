"""Score and export the preregistered external-agent control segments."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from arbiter.evaluation.external_scoring import ACTION_METHODS, _matches, load_resource_nodes


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
ARTIFACTS = ROOT / "artifacts/post_review/external_controls"
OUTPUT = ROOT / "reports/main/post_review/external_controls"
METHODS = ("direct_llm", "react")
SEGMENTS = ("oracle_knowledge", "control_all_public", "conditions")


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def interval(values: list[int], seed: int = 20261013) -> tuple[float, float, float]:
    data = np.asarray(values, dtype=float)
    if not len(data):
        return float("nan"), float("nan"), float("nan")
    draws = np.random.default_rng(seed).integers(0, len(data), (10_000, len(data)))
    sampled = data[draws].mean(axis=1)
    return float(data.mean()), float(np.quantile(sampled, .025)), float(np.quantile(sampled, .975))


def aggregate(method: str, setting: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    feasible = [row for row in rows if row["true_feasible"]]
    sr = interval([int(row["success"]) for row in rows])
    fsr = interval([int(row["success"]) for row in feasible])
    htr = interval([int(row["metrics"]["HTR_all"]) for row in rows])
    return {
        "method": method, "setting": setting, "n": len(rows),
        "feasible_n": len(feasible), "successes": sum(int(row["success"]) for row in rows),
        "SR": sr[0], "SR_ci95_low": sr[1], "SR_ci95_high": sr[2],
        "feasible_successes": sum(int(row["success"]) for row in feasible),
        "feasible_task_SR": fsr[0], "feasible_SR_ci95_low": fsr[1],
        "feasible_SR_ci95_high": fsr[2],
        "HTR_all": htr[0], "HTR_all_ci95_low": htr[1], "HTR_all_ci95_high": htr[2],
        "mean_model_calls": sum(int(row["model_calls"]) for row in rows) / len(rows),
        "input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "output_tokens": sum(int(row["output_tokens"]) for row in rows),
    }


def load_segments(required: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for segment in required:
        path = ARTIFACTS / segment / "episodes.jsonl"
        if not path.is_file():
            raise RuntimeError(f"missing assembled segment: {segment}")
        result[segment] = jsonl(path)
    return result


def matched_first_round() -> list[dict[str, Any]]:
    path = ROOT / "reports/main/post_review/external_agents/rescore_v2/episode_metrics.csv"
    rows = []
    with path.open() as handle:
        for raw in csv.DictReader(handle):
            if raw["method"] not in METHODS or not raw["condition"].startswith("matched_"):
                continue
            rows.append({
                "method": raw["method"], "condition": raw["condition"],
                "world": raw["world"], "task_id": raw["task_id"],
                "true_feasible": bool(int(raw["true_feasible"])),
                "success": int(raw["SR"]),
                "metrics": {"HTR_all": int(raw["legacy_HTR_all"])},
                "model_calls": int(raw["model_calls"]),
                "input_tokens": int(raw["input_tokens"]),
                "output_tokens": int(raw["output_tokens"]),
            })
    if len(rows) != 132:
        raise RuntimeError(f"expected 132 first-round matched episodes, got {len(rows)}")
    return rows


def pipeline_naive_controls(control_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for row in jsonl(ROOT / "artifacts/main/all_public/episodes.jsonl"):
        if (row["method"] == "naive_rag" and row["seed"] == 20261006
                and row["task_id"] in control_ids
                and row["condition"].startswith(("matched_", "mismatched_"))):
            rows.append({
                "method": "pipeline_naive", "condition": row["condition"],
                "world": row["world"], "task_id": row["task_id"],
                "true_feasible": bool(row["true_feasible"]),
                "success": int(row["metrics"]["SR"]),
                "metrics": {"HTR_all": int(row["metrics"]["HTR_all"])},
                "model_calls": 0, "input_tokens": 0, "output_tokens": 0,
            })
    if len(rows) != 60:
        raise RuntimeError(f"expected 60 pipeline control episodes, got {len(rows)}")
    return rows


def claim_assertion(claim: dict[str, Any]) -> dict[str, Any] | None:
    payload = claim.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = claim.get("claim_type")
    if kind == "drop":
        return {"kind": "drop", "block": claim.get("subject_item"),
                "drops": payload.get("drops", []), "silk_touch": payload.get("silk_touch", False)}
    if kind in {"recipe", "smelting"}:
        station = payload.get("station")
        layout = payload.get("layout")
        if station == "crafting_grid" and isinstance(layout, list) and len(layout) == 9:
            return {"kind": "concrete_grid", "layout": layout}
        ingredients = payload.get("ingredients")
        if not isinstance(ingredients, list):
            return None
        return {"kind": "recipe", "station": station,
                "ingredients": [[value] for value in ingredients], "ordered": False}
    if kind == "name":
        return {"kind": "name", "item": claim.get("subject_item")}
    return None


def pollution_contact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = WEBCRAFT / "corpus/indexes/formal_p1"
    mixed = {row["chunk_id"] for row in jsonl(index / "mixed/documents.jsonl")}
    polluted = {row["chunk_id"] for row in jsonl(index / "polluted/documents.jsonl")}
    synthetic = polluted - mixed
    claims = {}
    for package in jsonl(WEBCRAFT / "corpus/claims_cache/formal_p1/polluted/chunk_claims.jsonl"):
        if package["chunk_id"] in synthetic:
            claims[package["chunk_id"]] = package["claims"]
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    output = []
    for row in rows:
        if row["condition"] != "polluted":
            continue
        events = jsonl(ROOT / row["action_log"])
        retrieved: dict[str, int] = {}
        for index_value, event in enumerate(events):
            if event.get("method") != "search":
                continue
            for hit in event.get("result", []):
                chunk = hit.get("chunk_id")
                if chunk in synthetic:
                    retrieved.setdefault(chunk, index_value)
        matched_claims = set()
        matched_actions = set()
        for chunk, search_index in retrieved.items():
            for claim in claims.get(chunk, []):
                assertion = claim_assertion(claim)
                if assertion is None:
                    continue
                for event_index, event in enumerate(events):
                    if event_index <= search_index or event.get("method") not in ACTION_METHODS:
                        continue
                    if _matches(event, assertion, nodes):
                        matched_claims.add(str(claim["claim_id"]))
                        matched_actions.add(event_index)
        output.append({
            "method": row["method"], "condition": row["condition"],
            "world": row["world"], "task_id": row["task_id"],
            "synthetic_chunks_retrieved": len(retrieved),
            "synthetic_claims_action_matched": len(matched_claims),
            "subsequent_actions_matched": len(matched_actions),
            "synthetic_entered_plan_proxy": int(bool(matched_actions)),
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--through", choices=SEGMENTS, required=True)
    args = parser.parse_args()
    required = SEGMENTS[:SEGMENTS.index(args.through) + 1]
    segments = load_segments(required)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    table = []
    for segment, rows in segments.items():
        for method in METHODS:
            group = [row for row in rows if row["method"] == method]
            if segment == "conditions":
                for condition in ("mixed", "polluted"):
                    table.append(aggregate(
                        method, condition,
                        [row for row in group if row["condition"] == condition],
                    ))
            else:
                table.append(aggregate(method, segment, group))
    if "control_all_public" in segments:
        control_ids = {row["task_id"] for row in segments["control_all_public"]}
        baseline = pipeline_naive_controls(control_ids)
        table.append(aggregate("pipeline_naive", "control_all_public", baseline))
    if "conditions" in segments:
        matched = matched_first_round()
        for method in METHODS:
            table.append(aggregate(method, "matched_first_round", [row for row in matched if row["method"] == method]))
        contacts = pollution_contact(segments["conditions"])
        write_csv(OUTPUT / "pollution_contact.csv", contacts)
    write_csv(OUTPUT / "main_table.csv", table)
    settings = ("mixed", "polluted") if args.through == "conditions" else (args.through,)
    write_csv(OUTPUT / f"segment_{args.through}.csv", [row for row in table if row["setting"] in settings])

    if args.through == "conditions":
        index = {(row["method"], row["setting"]): row for row in table}
        report = ["# External capability controls", ""]
        for method in METHODS:
            oracle = index[(method, "oracle_knowledge")]
            passed = oracle["feasible_task_SR"] >= .5
            report.append(
                f"J1 ({method}): feasible-task SR = {oracle['feasible_task_SR']:.4f}; "
                f"{'knowledge acquisition/judgment bottleneck criterion met' if passed else 'basic capability bottleneck criterion met'}."
            )
        naive = index[("pipeline_naive", "control_all_public")]
        for method in METHODS:
            control = index[(method, "control_all_public")]
            report.append(
                f"J2 ({method}): control SR = {control['SR']:.4f}; pipeline Naive control SR = "
                f"{naive['SR']:.4f}; difference = {control['SR'] - naive['SR']:+.4f}."
            )
        for method in METHODS:
            matched_row = index[(method, "matched_first_round")]
            mixed = aggregate(method, "mixed", [row for row in segments["conditions"] if row["method"] == method and row["condition"] == "mixed"])
            polluted = aggregate(method, "polluted", [row for row in segments["conditions"] if row["method"] == method and row["condition"] == "polluted"])
            relevant = [row for row in contacts if row["method"] == method]
            report.append(
                f"J3 ({method}): matched/mixed/polluted feasible SR = "
                f"{matched_row['feasible_task_SR']:.4f}/{mixed['feasible_task_SR']:.4f}/{polluted['feasible_task_SR']:.4f}; "
                f"HTR_all = {matched_row['HTR_all']:.4f}/{mixed['HTR_all']:.4f}/{polluted['HTR_all']:.4f}; "
                f"synthetic-plan proxy = {sum(row['synthetic_entered_plan_proxy'] for row in relevant)}/{len(relevant)}."
            )
        statuses = [json.loads((ARTIFACTS / segment / "status.json").read_text()) for segment in SEGMENTS]
        identifiers = sorted({value for status in statuses for value in status["server_model_identifiers"]})
        report.extend(("", f"Actual returned model identifier(s): {', '.join(identifiers)}.",
                       f"Total task calls: {sum(status['task_calls'] for status in statuses)}."))
        (OUTPUT / "REPORT.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"through": args.through, "rows": len(table), "output": str(OUTPUT)}, sort_keys=True))


if __name__ == "__main__":
    main()
