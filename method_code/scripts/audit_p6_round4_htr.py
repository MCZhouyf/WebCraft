"""Evaluator-side action-role and failed-claim audit for P6 round 4."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter
from arbiter.agent import ArbiterAgent
from arbiter.agent.decision import DecisionController
from arbiter.agent.aggregation import (
    arbiter,
    naive_rag,
    operator_from_assertion,
)
from arbiter.agent.planning import BeliefPlanner, MechanismOperator
from arbiter.contracts import Goal, ItemStack
from arbiter.evaluation.p6_execution import (
    _claim_assertion,
    _claims,
    load_conflict_vectors,
)
from arbiter.evaluation.p6_matrix import development_tasks
from arbiter.pinning import build_pin


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
ROUND = ROOT / "artifacts/p6_round4_pipeline"
ACTION_METHODS = {"mine", "craft_grid", "smelt", "check_name"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty audit: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class ReplayAPI:
    """Return the exact public results saved by the original episode."""

    def __init__(self, events: list[dict[str, Any]]):
        self.events = events
        self.index = 0

    def _next(self, method: str, params: dict[str, Any]) -> Any:
        if self.index >= len(self.events):
            raise AssertionError(f"replay emitted extra {method} call")
        event = self.events[self.index]
        self.index += 1
        assert event["method"] == method, (self.index, method, event["method"])
        assert event["params"] == params, (self.index, params, event["params"])
        return event["result"]

    def mine(self, node_id: str) -> dict[str, Any]:
        return self._next("mine", {"nodeId": node_id})

    def craft_grid(self, layout: list[str | None]) -> dict[str, Any]:
        return self._next("craft_grid", {"layout": layout})

    def smelt(self, item: str, fuel: str, n: int) -> dict[str, Any]:
        return self._next("smelt", {"input": item, "fuel": fuel, "n": n})

    def check_name(self, identifier: str) -> dict[str, Any]:
        return self._next("check_name", {"identifier": identifier})

    def inventory(self) -> list[dict[str, Any]]:
        return self._next("inventory", {})

    def report_version(self, guess: str) -> dict[str, Any]:
        return self._next("report_version", {"guess": guess})

    def submit(self, result: Any) -> dict[str, Any]:
        return self._next("submit", {"result": result})

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        return self._next("search", {"query": query, "k": k})


class AuditedAgent(ArbiterAgent):
    def __init__(self, *args: Any, expected_used: list[str], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.expected_used = expected_used
        self.audit_role = "probe"
        self.audit_actions: list[dict[str, Any]] = []

    def execute_plan(self, plan: Any, *, exec_as_probe: bool = True) -> bool:
        previous = self.audit_role
        self.audit_role = "plan_step"
        try:
            return super().execute_plan(plan, exec_as_probe=exec_as_probe)
        finally:
            self.audit_role = previous

    def _call(self, method: str, params: dict[str, Any]):
        mechanism_id = self._used_mechanisms[-1]
        result = super()._call(method, params)
        self.audit_actions.append({
            "mechanism_id": mechanism_id,
            "role": self.audit_role,
            "method": method,
            "params": params,
            "result": result,
        })
        return result

    def _matching_plan(self, plans: list[Any]) -> Any:
        remaining = self.expected_used[len(self._used_mechanisms):]
        matches = []
        for plan in plans:
            prefix = 0
            for actual, expected in zip(plan.mechanism_ids, remaining):
                if actual != expected:
                    break
                prefix += 1
            if prefix:
                matches.append((prefix, plan))
        if not matches:
            raise AssertionError("saved execution is not a prefix of any current candidate plan")
        return max(matches, key=lambda row: row[0])[1]

    def solve(self, *, n_plans: int = 5, uncertainty_weight: float = 0.35,
              decision_voi: bool | None = None,
              exec_as_probe: bool | None = None) -> dict[str, Any]:
        if self._belief is None:
            raise RuntimeError("agent is not configured")
        self._used_mechanisms = []
        self._explicit_probes = []
        if decision_voi is None:
            decision_voi = self._ablations.decision_voi
        if exec_as_probe is None:
            exec_as_probe = self._ablations.exec_as_probe
        operators = [MechanismOperator(
            **{**operator.__dict__, "probability": self._belief.mechanism_probability(
                operator.mechanism_id
            )}
        ) for operator in self._operators]
        planner = BeliefPlanner(operators, self.kit, uncertainty_weight=uncertainty_weight)
        plans = planner.candidates(self.goal.item, self.goal.count, n_plans)
        controller = DecisionController()
        decisions = []
        for _ in range(2 * max(1, len(operators))):
            decision = controller.choose(
                plans, self._belief,
                {operator.mechanism_id: operator.acquisition_cost + 1 for operator in operators},
                decision_voi=decision_voi,
            )
            decisions.append(decision.kind)
            if decision.kind == "infeasible":
                assert len(self._used_mechanisms) == len(self.expected_used)
                self.api.submit({"feasible": False})
                return self._result(False, False, decisions)
            if decision.kind == "probe":
                next_index = len(self._used_mechanisms)
                assert next_index < len(self.expected_used)
                mechanism = self.expected_used[next_index]
                candidates = set().union(*(plan.mechanism_ids for plan in plans))
                assert mechanism in candidates
                operator = next(op for op in operators if op.mechanism_id == mechanism)
                self._explicit_probes.append(operator.mechanism_id)
                self._used_mechanisms.append(operator.mechanism_id)
                result = self._call(operator.method, operator.params)
                observed = not (isinstance(result, dict) and result.get("success") is False)
                observed = observed and operator.output in self._produced_names(result)
                self._belief.update(operator.mechanism_id, observed)
                self.kit = Counter({item["name"]: item["count"] for item in self.api.inventory()})
                operators = [MechanismOperator(
                    **{**op.__dict__, "probability": self._belief.mechanism_probability(op.mechanism_id)}
                ) for op in operators]
                planner = BeliefPlanner(operators, self.kit, uncertainty_weight=uncertainty_weight)
                plans = planner.candidates(self.goal.item, self.goal.count, n_plans)
                continue
            plan = self._matching_plan(plans)
            success = self.execute_plan(plan, exec_as_probe=exec_as_probe)
            if success:
                assert len(self._used_mechanisms) == len(self.expected_used)
                self.api.submit({"feasible": True})
                return self._result(True, True, decisions)
            self.kit = Counter({item["name"]: item["count"] for item in self.api.inventory()})
            operators = [MechanismOperator(
                **{**op.__dict__, "probability": self._belief.mechanism_probability(op.mechanism_id)}
            ) for op in operators]
            planner = BeliefPlanner(operators, self.kit, uncertainty_weight=uncertainty_weight)
            plans = planner.candidates(self.goal.item, self.goal.count, n_plans)
        assert len(self._used_mechanisms) == len(self.expected_used)
        self.api.submit({"feasible": None})
        return self._result(False, None, decisions)


def _task_map() -> dict[str, dict[str, Any]]:
    tasks = development_tasks(
        ROOT / "config/p6_development_tasks.json",
        WEBCRAFT / "config/resource_model.json",
    )
    return {task["id"]: task for task in tasks}


def _bundle(condition: str, task_id: str, seed: int) -> dict[str, Any]:
    return json.loads((
        ROUND / "evidence" / f"{condition}__{task_id}__{seed}.json"
    ).read_text())


def _aggregation(bundle: dict[str, Any], method: str):
    derived, operators = _claims(bundle)
    if method == "naive_rag":
        aggregation = naive_rag(derived)
    else:
        source_count = len({
            (claim.hit["source_site"], claim.hit["page_title"], claim.hit["revid"])
            for claim in derived
        })
        aggregation = arbiter(derived) if source_count >= 2 else naive_rag(derived)
    return aggregation, operators


def _claim_support(bundle: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    hits = {str(row["chunk_id"]): row for row in bundle["search_rows"]}
    mineable = set(map(str, bundle.get("mineable_nodes", ()))) or None
    support: dict[str, list[dict[str, str]]] = {}
    for raw in bundle["claims"]:
        hit = hits.get(str(raw["chunk_id"]))
        if hit is None:
            continue
        assertion = _claim_assertion(raw, str(hit["text"]), mineable)
        if assertion is None:
            continue
        try:
            mechanism = operator_from_assertion(assertion).mechanism_id
        except ValueError:
            continue
        support.setdefault(mechanism, []).append({
            "claim_id": str(raw["claim_id"]),
            "chunk_id": str(raw["chunk_id"]),
            "page_title": str(hit["page_title"]),
            "timestamp": str(hit["timestamp"]),
            "span_text": str(raw["span_text"]),
        })
    return support


def _replay_arbiter(
    episode: dict[str, Any], task: dict[str, Any], bundle: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    aggregation, operators = _aggregation(bundle, "arbiter")
    raw = ReplayAPI(episode["events"])
    api = PublicSemanticAdapter(raw)
    initial_rows = task["standard_items"] + task.get("task_supplements", [])
    agent = AuditedAgent(
        Goal(task["target"]["item"], int(task["target"]["count"])),
        [ItemStack(row["name"], int(row["count"])) for row in initial_rows],
        api,
        expected_used=list(episode["used_mechanism_ids"]),
    )
    agent.configure(operators, aggregation.belief)
    result = agent.solve()
    api.inventory()
    assert raw.index == len(raw.events), (raw.index, len(raw.events))
    assert result["used_mechanism_ids"] == episode["used_mechanism_ids"]
    assert len(result["probe_mechanism_ids"]) == episode["probe_count"]
    assert len(agent.audit_actions) == len(episode["used_mechanism_ids"])
    return result, agent.audit_actions


def _true_operator_ids(world: str, initial: Counter[str]) -> set[str]:
    sys.path.insert(0, str(WEBCRAFT))
    sys.path.insert(0, str(WEBCRAFT / "scripts"))
    from conflict_core import recipe_assertion  # type: ignore
    from oracle.planner import OraclePlanner  # type: ignore

    oracle = OraclePlanner(world, initial=initial)
    mechanisms = set()
    for recipe in oracle.recipes:
        assertion = recipe_assertion(recipe["raw"])
        if assertion is not None:
            mechanisms.add(operator_from_assertion(assertion).mechanism_id)
    for source in oracle.sources.values():
        assertion = {
            "kind": "drop", "block": source["block"],
            "silk_touch": False, "drops": [source["output"]],
            "_execution": {
                "requires": [source["tool"]] if source["tool"] else [],
                "output_count": source["output_count"],
            },
        }
        mechanisms.add(operator_from_assertion(assertion).mechanism_id)
    return mechanisms


def _observed(action: dict[str, Any], operator: Any) -> bool:
    result = action["result"]
    if not isinstance(result, dict) or result.get("success") is False:
        return False
    produced = []
    if isinstance(result.get("product"), dict):
        produced.append(result["product"])
    produced.extend(result.get("items", []))
    return operator.output in {
        item["name"] for item in produced if isinstance(item, dict) and "name" in item
    }


def main() -> None:
    expected_pin = json.loads((ROOT / "config/webcraft_pin.json").read_text())
    current_pin = build_pin(WEBCRAFT)
    for name in ("sim", "truth", "data", "config"):
        assert current_pin["roots"][name] == expected_pin["roots"][name], name
    tasks = _task_map()
    episodes = _jsonl(ROUND / "episodes.jsonl")
    vectors = load_conflict_vectors(WEBCRAFT / "data/conflicts.jsonl")
    htr_episodes = [
        row for row in episodes
        if row["method"] == "arbiter" and int(row["metrics"]["HTR"]) == 1
    ]
    assert len(htr_episodes) == 12
    htr_rows = []
    replay_rows = []
    for episode in htr_episodes:
        bundle = _bundle(episode["condition"], episode["task_id"], int(episode["seed"]))
        result, actions = _replay_arbiter(episode, tasks[episode["task_id"]], bundle)
        support = _claim_support(bundle)
        stale_count = 0
        for index, action in enumerate(actions, 1):
            mechanism = action["mechanism_id"]
            vector = vectors.get(mechanism)
            if vector is None or vector[episode["world"]] or not any(vector.values()):
                continue
            stale_count += 1
            claims = support.get(mechanism, [])
            htr_rows.append({
                "condition": episode["condition"], "task_id": episode["task_id"],
                "world": episode["world"], "seed": episode["seed"],
                "action_index": index, "action_method": action["method"],
                "action_params": json.dumps(action["params"], sort_keys=True),
                "mechanism_id": mechanism, "role": action["role"],
                "action_success": action["result"].get("success"),
                "probe_count_episode": episode["probe_count"],
                "support_claim_ids": ";".join(sorted({row["claim_id"] for row in claims})),
                "support_pages": ";".join(sorted({row["page_title"] for row in claims})),
                "support_spans": " || ".join(sorted({row["span_text"] for row in claims})),
                "truth_vector": json.dumps(vector, sort_keys=True),
            })
        assert stale_count > 0
        replay_rows.append({
            "condition": episode["condition"], "task_id": episode["task_id"],
            "world": episode["world"], "used_actions": len(actions),
            "probe_actions": sum(action["role"] == "probe" for action in actions),
            "plan_steps": sum(action["role"] == "plan_step" for action in actions),
            "stale_actions": stale_count,
            "decision_sequence": ";".join(result["decisions"]),
            "replay_match": True,
        })
    _write_csv(ROUND / "htr_audit.csv", htr_rows)
    _write_csv(ROUND / "htr_replay_episodes.csv", replay_rows)

    terminal = {
        (row["method"], row["condition"], row["task_id"], row["world"]): row["terminal_type"]
        for row in csv.DictReader((ROUND / "diagnosis/episodes.csv").open())
    }
    failures = [
        row for row in episodes
        if row["method"] == "naive_rag"
        and terminal[(row["method"], row["condition"], row["task_id"], row["world"])]
        == "execution_failed"
    ]
    assert len(failures) == 34
    standard = Counter({
        row["name"]: int(row["count"])
        for row in next(iter(tasks.values()))["standard_items"]
    })
    true_ids = {world: _true_operator_ids(world, standard) for world in ("1.16.5", "1.19", "1.21.11")}
    failure_rows = []
    for episode in failures:
        bundle = _bundle(episode["condition"], episode["task_id"], int(episode["seed"]))
        _, operators = _claims(bundle)
        operator_map = {operator.mechanism_id: operator for operator in operators}
        support = _claim_support(bundle)
        action_events = [event for event in episode["events"] if event["method"] in ACTION_METHODS]
        assert len(action_events) == len(episode["used_mechanism_ids"])
        failed = []
        for index, (mechanism, event) in enumerate(
            zip(episode["used_mechanism_ids"], action_events), 1
        ):
            operator = operator_map[mechanism]
            action = {"result": event["result"]}
            if not _observed(action, operator):
                failed.append((index, mechanism, event, operator))
        assert failed
        index, mechanism, event, operator = failed[0]
        vector = vectors.get(mechanism)
        catalogued_stale = bool(
            vector is not None and not vector[episode["world"]] and any(vector.values())
        )
        if catalogued_stale:
            category = "catalogued_stale_claim"
        elif mechanism not in true_ids[episode["world"]]:
            category = "extraction_error_uncatalogued_world_false"
        else:
            category = "other"
        claims = support.get(mechanism, [])
        failure_rows.append({
            "condition": episode["condition"], "task_id": episode["task_id"],
            "world": episode["world"], "seed": episode["seed"],
            "failed_action_index": index, "action_method": event["method"],
            "action_params": json.dumps(event["params"], sort_keys=True),
            "mechanism_id": mechanism, "category": category,
            "catalogued": vector is not None,
            "catalogued_stale_in_world": catalogued_stale,
            "mechanism_in_world_gold": mechanism in true_ids[episode["world"]],
            "event_success": event["success"], "expected_output": operator.output,
            "support_claim_ids": ";".join(sorted({row["claim_id"] for row in claims})),
            "support_pages": ";".join(sorted({row["page_title"] for row in claims})),
            "support_spans": " || ".join(sorted({row["span_text"] for row in claims})),
        })
    _write_csv(ROUND / "naive_execution_failure_audit.csv", failure_rows)
    summary = {
        "arbiter_htr_episodes": len(htr_episodes),
        "arbiter_stale_actions": len(htr_rows),
        "arbiter_stale_action_roles": dict(Counter(row["role"] for row in htr_rows)),
        "naive_execution_failed_episodes": len(failure_rows),
        "naive_failure_categories": dict(Counter(row["category"] for row in failure_rows)),
        "replay_matches": all(row["replay_match"] for row in replay_rows),
        "evaluator_truth_source": "WebCraft OraclePlanner over pinned truth/config hashes",
    }
    (ROUND / "htr_audit_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
