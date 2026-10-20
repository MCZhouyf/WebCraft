"""A8 zero-call query-follow-up audit and same-gateway cost estimate."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from arbiter.evaluation.external_scoring import ACTION_METHODS, _matches, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import actual_products, subject


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
OUT = ROOT / "reports/main/post_review/cds_agents_20261017"
V3D = ROOT / "reports/main/post_review/measurement_v3d/propagation_v3d_actions.csv"
EXTERNAL = {
    "direct_llm": ROOT / "artifacts/post_review/external_agents/final/direct_llm_assembled/episodes.jsonl",
    "react": ROOT / "artifacts/post_review/external_agents/final/react_assembled/episodes.jsonl",
    "reflexion": ROOT / "artifacts/post_review/external_agents/final/reflexion_assembled/episodes.jsonl",
    "adam": ROOT / "artifacts/post_review/external_agents/final/adam/episodes.jsonl",
    "xenon": ROOT / "artifacts/post_review/external_agents/final/xenon_assembled/episodes.jsonl",
}
PRICE_INPUTS = (
    WEBCRAFT / "corpus/llm_prior/conditional_calls.jsonl",
    WEBCRAFT / "corpus/motivation_multimodel/results/glm52/calls.jsonl",
)
A7_BASE = ROOT / "artifacts/post_review/diagnostic_controls_20261016"


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing empty export: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def short_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": event.get("sequence"), "method": event.get("method"),
        "params": event.get("params"), "success": event.get("success"),
        "result": event.get("result"), "item_flow": event.get("item_flow"),
    }


def external_records() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    output = {}
    for method, path in EXTERNAL.items():
        records = jsonl(path)
        if len(records) != 110:
            raise RuntimeError(f"{method} expected 110 records, got {len(records)}")
        for row in records:
            key = (method, row["condition"], row["world"], row["task_id"])
            if key in output:
                raise RuntimeError(f"duplicate first-run episode: {key}")
            output[key] = row
    return output


def classify_queries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    records = external_records()
    all_actions = [row for row in csv_rows(V3D) if row["setting"] == "first_run"]
    rows = [row for row in all_actions if row["action_method"] == "check_name"
            and row["discriminative"] == "1"]
    action_index = {
        (row["method"], row["condition"], row["world"], row["task_id"], int(row["event_index"])): row
        for row in all_actions
    }
    conflicts = {row["id"]: row for row in load_conflicts(WEBCRAFT / "data/conflicts.jsonl")}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    output = []
    snippets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["method"], row["condition"], row["world"], row["task_id"])
        record = records[key]
        events = jsonl(ROOT / record["action_log"])
        index = int(row["event_index"])
        denied = row["mismatch"] == "1"
        selected_index: int | None = None
        if not denied:
            label = "not_denied"
        else:
            current_ids = json.loads(row["stale_mechanism_ids"])
            focal_subjects = {subject(conflicts[value]["assertion"]) for value in current_ids}
            stale_assertions = [conflict["assertion"] for conflict in conflicts.values()
                                if conflict["truth_vector"].get(row["world"]) is False
                                and subject(conflict["assertion"]) in focal_subjects]
            true_assertions = [conflict["assertion"] for conflict in conflicts.values()
                               if conflict["truth_vector"].get(row["world"]) is True
                               and subject(conflict["assertion"]) in focal_subjects]
            rename_targets = {
                subject(conflict["assertion"])
                for conflict in conflicts.values()
                if conflict.get("main_type") == "renamed"
                and conflict["truth_vector"].get(row["world"]) is True
                and any(token in subject(conflict["assertion"]).split("_")
                        for focal in focal_subjects for token in focal.split("_"))
            }
            label = "no_related_followup"
            for later_index in range(index + 1, len(events)):
                event = events[later_index]
                if event.get("method") not in ACTION_METHODS:
                    continue
                later_row = action_index.get((row["method"], row["condition"], row["world"], row["task_id"], later_index))
                later_stale_ids = json.loads(later_row["stale_mechanism_ids"]) if later_row else []
                stale_same_subject = any(subject(conflicts[value]["assertion"]) in focal_subjects
                                         for value in later_stale_ids)
                if stale_same_subject or any(_matches(event, assertion, nodes) for assertion in stale_assertions):
                    label, selected_index = "denied_then_stale", later_index
                    break
                checked = str(event.get("params", {}).get("name", "")).removeprefix("minecraft:")
                valid_rename = (event.get("method") == "check_name" and checked in rename_targets
                                and isinstance(event.get("result"), dict)
                                and event["result"].get("resolvable") is True)
                valid_operator = any(_matches(event, assertion, nodes) for assertion in true_assertions)
                produces_focal = bool(actual_products(event) & focal_subjects)
                if valid_rename or valid_operator or (event.get("success") is True and produces_focal):
                    label, selected_index = "denied_then_target_valid", later_index
                    break
        if selected_index is None:
            later_submit = next((i for i in range(index + 1, len(events))
                                 if events[i].get("method") == "submit"), None)
            selected_index = later_submit
        excerpt = [short_event(events[index])]
        if selected_index is not None:
            excerpt.append(short_event(events[selected_index]))
        result = {
            "method": row["method"], "condition": row["condition"],
            "world": row["world"], "task_id": row["task_id"],
            "seed": row["seed"], "event_index": index,
            "stale_mechanism_ids": row["stale_mechanism_ids"],
            "world_denied": int(denied), "followup_class": label,
            "followup_event_index": "" if selected_index is None else selected_index,
            "action_log": record["action_log"],
            "excerpt_json": json.dumps(excerpt, sort_keys=True),
        }
        output.append(result)
        if len(snippets[(row["method"], label)]) < 2:
            snippets[(row["method"], label)].append(result)
    expected = {"direct_llm": 13, "react": 18, "reflexion": 19, "adam": 10, "xenon": 18}
    actual = Counter(row["method"] for row in output)
    if dict(actual) != expected:
        raise RuntimeError(f"query-contact input changed: {dict(actual)}")
    summary = []
    classes = ("denied_then_target_valid", "denied_then_stale", "not_denied", "no_related_followup")
    for method in EXTERNAL:
        selected = [row for row in output if row["method"] == method]
        counts = Counter(row["followup_class"] for row in selected)
        for label in classes:
            summary.append({
                "method": method, "followup_class": label,
                "count": counts[label], "denominator": len(selected),
                "proportion": counts[label] / len(selected) if selected else "",
            })
    lines = ["# Query follow-up examples", "",
             "Zero-call replay over first-run external-agent logs. Up to two earliest events per observed method/class.", ""]
    for (method, label), values in sorted(snippets.items()):
        lines.extend((f"## {method}: {label}", ""))
        for value in values:
            lines.append(f"- `{value['condition']} / {value['world']} / {value['task_id']} / event {value['event_index']}`")
            lines.append(f"  `{value['excerpt_json']}`")
        lines.append("")
    return output, summary, "\n".join(lines)


def pricing_fit() -> dict[str, Any]:
    observations = []
    by_source: dict[str, dict[str, Any]] = {}
    for path in PRICE_INPUTS:
        rows = jsonl(path)
        source = str(path.relative_to(WEBCRAFT))
        for row in rows:
            if row.get("model", row.get("server_model")) != "glm-5.2":
                continue
            usage = row["usage"]
            observations.append((
                int(usage.get("prompt_tokens", 0)) - int(usage.get("cached_tokens", 0)),
                int(usage.get("cached_tokens", 0)), int(usage.get("completion_tokens", 0)),
                float(row["cost"]), str(row["currency"]), str(row["timestamp"]) if "timestamp" in row else "",
                source,
            ))
        chosen = [row for row in observations if row[-1] == source]
        by_source[source] = {
            "calls": len(chosen), "cost": sum(row[3] for row in chosen),
            "uncached_input_tokens": sum(row[0] for row in chosen),
            "cached_input_tokens": sum(row[1] for row in chosen),
            "output_tokens": sum(row[2] for row in chosen),
        }
    currencies = {row[4] for row in observations}
    if currencies != {"CNY"}:
        raise RuntimeError(f"mixed or missing billing currency: {currencies}")
    matrix = np.asarray([[row[0], row[1], row[2]] for row in observations], dtype=float)
    costs = np.asarray([row[3] for row in observations], dtype=float)
    rates = np.linalg.lstsq(matrix, costs, rcond=None)[0]
    residual = matrix @ rates - costs
    return {
        "fit": "least_squares_through_origin_with_reported_cache_tokens",
        "currency": "CNY", "observations": len(observations),
        "uncached_input_per_million": float(rates[0] * 1_000_000),
        "cached_input_per_million": float(rates[1] * 1_000_000),
        "output_per_million": float(rates[2] * 1_000_000),
        "rmse_cny": float(np.sqrt(np.mean(residual ** 2))),
        "max_abs_residual_cny": float(np.max(np.abs(residual))),
        "sources": by_source,
        "claims_cache_billing_ledger_found": False,
        "claims_cache_note": "Frozen claim-cache files retain claims but no recoverable token/cost ledger; llm_prior is reported separately and is not relabelled as claim-cache construction.",
    }


def a7_records() -> list[dict[str, Any]]:
    indexed = {}
    for root in (A7_BASE / "run_supervised", A7_BASE / "recovery_v2", A7_BASE / "S"):
        for path in root.glob("**/episodes.jsonl"):
            for row in jsonl(path):
                key = (row["method"], row["condition"], row["task_id"], row["world"], row["source_condition"])
                indexed[key] = row
    if len(indexed) != 160:
        raise RuntimeError(f"expected 160 unique A7 episodes, got {len(indexed)}")
    return list(indexed.values())


def token_scope(name: str, records: list[dict[str, Any]], pricing: dict[str, Any]) -> dict[str, Any]:
    input_tokens = sum(int(row.get("input_tokens", 0)) for row in records)
    output_tokens = sum(int(row.get("output_tokens", 0)) for row in records)
    # Episode exports do not retain cached-input totals. The no-cache estimate
    # is therefore primary and the all-input-cached value is a mechanical lower bound.
    high = (input_tokens * pricing["uncached_input_per_million"]
            + output_tokens * pricing["output_per_million"]) / 1_000_000
    low = (input_tokens * pricing["cached_input_per_million"]
           + output_tokens * pricing["output_per_million"]) / 1_000_000
    return {"scope": name, "episodes": len(records), "input_tokens": input_tokens,
            "output_tokens": output_tokens, "estimated_cost_cny_no_cache": high,
            "estimated_cost_cny_all_input_cached_lower_bound": low}


def cost_estimate() -> dict[str, Any]:
    pricing = pricing_fit()
    first_run = []
    for path in EXTERNAL.values():
        first_run.extend(jsonl(path))
    scopes = [token_scope("external_first_run_five_methods", first_run, pricing),
              token_scope("A7_diagnostic_controls", a7_records(), pricing)]
    a7 = a7_records()
    totals = np.asarray([int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
                         for row in a7], dtype=float)
    episode_costs = np.asarray([
        (int(row.get("input_tokens", 0)) * pricing["uncached_input_per_million"]
         + int(row.get("output_tokens", 0)) * pricing["output_per_million"]) / 1_000_000
        for row in a7
    ])
    p10, p90 = np.quantile(totals, [0.10, 0.90])
    cost_p10, cost_p90 = np.quantile(episode_costs, [0.10, 0.90])
    # The primary-run range excludes the one additional boundary query because
    # no comparable historical prompt exists; that omission is explicit.
    scopes.append({
        "scope": "A8_pre_run_primary_loop_range",
        "episodes": 416, "a7_per_episode_total_token_p10": float(p10),
        "a7_per_episode_total_token_p90": float(p90),
        "estimated_total_tokens_low": float(416 * p10),
        "estimated_total_tokens_high": float(416 * p90),
        "a7_per_episode_no_cache_cost_p10_cny": float(cost_p10),
        "a7_per_episode_no_cache_cost_p90_cny": float(cost_p90),
        "estimated_primary_loop_cost_cny_low": float(416 * cost_p10),
        "estimated_primary_loop_cost_cny_high": float(416 * cost_p90),
        "boundary_queries": 416, "boundary_query_cost_included": False,
    })
    return {"schema_version": 1, "estimated": True, "pricing": pricing, "scopes": scopes,
            "uncertainty": [
                "Claim-cache construction has no recoverable billing ledger.",
                "Historical episode exports omit cached-input token totals, so the reported scope estimates are bounds.",
                "A8 boundary-query token usage is unknown before execution and excluded from the pre-run range.",
                "No currency conversion is applied.",
            ]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    events, summary, examples = classify_queries()
    write_csv(OUT / "query_followup_events.csv", events)
    write_csv(OUT / "query_followup_summary.csv", summary)
    (OUT / "query_followup_examples.md").write_text(examples)
    estimate = cost_estimate()
    (OUT / "cost_estimate_pre_run.json").write_text(json.dumps(estimate, indent=2, sort_keys=True) + "\n")
    lines = ["# A8 pre-run cost estimate", "", "All monetary values are estimates in CNY.", "",
             f"The same-gateway fit uses {estimate['pricing']['observations']} charged calls. Fitted rates per million tokens are "
             f"{estimate['pricing']['uncached_input_per_million']:.6g} uncached input, "
             f"{estimate['pricing']['cached_input_per_million']:.6g} cached input, and "
             f"{estimate['pricing']['output_per_million']:.6g} output. RMSE is "
             f"{estimate['pricing']['rmse_cny']:.3g} CNY.", "",
             "The frozen claim cache has no recoverable billing ledger. The knowledge-package (`llm_prior`) and motivation ledgers are named separately rather than treated as claim-cache records.", ""]
    for row in estimate["scopes"]:
        lines.append(f"- `{row['scope']}`: {json.dumps(row, sort_keys=True)}")
    (OUT / "COST_ESTIMATE.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"query_events": len(events), "query_summary": summary,
                      "pricing": estimate["pricing"], "scopes": estimate["scopes"]}, sort_keys=True))


if __name__ == "__main__":
    main()
