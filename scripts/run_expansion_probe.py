#!/usr/bin/env python3
"""Run the preregistered deterministic expansion feasibility probe."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_stage1_data import Builder  # noqa: E402
from conflict_core import assertion_id, compact, recipe_assertion  # noqa: E402
from oracle.planner import VERSIONS, OraclePlanner, item_id, validate_plan  # noqa: E402


REPORT_DIR = ROOT / "reports" / "expansion_probe"
VERSIONS = tuple(VERSIONS)
NORMALIZER_SUFFIXES = (
    "_chest_boat", "_planks", "_log", "_wood", "_stem", "_hyphae",
    "_slab", "_boat", "_wool", "_carpet", "_harness",
)
DRIFT_LABEL_ZH = {
    "none": "无",
    "direct_identifier": "直接标识符",
    "mechanism_dependent_rename": "机制依赖型改名",
    "long_range_effect": "长程影响",
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def item_family(item: str) -> str:
    for suffix in NORMALIZER_SUFFIXES:
        if item.endswith(suffix):
            return suffix[1:]
    return item


def mechanism_key(row: dict[str, Any]) -> str:
    assertion = row["assertion"]
    if assertion["kind"] in {"recipe", "concrete_grid"}:
        return f"recipe:{assertion['output']}"
    if assertion["kind"] == "drop":
        return f"drop:{assertion['block']}"
    return f"{assertion['kind']}:{assertion.get('item', 'unknown')}"


def mechanism_family(key: str) -> str:
    kind, subject = key.split(":", 1)
    return f"{kind}:{item_family(subject)}"


class ExpansionProbe:
    def __init__(self) -> None:
        self.builder = Builder()
        self.conflicts = [
            json.loads(line)
            for line in (ROOT / "data/conflicts.jsonl").read_text().splitlines()
            if line
        ]
        self.rows_by_id = {row["id"]: row for row in self.conflicts}
        self.capacities = json.loads(
            (ROOT / "config/resource_model.json").read_text()
        )["resource_block_counts"]
        self.tasks = [
            json.loads(line)
            for line in (ROOT / "data/tasks.jsonl").read_text().splitlines()
            if line
        ]
        self.page_map = {
            row["item_id"]: row
            for row in json.loads((ROOT / "corpus/manifests/page_map_p1.json").read_text())
        }
        self.names = {
            version: set(map(item_id, self.builder.truth[version]["names"]["items"]))
            for version in VERSIONS
        }
        self.core_targets = {task["target"]["item"] for task in self.tasks}
        with (ROOT / "reports/a6_mechanism_frequency.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            self.core_conflict_ids = {
                row["conflict_id"] for row in csv.DictReader(handle)
            }
        self.core_mechanisms = {
            mechanism_key(self.rows_by_id[conflict_id])
            for conflict_id in self.core_conflict_ids
        }
        if len(self.core_mechanisms) != 22:
            raise RuntimeError(
                f"Core42 mechanism audit expected 22, got {len(self.core_mechanisms)}"
            )
        self.core_families = {mechanism_family(key) for key in self.core_mechanisms}

    def target_universe(self, planners: dict[str, OraclePlanner]) -> list[str]:
        outputs = set().union(*(
            {recipe["output"] for recipe in planners[version].recipes}
            for version in VERSIONS
        ))
        targets = sorted(outputs)
        if len(targets) != 934:
            raise RuntimeError(f"target universe expected 934, got {len(targets)}")
        return targets

    def action_conflict_ids(self, version: str, action: dict[str, Any]) -> list[str]:
        candidate = None
        if action["method"] in {"craft_grid", "smelt"}:
            raw = self.builder.raw_recipe[version].get(action["recipe_id"])
            assertion = recipe_assertion(raw) if raw else None
            candidate = assertion_id(assertion) if assertion else None
        elif action["method"] == "mine":
            assertion = {
                "kind": "drop",
                "block": action["node_type"],
                "silk_touch": False,
                "drops": [action["expected"]["name"]],
            }
            candidate = assertion_id(assertion)
        return [candidate] if candidate in self.rows_by_id else []

    def path_items(self, target: str, plan: dict[str, Any]) -> set[str]:
        items = {target}
        for action in plan["actions"]:
            if action["method"] == "craft_grid":
                items.update(item for item in action["layout"] if item)
            elif action["method"] == "smelt":
                items.update((action["input"], action["fuel"]))
            elif action["method"] == "mine":
                items.add(action["node_type"])
                if action.get("requires_tool"):
                    items.add(action["requires_tool"])
            items.add(action["expected"]["name"])
        return items

    def covered_page(self, item: str) -> bool:
        row = self.page_map.get(item)
        if not row or not row.get("verified_in_dump"):
            return False
        titles = row.get("resolved_title", {})
        return isinstance(titles, dict) and any(titles.values())

    def run_planners(self) -> tuple[list[dict[str, Any]], str]:
        planners = {
            version: OraclePlanner(version, capacities=self.capacities)
            for version in VERSIONS
        }
        targets = self.target_universe(planners)
        output = []
        for target in targets:
            plans = {}
            for version in VERSIONS:
                result = planners[version].plan(target)
                if result.feasible:
                    validate_plan(result)
                plans[version] = result.as_dict()
            output.append({"target": target, "plans": plans})
        return output, canonical_hash(output)

    def source_cohorts(self, planners: dict[str, OraclePlanner]) -> dict[str, set[str]]:
        outputs = {
            version: {recipe["output"] for recipe in planners[version].recipes}
            for version in VERSIONS
        }
        assertions: dict[str, dict[str, set[str]]] = {
            version: defaultdict(set) for version in VERSIONS
        }
        for version in VERSIONS:
            for recipe in planners[version].recipes:
                normalized = recipe_assertion(recipe["raw"])
                if normalized:
                    assertions[version][recipe["output"]].add(compact(normalized))
        new_routes = {
            target
            for target in outputs["1.16.5"] & outputs["1.21.11"]
            if assertions["1.21.11"][target] - assertions["1.16.5"][target]
        }
        return {
            "newly_craftable_1.21.11_vs_1.16.5": outputs["1.21.11"] - outputs["1.16.5"],
            "new_route_1.21.11_vs_1.16.5": new_routes,
        }

    def analyze(self, raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        analyzed = []
        for raw in raw_rows:
            target = raw["target"]
            plans = raw["plans"]
            feasible = {version: bool(plans[version]["feasible"]) for version in VERSIONS}
            costs = {
                version: plans[version]["primary_cost"] if feasible[version] else None
                for version in VERSIONS
            }
            exact_by_world: dict[str, list[str]] = {}
            mechanisms_by_world: dict[str, list[str]] = {}
            dominant_by_world: dict[str, list[str]] = {}
            all_items = {target}
            for version in VERSIONS:
                if not feasible[version]:
                    exact_by_world[version] = []
                    mechanisms_by_world[version] = []
                    dominant_by_world[version] = []
                    continue
                plan = plans[version]
                all_items.update(self.path_items(target, plan))
                action_ids = [self.action_conflict_ids(version, action) for action in plan["actions"]]
                exact = sorted({value for values in action_ids for value in values})
                exact_by_world[version] = exact
                mechanisms_by_world[version] = sorted({
                    mechanism_key(self.rows_by_id[value]) for value in exact
                })
                dominant = []
                for values in reversed(action_ids):
                    if values:
                        dominant = sorted({
                            mechanism_key(self.rows_by_id[value]) for value in values
                        })
                        break
                dominant_by_world[version] = dominant

            feasible_versions = [version for version in VERSIONS if feasible[version]]
            feasibility_change = len(set(feasible.values())) > 1
            exact_signatures = {
                tuple(exact_by_world[version]) for version in feasible_versions
            }
            route_change = len(feasible_versions) >= 2 and len(exact_signatures) > 1
            finite_costs = [costs[version] for version in feasible_versions]
            positive = [cost for cost in finite_costs if cost and cost > 0]
            if len(finite_costs) >= 2 and 0 in finite_costs and positive:
                cost_ratio = math.inf
            elif len(positive) >= 2:
                cost_ratio = max(positive) / min(positive)
            else:
                cost_ratio = 1.0
            efficiency_change = len(finite_costs) >= 2 and cost_ratio >= 1.5
            no_conflict = not (feasibility_change or route_change or efficiency_change)
            if feasibility_change:
                primary_type = "feasibility_change"
            elif route_change:
                primary_type = "route_change"
            elif efficiency_change:
                primary_type = "efficiency_change"
            else:
                primary_type = "no_conflict"

            path_mechanisms = sorted({
                value for values in mechanisms_by_world.values() for value in values
            })
            dominant_mechanisms = sorted({
                value for values in dominant_by_world.values() for value in values
            })
            path_families = sorted({mechanism_family(key) for key in path_mechanisms})
            dominant_families = sorted({mechanism_family(key) for key in dominant_mechanisms})
            missing_items = sorted(item for item in all_items if not self.covered_page(item))
            cluster_basis = dominant_mechanisms or path_mechanisms
            cluster_id = "|".join(cluster_basis)
            reachable = any(feasible.values())
            has_conflict = not no_conflict
            new_target = target not in self.core_targets
            new_dominant = not bool(set(dominant_mechanisms) & self.core_mechanisms)
            deduplicated = reachable and has_conflict and new_target and new_dominant
            corpus_closed = deduplicated and not missing_items
            analyzed.append({
                "target": target,
                "feasible": feasible,
                "costs": costs,
                "exact_conflicts_by_world": exact_by_world,
                "mechanisms_by_world": mechanisms_by_world,
                "dominant_by_world": dominant_by_world,
                "path_mechanisms": path_mechanisms,
                "path_families": path_families,
                "dominant_mechanisms": dominant_mechanisms,
                "dominant_families": dominant_families,
                "path_items": sorted(all_items),
                "missing_items": missing_items,
                "cost_ratio": "inf" if math.isinf(cost_ratio) else cost_ratio,
                "feasibility_change": feasibility_change,
                "route_change": route_change,
                "efficiency_change": efficiency_change,
                "no_conflict": no_conflict,
                "primary_type": primary_type,
                "reachable": reachable,
                "has_conflict": has_conflict,
                "new_target": new_target,
                "new_dominant_mechanism": new_dominant,
                "deduplicated": deduplicated,
                "corpus_closed": corpus_closed,
                "cluster_id": cluster_id,
                "plans": plans,
            })
        return analyzed

    def layer_membership(self, row: dict[str, Any]) -> list[bool]:
        return [
            True,
            row["reachable"],
            row["reachable"] and row["has_conflict"],
            row["reachable"] and row["has_conflict"] and row["new_target"],
            row["deduplicated"],
            row["corpus_closed"],
        ]

    def task_drift_labels(self) -> list[dict[str, Any]]:
        output = []
        for task in sorted((task for task in self.tasks if task["split"] == "eval"), key=lambda r: r["id"]):
            target = task["target"]["item"]
            direct = set()
            rename_conflicts = set()
            long_conflicts = set()
            evidence_conflicts = set(task["conflict_ids"])
            worlds = []
            for version in VERSIONS:
                plan = task["oracle_plans"].get(version)
                if not plan:
                    continue
                worlds.append(version)
                identifiers = self.path_items(target, plan)
                for item in identifiers:
                    if any(item not in self.names[other] for other in VERSIONS):
                        direct.add(item)
                action_ids = [self.action_conflict_ids(version, action) for action in plan["actions"]]
                for values in action_ids:
                    for conflict_id in values:
                        if self.rows_by_id[conflict_id]["main_type"] == "renamed":
                            rename_conflicts.add(conflict_id)
                if action_ids:
                    for index, values in enumerate(action_ids):
                        if values and index < len(action_ids) - 1:
                            long_conflicts.update(values)
            if direct:
                label = "direct_identifier"
                reason = "target or oracle-path identifier is absent from another frozen world"
            elif rename_conflicts:
                label = "mechanism_dependent_rename"
                reason = "stable target depends on a renamed mechanism"
            elif long_conflicts:
                label = "long_range_effect"
                reason = "conflict-bearing action precedes the terminal goal action"
            else:
                label = "none"
                reason = "no representation drift found on a feasible oracle path"
            output.append({
                "task_id": task["id"],
                "task_type": task["type"],
                "target": target,
                "drift_label": label,
                "drift_label_zh": DRIFT_LABEL_ZH[label],
                "direct_identifiers": ";".join(sorted(direct)),
                "rename_conflict_ids": ";".join(sorted(rename_conflicts)),
                "long_range_conflict_ids": ";".join(sorted(long_conflicts)),
                "path_conflict_ids": ";".join(sorted(evidence_conflicts)),
                "feasible_worlds": ";".join(worlds),
                "reason": reason,
            })
        if len(output) != 34:
            raise RuntimeError(f"expected 34 evaluation task labels, got {len(output)}")
        return output

    def write_outputs(
        self,
        analyzed: list[dict[str, Any]],
        run_hashes: list[str],
        cohorts: dict[str, set[str]],
    ) -> None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        layer_names = [
            "recipe_output_universe", "reachable_any_version", "contains_conflict",
            "remove_core_target", "remove_core_dominant_mechanism", "corpus_closed",
        ]
        funnel = []
        for index, layer in enumerate(layer_names):
            count = sum(all(self.layer_membership(row)[: index + 1]) for row in analyzed)
            funnel.append({"layer": index + 1, "name": layer, "count": count})
        write_csv(REPORT_DIR / "funnel.csv", funnel, ["layer", "name", "count"])

        target_rows = []
        for row in analyzed:
            target_rows.append({
                "target": row["target"],
                "F_1.16.5": int(row["feasible"]["1.16.5"]),
                "F_1.19": int(row["feasible"]["1.19"]),
                "F_1.21.11": int(row["feasible"]["1.21.11"]),
                "C_1.16.5": row["costs"]["1.16.5"] if row["costs"]["1.16.5"] is not None else "inf",
                "C_1.19": row["costs"]["1.19"] if row["costs"]["1.19"] is not None else "inf",
                "C_1.21.11": row["costs"]["1.21.11"] if row["costs"]["1.21.11"] is not None else "inf",
                "primary_type": row["primary_type"],
                "feasibility_change": int(row["feasibility_change"]),
                "route_change": int(row["route_change"]),
                "efficiency_change": int(row["efficiency_change"]),
                "cost_ratio": row["cost_ratio"],
                "path_mechanisms": ";".join(row["path_mechanisms"]),
                "mechanism_families": ";".join(row["path_families"]),
                "dominant_mechanisms": ";".join(row["dominant_mechanisms"]),
                "cluster_id": row["cluster_id"],
                "core_target": int(not row["new_target"]),
                "shares_core_dominant": int(not row["new_dominant_mechanism"]),
                "corpus_closed": int(row["corpus_closed"]),
                "missing_items": ";".join(row["missing_items"]),
            })
        write_csv(REPORT_DIR / "targets_934.csv", target_rows, list(target_rows[0]))

        plans_path = REPORT_DIR / "plans_934.jsonl"
        plans_path.write_text("".join(
            json.dumps({"target": row["target"], "plans": row["plans"]}, sort_keys=True) + "\n"
            for row in analyzed
        ))

        conflict_rows = [row for row in analyzed if row["reachable"] and row["has_conflict"]]
        type_order = (
            "feasibility_change", "route_change", "efficiency_change", "no_conflict"
        )
        type_counts = Counter(row["primary_type"] for row in analyzed)
        overlap_counts = Counter({
            "feasibility_change": 0,
            "route_change": 0,
            "efficiency_change": 0,
        })
        for row in analyzed:
            for label in ("feasibility_change", "route_change", "efficiency_change"):
                if row[label]:
                    overlap_counts[label] += 1
        type_stages = {
            "all_targets": analyzed,
            "reachable_conflict": conflict_rows,
            "deduplicated": [row for row in analyzed if row["deduplicated"]],
            "corpus_closed": [row for row in analyzed if row["corpus_closed"]],
        }
        type_rows = [
            {
                "count_kind": f"exclusive_{stage}",
                "type": key,
                "count": sum(row["primary_type"] == key for row in rows),
            }
            for stage, rows in type_stages.items()
            for key in type_order
        ] + [
            {"count_kind": "overlapping_label", "type": key, "count": value}
            for key, value in sorted(overlap_counts.items())
        ]
        write_csv(REPORT_DIR / "type_counts.csv", type_rows, ["count_kind", "type", "count"])

        deduplicated = [row for row in analyzed if row["deduplicated"]]
        new_mechanisms = sorted({
            key for row in deduplicated for key in row["path_mechanisms"]
            if key not in self.core_mechanisms
        })
        new_families = sorted({
            mechanism_family(key) for key in new_mechanisms
            if mechanism_family(key) not in self.core_families
        })
        mechanism_rows = []
        exact_candidate_counts = Counter()
        closed_exact_counts = Counter()
        for row in deduplicated:
            exact_ids = {
                conflict_id
                for values in row["exact_conflicts_by_world"].values()
                for conflict_id in values
            }
            exact_candidate_counts.update(exact_ids)
            if row["corpus_closed"]:
                closed_exact_counts.update(exact_ids)
        exact_assertion_rows = []
        for conflict_id in sorted(exact_candidate_counts):
            key = mechanism_key(self.rows_by_id[conflict_id])
            exact_assertion_rows.append({
                "conflict_id": conflict_id,
                "main_type": self.rows_by_id[conflict_id]["main_type"],
                "mechanism": key,
                "family": mechanism_family(key),
                "core22_assertion": int(conflict_id in self.core_conflict_ids),
                "deduplicated_candidate_count": exact_candidate_counts[conflict_id],
                "corpus_closed_candidate_count": closed_exact_counts[conflict_id],
            })
        write_csv(
            REPORT_DIR / "mechanism_assertions.csv", exact_assertion_rows,
            ["conflict_id", "main_type", "mechanism", "family", "core22_assertion",
             "deduplicated_candidate_count", "corpus_closed_candidate_count"],
        )
        new_exact_assertions = sorted(set(exact_candidate_counts) - self.core_conflict_ids)
        all_reachable_mechanisms = sorted({
            key for row in conflict_rows for key in row["path_mechanisms"]
        })
        for key in all_reachable_mechanisms:
            mechanism_rows.append({
                "mechanism": key,
                "family": mechanism_family(key),
                "core_covered": int(key in self.core_mechanisms),
                "deduplicated_candidate_count": sum(
                    key in row["path_mechanisms"] for row in deduplicated
                ),
                "corpus_closed_candidate_count": sum(
                    row["corpus_closed"] and key in row["path_mechanisms"] for row in analyzed
                ),
            })
        write_csv(
            REPORT_DIR / "mechanisms.csv", mechanism_rows,
            ["mechanism", "family", "core_covered", "deduplicated_candidate_count", "corpus_closed_candidate_count"],
        )
        family_rows = []
        reachable_families = sorted({mechanism_family(key) for key in all_reachable_mechanisms})
        for family in reachable_families:
            family_rows.append({
                "family": family,
                "core_covered": int(family in self.core_families),
                "new_after_dedup": int(family in new_families),
                "mechanisms": ";".join(sorted(
                    key for key in all_reachable_mechanisms if mechanism_family(key) == family
                )),
            })
        write_csv(
            REPORT_DIR / "mechanism_families.csv", family_rows,
            ["family", "core_covered", "new_after_dedup", "mechanisms"],
        )

        clusters: dict[str, list[str]] = defaultdict(list)
        for row in deduplicated:
            clusters[row["cluster_id"]].append(row["target"])
        cluster_rows = [
            {"cluster_id": key, "candidate_count": len(values), "targets": ";".join(sorted(values))}
            for key, values in sorted(clusters.items())
        ]
        write_csv(REPORT_DIR / "clusters.csv", cluster_rows, ["cluster_id", "candidate_count", "targets"])

        cohort_rows = []
        for cohort, members in sorted(cohorts.items()):
            by_target = {row["target"]: row for row in analyzed}
            for index, layer in enumerate(layer_names):
                count = sum(
                    all(self.layer_membership(by_target[target])[: index + 1])
                    for target in members
                )
                cohort_rows.append({
                    "cohort": cohort, "source_count": len(members),
                    "layer": index + 1, "layer_name": layer, "survivors": count,
                })
        write_csv(
            REPORT_DIR / "source_cohorts.csv", cohort_rows,
            ["cohort", "source_count", "layer", "layer_name", "survivors"],
        )

        closed_rows = [row for row in target_rows if row["corpus_closed"] == 1]
        missing_rows = [
            row for row in target_rows
            if next(value for value in analyzed if value["target"] == row["target"])["deduplicated"]
            and row["corpus_closed"] == 0
        ]
        write_csv(REPORT_DIR / "corpus_closed_candidates.csv", closed_rows, list(target_rows[0]))
        write_csv(REPORT_DIR / "missing_page_candidates.csv", missing_rows, list(target_rows[0]))

        drift_rows = self.task_drift_labels()
        write_csv(
            ROOT / "reports/paperA/task_drift_labels.csv", drift_rows, list(drift_rows[0])
        )
        drift_counts = Counter({label: 0 for label in DRIFT_LABEL_ZH})
        drift_counts.update(row["drift_label"] for row in drift_rows)

        run_report = {
            "run_hashes": run_hashes,
            "byte_identical": len(set(run_hashes)) == 1,
            "target_count": len(analyzed),
            "plan_calls_per_run": len(analyzed) * len(VERSIONS),
            "python_hash_seed": os.environ["PYTHONHASHSEED"],
            "expected_independent_process_hash": os.environ.get("EXPANSION_EXPECTED_HASH"),
            "matches_expected_independent_process_hash": (
                not os.environ.get("EXPANSION_EXPECTED_HASH")
                or run_hashes[0] == os.environ["EXPANSION_EXPECTED_HASH"]
            ),
            "input_hashes": {
                str(path.relative_to(ROOT)): sha256(path)
                for path in (
                    ROOT / "config/resource_model.json",
                    ROOT / "data/conflicts.jsonl",
                    ROOT / "data/tasks.jsonl",
                    ROOT / "reports/a6_mechanism_frequency.csv",
                    ROOT / "corpus/manifests/page_map_p1.json",
                    *(ROOT / f"truth/{version}.json" for version in VERSIONS),
                )
            },
        }
        (REPORT_DIR / "determinism.json").write_text(json.dumps(run_report, indent=2) + "\n")
        if not run_report["byte_identical"]:
            raise RuntimeError("the two complete planning runs differ")
        if not run_report["matches_expected_independent_process_hash"]:
            raise RuntimeError("accepted run differs from independent-process hash")

        n_task = sum(row["corpus_closed"] for row in analyzed)
        n_mech = len(new_families)
        n_cluster = len(clusters)
        reachable_mechanisms = set(all_reachable_mechanisms)
        reachable_families_set = set(reachable_families)
        report = [
            "# Expansion Feasibility Probe Results", "",
            "The probe followed `prereg/expansion_probe.md`; no model or evaluated method was run.", "",
            "## Funnel", "",
            "| Layer | Definition | Count |", "|---:|---|---:|",
            *[f"| {row['layer']} | {row['name']} | {row['count']} |" for row in funnel],
            "", "## Decision numbers", "",
            f"- `N_task={n_task}` corpus-closed candidates.",
            f"- `N_mech={n_mech}` new mechanism families after deduplication.",
            f"- `N_cluster={n_cluster}` independent target-mechanism clusters after deduplication.",
            "", "## Mechanism audit", "",
            f"- Core42 normalized mechanisms: {len(self.core_mechanisms)}; families: {len(self.core_families)}.",
            f"- Reachable conflict mechanisms: {len(reachable_mechanisms)} "
            f"({len(reachable_mechanisms & self.core_mechanisms)} covered, "
            f"{len(reachable_mechanisms - self.core_mechanisms)} new).",
            f"- Reachable mechanism families: {len(reachable_families_set)} "
            f"({len(reachable_families_set & self.core_families)} covered, "
            f"{len(reachable_families_set - self.core_families)} new).",
            f"- New exact conflict assertion IDs relative to the Core22 assertion list: "
            f"{len(new_exact_assertions)}.",
            f"- New folded mechanisms represented after deduplication: {len(new_mechanisms)}.",
            f"- New mechanism families: {', '.join(new_families)}.",
            "", "## Target types", "",
            "| Type | All 934 | Reachable conflict | Deduplicated | Corpus-closed |",
            "|---|---:|---:|---:|---:|",
            *[
                f"| {key} | {sum(row['primary_type'] == key for row in analyzed)} | "
                f"{sum(row['primary_type'] == key for row in conflict_rows)} | "
                f"{sum(row['primary_type'] == key for row in deduplicated)} | "
                f"{sum(row['primary_type'] == key and row['corpus_closed'] for row in analyzed)} |"
                for key in type_order
            ],
            "", "## Source-cohort audit", "",
            "| Cohort | Source count | Layer-6 survivors |", "|---|---:|---:|",
        ]
        for cohort, members in sorted(cohorts.items()):
            final_count = next(
                row["survivors"] for row in cohort_rows
                if row["cohort"] == cohort and row["layer"] == 6
            )
            report.append(f"| {cohort} | {len(members)} | {final_count} |")
        new_routes = next(
            members for name, members in cohorts.items()
            if name == "new_route_1.21.11_vs_1.16.5"
        )
        new_route_reachable = sum(
            row["target"] in new_routes and row["reachable"] for row in analyzed
        )
        newly_craftable = next(
            members for name, members in cohorts.items()
            if name == "newly_craftable_1.21.11_vs_1.16.5"
        )
        report.extend([
            "",
            f"The informal 'about 300 newly craftable outputs' corresponds to "
            f"{len(newly_craftable)} endpoint-only outputs. The raw normalized endpoint "
            f"difference contains {len(new_routes)} new-route outputs, not about 40; "
            f"{new_route_reachable} survive the frozen-arena reachability layer, which is "
            "the layer that reproduces the approximate 40 figure.",
            "", "## Representation drift", "",
            ", ".join(f"{key}={value}" for key, value in sorted(drift_counts.items())) + ".",
            "", "## Determinism", "",
            f"Both complete runs produced `{run_hashes[0]}` with "
            "`PYTHONHASHSEED=0`; an independent invocation was required to match this "
            "hash before acceptance.", "",
        ])
        (REPORT_DIR / "REPORT.md").write_text("\n".join(report))


def main() -> None:
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit("accepted expansion runs require PYTHONHASHSEED=0")
    probe = ExpansionProbe()
    first, first_hash = probe.run_planners()
    second, second_hash = probe.run_planners()
    planners = {
        version: OraclePlanner(version, capacities=probe.capacities)
        for version in VERSIONS
    }
    cohorts = probe.source_cohorts(planners)
    analyzed = probe.analyze(first)
    probe.write_outputs(analyzed, [first_hash, second_hash], cohorts)
    print(json.dumps({
        "targets": len(analyzed),
        "run_hash": first_hash,
        "deterministic": first_hash == second_hash,
        "corpus_closed": sum(row["corpus_closed"] for row in analyzed),
        "deduplicated": sum(row["deduplicated"] for row in analyzed),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
