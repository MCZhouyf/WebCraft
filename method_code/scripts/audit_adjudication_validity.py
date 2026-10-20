"""Audit whether saved execution-time adjudications were controlled tests."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
OUT = ROOT / "reports/main/post_review/adjudication_validity"
SEMANTIC = {"mine", "craft_grid", "smelt", "check_name"}
CATEGORIES = (
    "controlled_confirmed",
    "controlled_with_delta",
    "controlled_rejected",
    "uncontrolled",
    "indeterminate",
)
PUBLIC_ERRORS = {
    "unknown resource node",
    "resource node is empty",
    "layout must contain exactly 9 cells",
    "crafting table unavailable",
    "furnace unavailable",
    "missing smelting input or fuel",
    "smelting timeout",
    "n must be a positive integer",
    "episode closed",
    "invalid item identifier",
    "missing ingredient",
    "action could not be completed",
}
INPUT_HASHES = {
    "artifacts/post_review/external_agents/final/direct_llm_assembled/episodes.jsonl":
        "5c1901cb3da980ee3f6b89cabdd106e3c67573e3d93d81820918e4ca4af95c29",
    "artifacts/post_review/external_agents/final/react_assembled/episodes.jsonl":
        "b57d11ef341c832470e3fa37995290a3db45c1c125c5d7143bc6670d57b09c11",
    "artifacts/post_review/external_agents/final/reflexion_assembled/episodes.jsonl":
        "af894b0a1917f1813d26cde830435f5efa5b3984fae3827ebe9dc37e69e9e447",
    "artifacts/post_review/external_agents/final/xenon_assembled/episodes.jsonl":
        "ef8eea2f300da88f4534004d3192d52fd93d5b2978f4d8cc4f1e5dae7e997332",
    "artifacts/post_review/external_agents/final/adam/episodes.jsonl":
        "69a82f35a2c49bd5b4b6df6a045e98447fe39f1be7ba3d2b2648b7c7d6d89c59",
    "artifacts/main/all_public/episodes.jsonl":
        "a73a99871810cf416ebbb33588cca3185aa6621ae848b45c2d212ce1c9107134",
    "artifacts/main/llm_only/episodes.jsonl":
        "b0e87c93a7f8b25d15db965e7f406b416c6e0910b3b794aae96c1c69d36af4a1",
    "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl":
        "32555ecac0ca92147dbcc1055b936bb1a82d63fecb8f798686ecea5fcc969fc7",
    "artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl":
        "ec09d6d9d835e824bad4fa8dbef4f5c0cb93a636ae8fda5c0c85384c772a1652",
}
EXTERNAL = {
    "direct_llm": "direct_llm_assembled",
    "react": "react_assembled",
    "reflexion": "reflexion_assembled",
    "xenon": "xenon_assembled",
    "adam": "adam",
}
TOOL_RANK = {
    "wooden_pickaxe": 0,
    "golden_pickaxe": 0,
    "stone_pickaxe": 1,
    "iron_pickaxe": 2,
    "diamond_pickaxe": 3,
    "netherite_pickaxe": 4,
}
NODE_TOOL_RANK = {
    "iron": 1, "iron_ore": 1,
    "gold": 1, "gold_ore": 1,
    "lapis": 1, "lapis_ore": 1,
    "diamond": 2, "diamond_ore": 2,
    "redstone": 2, "redstone_ore": 2,
    "stone": 0, "cobblestone": 0, "coal": 0, "coal_ore": 0,
    "log": -1, "oak_log": -1, "sand": -1, "gravel": -1,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def clean_name(value: Any) -> Any:
    return value.removeprefix("minecraft:") if isinstance(value, str) else value


def items_counter(items: Any) -> Counter[str]:
    result: Counter[str] = Counter()
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            count = item.get("count", 1)
            if isinstance(count, int) and not isinstance(count, bool):
                result[clean_name(item["name"])] += count
    return +result


def flow(event: dict[str, Any]) -> tuple[Counter[str], Counter[str]]:
    value = event.get("item_flow") or {}
    return items_counter(value.get("consumed", [])), items_counter(value.get("produced", []))


def apply_flow(inventory: Counter[str], event: dict[str, Any]) -> Counter[str]:
    consumed, produced = flow(event)
    after = inventory.copy()
    after.subtract(consumed)
    after.update(produced)
    if any(value < 0 for value in after.values()):
        raise ValueError("item flow consumes unavailable inventory")
    return +after


def reverse_flow(inventory: Counter[str], event: dict[str, Any]) -> Counter[str]:
    consumed, produced = flow(event)
    before = inventory.copy()
    before.subtract(produced)
    before.update(consumed)
    if any(value < 0 for value in before.values()):
        raise ValueError("item flow produces unavailable reverse inventory")
    return +before


def inventory_snapshots(events: list[dict[str, Any]]) -> tuple[list[Counter[str] | None], str]:
    anchors = [
        (index, items_counter(event.get("result")))
        for index, event in enumerate(events)
        if event.get("method") == "inventory" and isinstance(event.get("result"), list)
    ]
    if not anchors:
        return [None] * len(events), "no_inventory_anchor"
    anchor_index, anchor_inventory = anchors[0]
    before: list[Counter[str] | None] = [None] * len(events)
    before[anchor_index] = anchor_inventory.copy()
    try:
        state = anchor_inventory.copy()
        for index in range(anchor_index - 1, -1, -1):
            state = reverse_flow(state, events[index])
            before[index] = state.copy()
        state = anchor_inventory.copy()
        for index in range(anchor_index + 1, len(events)):
            before[index] = state.copy()
            state = apply_flow(state, events[index])
            if events[index].get("method") == "inventory" and isinstance(events[index].get("result"), list):
                observed = items_counter(events[index]["result"])
                if state != observed:
                    return [None] * len(events), f"inventory_anchor_mismatch_at_{index}"
                state = observed
    except ValueError as error:
        return [None] * len(events), str(error)
    return before, "ok"


def result_error(event: dict[str, Any]) -> str:
    result = event.get("result")
    return str(result.get("error", "")) if isinstance(result, dict) else ""


def required_inputs(event: dict[str, Any], inventory: Counter[str]) -> tuple[list[str], str]:
    method = event.get("method")
    params = event.get("params") or {}
    if method == "craft_grid":
        layout = params.get("layout")
        if not isinstance(layout, list) or len(layout) != 9:
            return [], "invalid_layout"
        needed = Counter()
        for cell in layout:
            if cell is None:
                continue
            if isinstance(cell, str):
                needed[clean_name(cell)] += 1
            elif isinstance(cell, dict) and isinstance(cell.get("name"), str):
                needed[clean_name(cell["name"])] += int(cell.get("count", 1))
            else:
                return [], "invalid_layout"
        missing = [f"missing_input:{name}:{count - inventory[name]}"
                   for name, count in sorted(needed.items()) if inventory[name] < count]
        return missing, "ready" if not missing else "missing_input"
    if method == "smelt":
        input_name, fuel_name = clean_name(params.get("input")), clean_name(params.get("fuel"))
        n = params.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return [], "invalid_n"
        missing = []
        if not isinstance(input_name, str) or inventory[input_name] < n:
            short = n - inventory[input_name] if isinstance(input_name, str) else n
            missing.append(f"missing_input:{input_name}:{short}")
        fuel_count = math.ceil(n / 8) if fuel_name == "coal" else 1
        if not isinstance(fuel_name, str) or inventory[fuel_name] < fuel_count:
            short = fuel_count - inventory[fuel_name] if isinstance(fuel_name, str) else fuel_count
            missing.append(f"missing_fuel:{fuel_name}:{short}")
        kinds = {entry.split(":", 1)[0] for entry in missing}
        return missing, "+".join(sorted(kinds)) if missing else "ready"
    if method == "mine":
        node_id = clean_name(params.get("nodeId"))
        if not isinstance(node_id, str):
            return [], "unknown_node_family"
        family = re.sub(r"_\d+$", "", node_id)
        required = NODE_TOOL_RANK.get(family)
        if required is None:
            return [], "unknown_node_family"
        held = max((rank for name, rank in TOOL_RANK.items() if inventory[name] > 0), default=-1)
        if held < required:
            return [f"missing_tool:required_rank_{required}:held_rank_{held}"], "missing_tool"
        return [], "ready"
    if method == "check_name":
        return [], "ready"
    return [], "unknown_method"


def expected_value(trace: dict[str, Any] | None) -> tuple[Any, str]:
    if not trace:
        return None, "not_logged"
    raw = trace.get("action_signature")
    if not isinstance(raw, str):
        return None, "malformed_signature"
    try:
        signature = json.loads(raw)
    except json.JSONDecodeError:
        return None, "malformed_signature"
    if not isinstance(signature, list) or len(signature) < 3:
        return None, "malformed_signature"
    return signature[2], "logged"


def result_delta_consistent(event: dict[str, Any]) -> bool:
    produced = flow(event)[1]
    result = event.get("result")
    if not isinstance(result, dict):
        return event.get("method") == "check_name"
    if event.get("method") == "craft_grid":
        product = result.get("product")
        return isinstance(product, dict) and produced == items_counter([product])
    if event.get("method") in {"mine", "smelt"}:
        return isinstance(result.get("items"), list) and produced == items_counter(result["items"])
    if event.get("method") == "check_name":
        return isinstance(result.get("resolvable"), bool) and not produced
    return False


def expected_matches(event: dict[str, Any], expected: Any) -> bool:
    produced = flow(event)[1]
    if isinstance(expected, str):
        return produced[clean_name(expected)] >= 1
    if isinstance(expected, dict) and isinstance(expected.get("name"), str):
        count = expected.get("count", 1)
        return isinstance(count, int) and produced[clean_name(expected["name"])] >= count
    if isinstance(expected, bool) and event.get("method") == "check_name":
        result = event.get("result")
        return isinstance(result, dict) and result.get("resolvable") is expected
    return result_delta_consistent(event)


def mechanism_family(method: str) -> str:
    return {
        "mine": "ore_drop",
        "craft_grid": "recipe",
        "smelt": "smelting",
        "check_name": "name",
    }.get(method, "other")


def classify(event: dict[str, Any], inventory: Counter[str] | None,
             inventory_status: str, trace: dict[str, Any] | None) -> dict[str, Any]:
    error = result_error(event)
    expected, expected_status = expected_value(trace)
    if inventory is None:
        return {
            "classification": "indeterminate", "subreason": inventory_status,
            "readiness": "unknown", "missing": [], "error": error,
            "expected": expected, "expected_status": expected_status,
        }
    missing, readiness = required_inputs(event, inventory)
    success = bool(event.get("success"))
    # These errors are decided before an inventory/tool prerequisite is consulted.
    precheck_rejections = {
        "unknown resource node", "resource node is empty",
        "layout must contain exactly 9 cells", "invalid item identifier",
        "n must be a positive integer", "episode closed",
    }
    if not success and error in precheck_rejections:
        category, subreason = "controlled_rejected", "public_precheck_rejection"
    elif not success and missing:
        category = "uncontrolled"
        subreason = "+".join(sorted({entry.split(":", 1)[0] for entry in missing}))
    elif success:
        if expected_status == "malformed_signature" or not result_delta_consistent(event):
            category, subreason = "indeterminate", "malformed_or_inconsistent_public_delta"
        elif expected_status == "logged" and not expected_matches(event, expected):
            category, subreason = "controlled_with_delta", "delta_differs_from_logged_promise"
        else:
            category, subreason = "controlled_confirmed", "public_delta_confirms_operation"
    elif error not in PUBLIC_ERRORS and not (
            isinstance(event.get("result"), dict) and event["result"].get("product") is None):
        category, subreason = "indeterminate", "unrecognized_public_error"
    elif error == "action could not be completed":
        category, subreason = "indeterminate", "generic_public_error"
    else:
        category, subreason = "controlled_rejected", "ready_public_rejection"
    return {
        "classification": category, "subreason": subreason,
        "readiness": readiness, "missing": missing, "error": error,
        "expected": expected, "expected_status": expected_status,
    }


def common_record(scope: str, reading_mode: str, episode: dict[str, Any],
                  event: dict[str, Any], event_index: int, semantic_index: int,
                  inventory: Counter[str] | None, inventory_status: str,
                  trace: dict[str, Any] | None = None) -> dict[str, Any]:
    result = classify(event, inventory, inventory_status, trace)
    return {
        "scope": scope,
        "reading_mode": reading_mode,
        "method": episode["method"],
        "condition": episode["condition"],
        "world": episode["world"],
        "task_id": episode["task_id"],
        "seed": episode["seed"],
        "event_index": event_index,
        "semantic_index": semantic_index,
        "sequence": event.get("sequence", ""),
        "action_method": event["method"],
        "mechanism_family": mechanism_family(event["method"]),
        "mechanism_id": trace.get("mechanism_id", "") if trace else "",
        "intent": event.get("intent", ""),
        "success": bool(event.get("success")),
        "classification": result["classification"],
        "subreason": result["subreason"],
        "readiness": result["readiness"],
        "missing_prerequisites": json.dumps(result["missing"], separators=(",", ":")),
        "inventory_before": json.dumps(dict(sorted((inventory or {}).items())), separators=(",", ":")),
        "params": json.dumps(event.get("params", {}), sort_keys=True, separators=(",", ":")),
        "public_error": result["error"],
        "item_flow": json.dumps(event.get("item_flow", {}), sort_keys=True, separators=(",", ":")),
        "expected_status": result["expected_status"],
        "expected": json.dumps(result["expected"], sort_keys=True, separators=(",", ":")),
        "observed": trace.get("observed", "") if trace else "",
        "action_signature": trace.get("action_signature", "") if trace else "",
        "previous_40_of_52_scope": False,
    }


def semantic_positions(events: list[dict[str, Any]]) -> dict[int, int]:
    return {event_index: semantic_index for semantic_index, event_index in enumerate(
        index for index, event in enumerate(events) if event.get("method") in SEMANTIC
    )}


def audit_external() -> tuple[list[dict[str, Any]], dict[str, str]]:
    records: list[dict[str, Any]] = []
    action_hashes: dict[str, str] = {}
    for method, directory in EXTERNAL.items():
        episodes = jsonl(ROOT / f"artifacts/post_review/external_agents/final/{directory}/episodes.jsonl")
        if len(episodes) != 110:
            raise RuntimeError(f"{method}: expected 110 episodes, got {len(episodes)}")
        for episode in episodes:
            path = ROOT / episode["action_log"]
            action_hashes[str(path.relative_to(ROOT))] = sha256(path)
            events = jsonl(path)
            snapshots, inventory_status = inventory_snapshots(events)
            positions = semantic_positions(events)
            for event_index, semantic_index in positions.items():
                event = events[event_index]
                if not event.get("success"):
                    records.append(common_record(
                        "external_failed_semantic", "all_public", episode, event,
                        event_index, semantic_index, snapshots[event_index], inventory_status,
                    ))
                if (method == "adam" and event.get("intent") == "probe"
                        and (event.get("cost") or {}).get("primary", 0) > 0):
                    records.append(common_record(
                        "external_adam_paid_probe", "all_public", episode, event,
                        event_index, semantic_index, snapshots[event_index], inventory_status,
                    ))
    adam = [row for row in records if row["scope"] == "external_adam_paid_probe"]
    if len(adam) != 65:
        raise RuntimeError(f"expected 65 paid ADAM probes, got {len(adam)}")
    return records, action_hashes


def aligned_traces(episode: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    events = episode["events"]
    positions = semantic_positions(events)
    traces = episode.get("policy_trace", [])
    if len(positions) != len(traces):
        raise RuntimeError(
            f"semantic/trace mismatch for {episode['method']} {episode['condition']} "
            f"{episode['task_id']} seed {episode['seed']}: {len(positions)} != {len(traces)}"
        )
    return traces, positions


def audit_main() -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    retirement_rows: list[dict[str, Any]] = []
    for mode in ("all_public", "llm_only"):
        episodes = jsonl(ROOT / f"artifacts/main/{mode}/episodes.jsonl")
        for episode in episodes:
            if episode["method"] not in {"verify_all", "arbiter"}:
                continue
            events = episode["events"]
            snapshots, inventory_status = inventory_snapshots(events)
            traces, positions = aligned_traces(episode)
            for event_index, semantic_index in positions.items():
                event, trace = events[event_index], traces[semantic_index]
                if episode["method"] == "verify_all" and event.get("intent") == "probe":
                    records.append(common_record(
                        "main_verify_all_probe", mode, episode, event, event_index,
                        semantic_index, snapshots[event_index], inventory_status, trace,
                    ))
                if (episode["method"] == "arbiter" and trace.get("intent") == "probe"
                        and trace.get("observed") is False):
                    row = common_record(
                        "main_arbiter_retirement", mode, episode, event, event_index,
                        semantic_index, snapshots[event_index], inventory_status, trace,
                    )
                    row["previous_40_of_52_scope"] = bool(
                        mode == "all_public"
                        and trace.get("mechanism_id") in set(episode.get("conflict_mechanism_ids", []))
                    )
                    records.append(row)
                    retirement_rows.append(row)
    collapse_fields = (
        "reading_mode", "condition", "world", "task_id", "mechanism_id", "action_signature",
    )
    collapsed = {tuple(row[field] for field in collapse_fields) for row in retirement_rows}
    previous = [row for row in retirement_rows if row["previous_40_of_52_scope"]]
    previous_collapsed = {
        tuple(row[field] for field in collapse_fields): row for row in previous
    }
    coverage = {
        "all_retirement_records": len(retirement_rows),
        "all_retirements_seed_collapsed": len(collapsed),
        "previous_audit_raw_records": len(previous),
        "previous_audit_seed_collapsed": len(previous_collapsed),
        "previous_scope_uncontrolled_raw": sum(
            row["classification"] == "uncontrolled" for row in previous
        ),
        "previous_scope_uncontrolled_seed_collapsed": sum(
            row["classification"] == "uncontrolled" for row in previous_collapsed.values()
        ),
    }
    if coverage["previous_audit_raw_records"] != 104 or coverage["previous_audit_seed_collapsed"] != 52:
        raise RuntimeError(f"previous 40/52 scope changed: {coverage}")
    return records, coverage


def audit_positive_control() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for mode in ("all_public", "llm_only"):
        episodes = jsonl(ROOT / f"artifacts/post_run_probe_then_rag/{mode}/episodes.jsonl")
        for episode in episodes:
            if episode["method"] != "probe_then_rag":
                continue
            events = episode["events"]
            snapshots, inventory_status = inventory_snapshots(events)
            positions = semantic_positions(events)
            for event_index, semantic_index in positions.items():
                event = events[event_index]
                if event.get("intent") == "probe":
                    records.append(common_record(
                        "probe_then_rag_version_probe", mode, episode, event, event_index,
                        semantic_index, snapshots[event_index], inventory_status,
                    ))
    return records


def aggregate_rows(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups[tuple(row[key] for key in keys)].append(row)
    output = []
    for values, rows in sorted(groups.items()):
        item = dict(zip(keys, values))
        item["records"] = len(rows)
        for category in CATEGORIES:
            count = sum(row["classification"] == category for row in rows)
            item[f"{category}_count"] = count
            item[f"{category}_proportion"] = count / len(rows)
        for shortage in ("missing_tool", "missing_input", "missing_fuel"):
            item[f"uncontrolled_{shortage}_count"] = sum(
                row["classification"] == "uncontrolled" and shortage in row["subreason"]
                for row in rows
            )
        output.append(item)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing empty output: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def proportion(rows: Iterable[dict[str, Any]]) -> float:
    values = list(rows)
    return sum(row["classification"] == "uncontrolled" for row in values) / len(values) if values else math.nan


def fmt(value: float) -> str:
    return "NA" if math.isnan(value) else f"{value:.4f}"


def gate(records: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    external = [row for row in records if row["scope"] == "external_failed_semantic"]
    external_rates = {
        method: proportion(row for row in external if row["method"] == method)
        for method in EXTERNAL
    }
    outside = [row for row in external if row["mechanism_family"] != "ore_drop"]
    outside_rates = {
        family: proportion(row for row in outside if row["mechanism_family"] == family)
        for family in ("recipe", "smelting", "name", "other")
        if any(row["mechanism_family"] == family for row in outside)
    }
    high_external = sum(value >= 0.50 for value in external_rates.values())
    high_outside = any(value >= 0.30 for value in outside_rates.values())
    any_ore = any(
        row["mechanism_family"] == "ore_drop" and row["classification"] == "uncontrolled"
        for row in records
    )
    if high_external >= 3 and high_outside:
        decision = "general phenomenon"
    elif any_ore and all(value < 0.30 for value in outside_rates.values()):
        decision = "mechanism-local phenomenon"
    elif sum(value < 0.30 for value in external_rates.values()) >= 3:
        decision = "implementation-local problem"
    else:
        decision = "inconclusive"
    return decision, {
        "external_method_uncontrolled": external_rates,
        "external_outside_ore_family_uncontrolled": outside_rates,
        "external_methods_at_or_above_0_50": high_external,
        "outside_ore_family_at_or_above_0_30": high_outside,
    }


def main() -> None:
    for relative, expected in INPUT_HASHES.items():
        actual = sha256(ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"frozen input changed: {relative}: {actual} != {expected}")
    if sha256(ROOT / "config/webcraft_pin.json") != "697b81a8d33b27ecef36cffcff082a9ea20eed0548799306a8cd98741199389d":
        raise RuntimeError("webcraft pin changed")
    if sha256(WEBCRAFT / "sim/semantic_agent.py") != "f9c870e59d52dd5e7af190d476375699afcca41499b4a80f9687ea631588f3cc":
        raise RuntimeError("pinned public semantic interface changed")

    external_records, action_hashes = audit_external()
    main_records, retirement_coverage = audit_main()
    positive_records = audit_positive_control()
    records = external_records + main_records + positive_records

    expected_scope_counts = {
        "external_failed_semantic": 477,
        "external_adam_paid_probe": 65,
        "main_verify_all_probe": 1904,
        "main_arbiter_retirement": 870,
        "probe_then_rag_version_probe": 5440,
    }
    actual_scope_counts = Counter(row["scope"] for row in records)
    if actual_scope_counts != Counter(expected_scope_counts):
        raise RuntimeError(f"audited scope changed: {actual_scope_counts} != {expected_scope_counts}")

    per_method = aggregate_rows(records, ("scope", "reading_mode", "method"))
    per_mechanism = aggregate_rows(
        records, ("scope", "reading_mode", "method", "mechanism_family")
    )
    decision, gate_details = gate(records)
    external_rates = gate_details["external_method_uncontrolled"]
    verify = [row for row in records if row["scope"] == "main_verify_all_probe"]
    retire = [row for row in records if row["scope"] == "main_arbiter_retirement"]
    outside = [row for row in records
               if row["scope"] == "external_failed_semantic"
               and row["mechanism_family"] != "ore_drop"]
    verify_modes = {mode: proportion(row for row in verify if row["reading_mode"] == mode)
                    for mode in ("all_public", "llm_only")}
    retire_modes = {mode: proportion(row for row in retire if row["reading_mode"] == mode)
                    for mode in ("all_public", "llm_only")}

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "action_audit.csv", records)
    write_csv(OUT / "per_method.csv", per_method)
    write_csv(OUT / "per_mechanism.csv", per_mechanism)
    report_lines = [
        "# Adjudication validity audit",
        "",
        "1. External five-method failed semantic actions have uncontrolled proportions in "
        f"[{min(external_rates.values()):.4f}, {max(external_rates.values()):.4f}] "
        f"(Direct={external_rates['direct_llm']:.4f}, ReAct={external_rates['react']:.4f}, "
        f"Reflexion={external_rates['reflexion']:.4f}, XENON={external_rates['xenon']:.4f}, "
        f"ADAM={external_rates['adam']:.4f}).",
        "2. Verify-All probe uncontrolled proportion is "
        f"{proportion(verify):.4f} pooled (all_public={verify_modes['all_public']:.4f}, "
        f"llm_only={verify_modes['llm_only']:.4f}); original-ARBITER retirement uncontrolled "
        f"proportion is {proportion(retire):.4f} pooled "
        f"(all_public={retire_modes['all_public']:.4f}, llm_only={retire_modes['llm_only']:.4f}), "
        f"over {retirement_coverage['all_retirement_records']} raw / "
        f"{retirement_coverage['all_retirements_seed_collapsed']} seed-collapsed retirements; "
        f"the previous audit covered {retirement_coverage['previous_audit_raw_records']} raw / "
        f"{retirement_coverage['previous_audit_seed_collapsed']} collapsed records, of which the "
        f"current classifier marks {retirement_coverage['previous_scope_uncontrolled_raw']} raw / "
        f"{retirement_coverage['previous_scope_uncontrolled_seed_collapsed']} collapsed as uncontrolled.",
        "3. Outside-ore mechanisms have uncontrolled proportion "
        f"{proportion(outside):.4f}, so uncontrolled events are not confined to ore-drop/pickaxe-tier "
        f"preconditions; G1 = {decision}.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(report_lines) + "\n")
    provenance = {
        "preregistration_commits": ["fc066f5", "4c242d4"],
        "input_sha256": INPUT_HASHES,
        "external_action_log_manifest_sha256": hashlib.sha256(
            "".join(f"{path} {digest}\n" for path, digest in sorted(action_hashes.items())).encode()
        ).hexdigest(),
        "external_action_log_count": len(action_hashes),
        "scope_counts": dict(sorted(actual_scope_counts.items())),
        "retirement_coverage": retirement_coverage,
        "gate": {"decision": decision, **gate_details},
        "llm_calls": 0,
        "new_episodes": 0,
        "hidden_truth_loaded": False,
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "scope_counts": provenance["scope_counts"],
        "retirement_coverage": retirement_coverage,
        "external_uncontrolled_range": [min(external_rates.values()), max(external_rates.values())],
        "verify_all_uncontrolled": proportion(verify),
        "arbiter_retirement_uncontrolled": proportion(retire),
        "outside_ore_uncontrolled": proportion(outside),
        "G1": decision,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
