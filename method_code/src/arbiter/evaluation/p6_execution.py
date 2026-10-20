"""Evaluation-only P6 orchestration over public search and simulator facades."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from arbiter.adapters import (
    PublicSemanticAdapter,
    load_claims_cache,
    load_search_backend,
    load_webcraft_module,
)
from arbiter.adapters.claims import validate_claim_rows
from arbiter.agent import (
    ARBITER_EXECUTION, NAIVE_EXECUTION, VERIFY_ALL_EXECUTION,
    ArbiterAgent, ExecutionPolicy,
)
from arbiter.agent.aggregation import (
    SearchDerivedClaim,
    arbiter,
    majority_rag,
    naive_rag,
    operator_from_assertion,
    parametric,
    shared_planner,
    truthfinder_rag,
    verify_all,
)
from arbiter.contracts import Goal, ItemStack
from arbiter.llm import CachedClaimExtractor, GatewayClient
from arbiter.pinning import verify_webcraft_pin

from .p6_matrix import P6Job
from .oracle_baseline import oracle_aggregation
from .claim_compiler import (
    CorpusItemNormalizer,
    compile_claim_assertions,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _execution_policy() -> ExecutionPolicy:
    config = json.loads((REPO_ROOT / "config/arbiter_hparams.json").read_text())
    raw = config.get("execution")
    expected = {
        "version", "n_plans", "uncertainty_weight", "inventory_aware",
        "max_probes", "max_actions", "decision_error_cost",
    }
    if not isinstance(raw, dict) or set(raw) != expected or raw["version"] != 1:
        raise RuntimeError("missing or invalid final execution policy")
    return ExecutionPolicy(
        n_plans=int(raw["n_plans"]),
        uncertainty_weight=float(raw["uncertainty_weight"]),
        inventory_aware=bool(raw["inventory_aware"]),
        max_probes=int(raw["max_probes"]),
        max_actions=int(raw["max_actions"]),
        decision_error_cost=float(raw["decision_error_cost"]),
    )


def _reader_config() -> dict[str, Any]:
    config = json.loads((REPO_ROOT / "config/arbiter_hparams.json").read_text())
    reader = config.get("reader")
    if not isinstance(reader, dict):
        raise RuntimeError("missing frozen reader configuration")
    expected = {
        "version", "query_sequence", "family_table_source", "family_table",
        "page_title_followup", "k", "max_searches", "mip_gap_retry_rounds",
        "reading_mode", "llm_extractor_version",
    }
    if set(reader) != expected:
        raise RuntimeError("unexpected frozen reader configuration fields")
    if reader["version"] != 5:
        raise RuntimeError("formal reader must use version 5")
    if reader["query_sequence"] != ["display_name", "family_name", "id"]:
        raise RuntimeError("reader v5 query order changed")
    if reader["reading_mode"] != "all_public":
        raise RuntimeError("reader v5 default must be all_public")
    if reader["llm_extractor_version"] != "96b07e80":
        raise RuntimeError("reader v5 LLM extractor version changed")
    return reader


def _compiler(root: Path) -> tuple[CorpusItemNormalizer, dict[str, Any]]:
    config = json.loads((REPO_ROOT / "config/arbiter_hparams.json").read_text())
    compiler = config.get("compiler")
    if not isinstance(compiler, dict) or set(compiler) != {
        "version", "schema_source", "normalizer_sources",
    }:
        raise RuntimeError("missing or invalid shared compiler configuration")
    if compiler["version"] != 1:
        raise RuntimeError("unsupported shared compiler version")
    schema = compiler["schema_source"]
    if set(schema) != {"path", "sha256"}:
        raise RuntimeError("invalid claim payload schema source")
    schema_path = root / schema["path"]
    actual_schema_hash = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    if actual_schema_hash != schema["sha256"]:
        raise RuntimeError("claim payload schema hash mismatch")
    normalizer = CorpusItemNormalizer.from_config(root, compiler["normalizer_sources"])
    provenance = {
        "version": compiler["version"],
        "schema_source": {"path": schema["path"], "sha256": actual_schema_hash},
        "normalizer_sources": [
            {"path": path, "sha256": digest, "symbol": symbol}
            for path, digest, symbol in normalizer.provenance
        ],
    }
    return normalizer, provenance


def _family_query(item: str, table: list[dict[str, str]]) -> str | None:
    for row in table:
        if set(row) != {"match", "value", "query"}:
            raise RuntimeError("invalid reader family-table row")
        if row["match"] == "exact" and item == row["value"]:
            return row["query"]
        if row["match"] == "suffix" and item.endswith(row["value"]):
            return row["query"]
        if row["match"] not in {"exact", "suffix"}:
            raise RuntimeError("invalid reader family-table match mode")
    return None


def _demand_queries(item: str, reader: dict[str, Any]) -> list[tuple[str, str]]:
    values = {
        "display_name": item.replace("_", " "),
        "family_name": _family_query(item, reader["family_table"]),
        "id": f"minecraft:{item}",
    }
    return [
        (mode, values[mode]) for mode in reader["query_sequence"]
        if values[mode] is not None
    ]


def _effective_extractor(claim: dict[str, Any], llm_version: str) -> str:
    marker = claim.get("payload", {}).get("extractor")
    if isinstance(marker, str) and marker:
        return marker
    if claim.get("extractor_version") == llm_version:
        return "llm"
    return "unclassified"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_conflict_vectors(path: Path) -> dict[str, dict[str, bool]]:
    return {row["id"]: row["truth_vector"] for row in _jsonl(path)}


def build_evidence(
    task: dict[str, Any], condition: str, *, index_dir: str, root: Path,
    pin_path: Path, claims_cache: Any | None,
    fallback: CachedClaimExtractor | None, fallback_dir: Path, top_k: int,
) -> dict[str, Any]:
    reader = _reader_config()
    normalizer, compiler_provenance = _compiler(root)
    if top_k != int(reader["k"]):
        raise RuntimeError("runtime top-k differs from frozen reader configuration")
    family_source = reader["family_table_source"]
    if set(family_source) != {"path", "sha256"}:
        raise RuntimeError("invalid reader family-table source")
    family_path = root / str(family_source["path"])
    if hashlib.sha256(family_path.read_bytes()).hexdigest() != family_source["sha256"]:
        raise RuntimeError("reader family table differs from the frozen normalizer")
    condition_index = str(Path(index_dir) / condition)
    resource_model = json.loads((root / "config/resource_model.json").read_text())
    mineable_nodes = sorted(resource_model["resource_block_counts"])
    backend = load_search_backend(
        condition, condition_index, root=root, pin_path=pin_path,
    )
    rows: list[dict[str, Any]] = []
    extracted: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    search_events: list[dict[str, Any]] = []
    claims_by_chunk: dict[str, list[dict[str, Any]]] = {}
    target = str(task["target"]["item"])
    initial_rows = task["standard_items"] + task.get("task_supplements", [])
    initial = Counter({row["name"]: int(row["count"]) for row in initial_rows})
    demand_queue: deque[str] = deque([target])
    seen_demands: set[str] = set()
    if fallback is not None:
        fallback_dir.mkdir(parents=True, exist_ok=True)

    def claims_for(row: dict[str, Any]) -> list[dict[str, Any]]:
        if claims_cache is not None:
            return claims_cache.get(str(row["chunk_id"]))
        if fallback is None:
            raise RuntimeError("claim cache unavailable and fallback disabled")
        cache_name = hashlib.sha256(str(row["chunk_id"]).encode()).hexdigest() + ".json"
        cache_path = fallback_dir / cache_name
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text())
            if cached.get("chunk_id") != row["chunk_id"]:
                raise RuntimeError("fallback claim cache provenance mismatch")
            return validate_claim_rows(cached["claims"], str(row["chunk_id"]))
        claims = fallback.extract(row)
        cache_path.write_text(json.dumps({
            "chunk_id": row["chunk_id"], "claims": claims,
        }, indent=2, sort_keys=True) + "\n")
        return claims

    def enqueue_claim_demands(
        claims: list[dict[str, Any]], demand_item: str, public_text: str,
    ) -> None:
        for claim in claims:
            if claim.get("subject_item") != demand_item:
                continue
            assertions = _claim_assertions(
                claim, public_text, set(mineable_nodes), normalizer,
            )
            for assertion in assertions:
                for requirement in assertion.get("_execution", {}).get("requires", []):
                    if requirement not in seen_demands and initial[requirement] <= 0:
                        demand_queue.append(requirement)
            if claim["claim_type"] not in {"recipe", "smelting"}:
                continue
            ingredients = claim["payload"].get("ingredients", [])
            if isinstance(ingredients, list):
                needed = Counter(item for item in ingredients if isinstance(item, str))
                for ingredient, count in needed.items():
                    if (ingredient not in seen_demands
                            and initial[ingredient] < count):
                        demand_queue.append(ingredient)
            if claim["claim_type"] == "smelting" and "coal" not in seen_demands:
                demand_queue.append("coal")

    def query_demand(item: str, phase: str) -> None:
        followed_titles: set[str] = set()

        def run_query(query: str, query_kind: str) -> list[dict[str, Any]]:
            if len(search_events) >= int(reader["max_searches"]):
                return []
            hits = backend.search(query, top_k)
            search_events.append({
                "phase": phase,
                "demand_item": item,
                "query": query,
                "query_kind": query_kind,
                "chunk_ids": [str(hit["chunk_id"]) for hit in hits],
            })
            for row in hits:
                chunk_id = str(row["chunk_id"])
                if chunk_id not in seen_chunks:
                    rows.append(row)
                    seen_chunks.add(chunk_id)
                    claims_by_chunk[chunk_id] = claims_for(row)
                    extracted.extend(claims_by_chunk[chunk_id])
                claims = claims_by_chunk[chunk_id]
                enqueue_claim_demands(claims, item, str(row["text"]))
            return hits

        for query_kind, query in _demand_queries(item, reader):
            hits = run_query(query, query_kind)
            if not reader["page_title_followup"]:
                continue
            for row in hits:
                title = str(row["page_title"])
                key = title.casefold()
                if key in followed_titles:
                    continue
                followed_titles.add(key)
                run_query(title, "page_title_followup")

    def next_demand() -> str:
        priorities = [
            (0 if item in mineable_nodes or item.endswith("_pickaxe") or item == "coal"
             else 1, index)
            for index, item in enumerate(demand_queue)
        ]
        _, selected = min(priorities)
        demand_queue.rotate(-selected)
        item = demand_queue.popleft()
        demand_queue.rotate(selected)
        return item

    while demand_queue and len(search_events) < int(reader["max_searches"]):
        item = next_demand()
        if item in seen_demands:
            continue
        seen_demands.add(item)
        query_demand(item, "closure")

    for _ in range(int(reader["mip_gap_retry_rounds"])):
        provisional = {
            "search_rows": rows,
            "claims": extracted,
            "mineable_nodes": mineable_nodes,
        }
        derived, operators = _claims(provisional, normalizer=normalizer)
        if derived:
            aggregation = naive_rag(derived)
            plans = shared_planner(
                aggregation, operators, initial, target,
                int(task["target"]["count"]), limit=1,
            )
        else:
            plans = []
        if plans or len(search_events) >= int(reader["max_searches"]):
            break
        for item in _planning_gap_items(operators, initial, target):
            query_demand(item, "mip_gap")
        while demand_queue and len(search_events) < int(reader["max_searches"]):
            item = next_demand()
            if item in seen_demands:
                continue
            seen_demands.add(item)
            query_demand(item, "gap_closure")
    if not rows:
        raise RuntimeError(f"search returned no evidence for {task['id']} under {condition}")
    extractor_counts = Counter(
        _effective_extractor(claim, str(reader["llm_extractor_version"]))
        for claim in extracted
    )
    return {
        "schema_version": 2, "task_id": task["id"], "condition": condition,
        "claim_source": "precomputed_cache" if claims_cache is not None else "preview_fallback",
        "search_rows": rows, "claims": extracted,
        "mineable_nodes": mineable_nodes,
        "reader": {
            "search_count": len(search_events),
            "unique_chunk_count": len(seen_chunks),
            "raw_claim_count": len(extracted),
            "extractor_claim_counts": dict(sorted(extractor_counts.items())),
            "search_events": search_events,
            "compiler_provenance": compiler_provenance,
        },
    }


def _claim_assertion(
    raw: dict[str, Any], public_text: str = "",
    mineable_nodes: set[str] | None = None,
) -> dict[str, Any] | None:
    assertions = _claim_assertions(raw, public_text, mineable_nodes)
    return assertions[0] if assertions else None


def _claim_assertions(
    raw: dict[str, Any], public_text: str = "",
    mineable_nodes: set[str] | None = None,
    normalizer: CorpusItemNormalizer | None = None,
) -> list[dict[str, Any]]:
    return compile_claim_assertions(
        raw, public_text, mineable_nodes,
        normalizer=normalizer or CorpusItemNormalizer({}, ()),
    )


def _claims(
    bundle: dict[str, Any], *, normalizer: CorpusItemNormalizer | None = None,
) -> tuple[list[SearchDerivedClaim], list[Any]]:
    hits = {str(row["chunk_id"]): row for row in bundle["search_rows"]}
    mineable_nodes = set(map(str, bundle.get("mineable_nodes", ()))) or None
    derived: list[SearchDerivedClaim] = []
    operators: dict[str, Any] = {}
    claims_by_id = {
        str(raw.get("claim_id")): raw for raw in bundle["claims"]
        if isinstance(raw.get("claim_id"), str)
    }
    for raw in bundle["claims"]:
        if raw["chunk_id"] not in hits:
            continue
        parent = claims_by_id.get(str(raw.get("parent_claim_id")))
        parent_payload = parent.get("payload", {}) if parent else {}
        parent_expands_family = bool(
            isinstance(parent_payload.get("layout_options"), list)
            and len(parent_payload["layout_options"]) == 9
        ) or bool(
            parent and parent.get("claim_type") == "smelting"
            and isinstance(parent_payload.get("ingredients"), list)
            and len(parent_payload["ingredients"]) > 1
        )
        if parent_expands_family:
            continue
        assertions = _claim_assertions(
            raw, str(hits[raw["chunk_id"]].get("text", "")), mineable_nodes,
            normalizer,
        )
        for assertion in assertions:
            try:
                operator = operator_from_assertion(assertion)
            except ValueError:
                continue
            operators[operator.mechanism_id] = operator
            derived.append(SearchDerivedClaim(
                hits[raw["chunk_id"]], operator.mechanism_id,
                raw["polarity"] == "positive",
            ))
    return derived, list(operators.values())


def _planning_gap_items(
    operators: list[Any], initial: Counter[str], goal: str,
) -> list[str]:
    reachable = {item for item, count in initial.items() if count > 0}
    changed = True
    while changed:
        changed = False
        for operator in operators:
            if all(any(option in reachable for option in choices)
                   for choices in operator.input_options) and all(
                       requirement in reachable for requirement in operator.requires
                   ):
                if operator.output not in reachable:
                    reachable.add(operator.output)
                    changed = True
    if goal in reachable:
        return []
    by_output: dict[str, list[Any]] = {}
    for operator in operators:
        by_output.setdefault(operator.output, []).append(operator)
    queue = deque([goal])
    visited = set()
    gaps = []
    while queue:
        item = queue.popleft()
        if item in visited or item in reachable:
            continue
        visited.add(item)
        producers = by_output.get(item, [])
        if not producers:
            gaps.append(item)
            continue
        for operator in producers:
            for choices in operator.input_options:
                if not any(option in reachable for option in choices):
                    queue.append(choices[0])
            for requirement in operator.requires:
                if requirement not in reachable:
                    queue.append(requirement)
    return gaps or [goal]


def _goal_count(inventory: list[dict[str, Any]], goal: dict[str, Any]) -> int:
    return sum(int(row["count"]) for row in inventory if row["name"] == goal["item"])


def _is_stale(mechanisms: list[str], world: str,
              vectors: dict[str, dict[str, bool]]) -> bool:
    for mechanism in mechanisms:
        vector = vectors.get(mechanism)
        if vector is not None and not vector[world] and any(vector.values()):
            return True
    return False


_MECHANISM_ACTIONS = frozenset({"mine", "craft_grid", "smelt", "check_name"})

_PROBE_THEN_RAG_CUTOFFS = {
    "1.16.5": "2021-02-13T16:05:32+00:00",
    "1.19": "2022-07-07T09:42:18+00:00",
    "1.21.11": "2026-01-08T12:23:30+00:00",
}
_STONE_PROBE_MECHANISM = "probe_prerequisite_stone_drop"
_PICKAXE_PROBE_MECHANISM = "probe_prerequisite_stone_pickaxe"
_IRON_SEPARATOR_MECHANISM = "cf_f222b76ffee74fa3"
_TURTLE_SEPARATOR_MECHANISM = "cf_c8f3718a4637369f"


def _produced_names(result: Any) -> set[str]:
    if not isinstance(result, dict):
        return set()
    rows = []
    if isinstance(result.get("product"), dict):
        rows.append(result["product"])
    if isinstance(result.get("items"), list):
        rows.extend(result["items"])
    return {
        str(row["name"]) for row in rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def _probe_then_rag_identify(api: PublicSemanticAdapter) -> dict[str, Any]:
    """Execute the frozen two-separator public decision tree."""
    mechanisms: list[str] = []
    trace: list[dict[str, Any]] = []

    stone = api.mine("stone", intent="probe")
    mechanisms.append(_STONE_PROBE_MECHANISM)
    if stone.get("success") is False or "cobblestone" not in _produced_names(stone):
        return {"identified_version": None, "mechanisms": mechanisms,
                "trace": trace, "failure": "stone_probe_prerequisite_failed"}

    layout = [
        "cobblestone", "cobblestone", "cobblestone",
        None, "stick", None, None, "stick", None,
    ]
    pickaxe = api.craft_grid(layout, intent="probe")
    mechanisms.append(_PICKAXE_PROBE_MECHANISM)
    if (pickaxe.get("success") is False
            or "stone_pickaxe" not in _produced_names(pickaxe)):
        return {"identified_version": None, "mechanisms": mechanisms,
                "trace": trace, "failure": "pickaxe_probe_prerequisite_failed"}

    iron = api.mine("iron_ore", intent="probe")
    mechanisms.append(_IRON_SEPARATOR_MECHANISM)
    products = _produced_names(iron)
    if iron.get("success") is False or not products:
        return {"identified_version": None, "mechanisms": mechanisms,
                "trace": trace, "failure": "iron_separator_failed"}
    post_116 = "raw_iron" in products
    trace.append({
        "separator": _IRON_SEPARATOR_MECHANISM,
        "intent": "probe", "observed": post_116,
    })
    if not post_116:
        return {"identified_version": "1.16.5", "mechanisms": mechanisms,
                "trace": trace, "failure": None}

    turtle = api.check_name("turtle_scute", intent="probe")
    mechanisms.append(_TURTLE_SEPARATOR_MECHANISM)
    if not isinstance(turtle.get("resolvable"), bool):
        return {"identified_version": None, "mechanisms": mechanisms,
                "trace": trace, "failure": "name_separator_malformed"}
    trace.append({
        "separator": _TURTLE_SEPARATOR_MECHANISM,
        "intent": "probe", "observed": turtle["resolvable"],
    })
    return {
        "identified_version": "1.21.11" if turtle["resolvable"] else "1.19",
        "mechanisms": mechanisms, "trace": trace, "failure": None,
    }


def _probe_then_rag_filter_bundle(
    bundle: dict[str, Any], identified_version: str,
) -> dict[str, Any]:
    """Keep only public evidence no later than the frozen version cutoff."""
    cutoff_text = _PROBE_THEN_RAG_CUTOFFS[identified_version]
    cutoff = datetime.fromisoformat(cutoff_text)
    rows = [
        row for row in bundle["search_rows"]
        if datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")) <= cutoff
    ]
    chunks = {str(row["chunk_id"]) for row in rows}
    claims = [row for row in bundle["claims"] if str(row["chunk_id"]) in chunks]
    return {
        **bundle,
        "search_rows": rows,
        "claims": claims,
        "temporal_filter": {
            "identified_version": identified_version,
            "corpus_cutoff": cutoff_text,
            "search_rows_before": len(bundle["search_rows"]),
            "search_rows_after": len(rows),
            "claims_before": len(bundle["claims"]),
            "claims_after": len(claims),
        },
    }


def _action_role_metrics(
    events: list[dict[str, Any]], mechanisms: list[str], world: str,
    vectors: dict[str, dict[str, bool]],
) -> dict[str, Any]:
    """Score agent-declared probe roles without judging whether they are honest."""
    actions = [event for event in events if event.get("method") in _MECHANISM_ACTIONS]
    if len(actions) != len(mechanisms):
        raise ValueError(
            "mechanism/action alignment mismatch: "
            f"{len(mechanisms)} mechanisms for {len(actions)} actions"
        )
    stale = [
        index for index, mechanism in enumerate(mechanisms)
        if _is_stale([mechanism], world, vectors)
    ]
    stale_commit = [index for index in stale if actions[index].get("intent") != "probe"]
    probes = [event for event in events if event.get("intent") == "probe"]
    return {
        "HTR_all": int(bool(stale)),
        "HTR_commit": int(bool(stale_commit)),
        "stale_action_indices": stale,
        "stale_commit_action_indices": stale_commit,
        "probe_count": len(probes),
        "probe_cost": sum(float(event["cost"]["primary"]) for event in probes),
        "primary_cost": sum(float(event["cost"]["primary"]) for event in events),
    }


def run_episode(
    job: P6Job, task: dict[str, Any], bundle: dict[str, Any], *, root: Path,
    pin_path: Path, index_dir: str, output_dir: Path,
    conflict_vectors: dict[str, dict[str, bool]],
    preview: bool,
    prior_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verify_webcraft_pin(pin_path, root, require_ready=True, allow_preview=preview)
    normalizer, _ = _compiler(root)
    if job.method == "probe_then_rag":
        derived, operators = [], []
    else:
        derived, operators = _claims(bundle, normalizer=normalizer)
    aggregation = None
    if job.method in {"parametric", "probe_then_parametric"}:
        if prior_bundle is None:
            raise RuntimeError("parametric requires a frozen prior bundle")
        operators = list(prior_bundle["operators"])
        aggregation = parametric(prior_bundle["samples"])
        derived = []
    elif job.method == "oracle":
        derived = []
    elif job.method == "probe_then_rag":
        pass
    elif derived:
        if job.method in {"naive_rag", "arbiter_shell_naive"}:
            aggregation = naive_rag(derived)
        elif job.method == "majority_rag":
            aggregation = majority_rag(derived)
        elif job.method == "truthfinder_rag":
            aggregation = truthfinder_rag(derived)
        elif job.method == "verify_all":
            aggregation = verify_all(derived)
        elif job.method == "arbiter":
            source_count = len({
                (claim.hit["source_site"], claim.hit["page_title"], claim.hit["revid"])
                for claim in derived
            })
            aggregation = arbiter(derived) if source_count >= 2 else naive_rag(derived)
        else:
            raise ValueError(f"unsupported public-evidence method: {job.method}")
    initial_rows = task["standard_items"] + task.get("task_supplements", [])
    initial = Counter({row["name"]: int(row["count"]) for row in initial_rows})
    if job.method == "oracle":
        operators, aggregation = oracle_aggregation(job.world, initial, root)
    log_path = output_dir / "actions" / (
        f"{job.condition}__{job.task_id}__{job.world}__{job.method}__{job.seed}.jsonl"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sim = load_webcraft_module(
        "sim", root=root, pin_path=pin_path, allow_preview=preview,
    )
    handle = sim.create_simulated_agent(
        version=job.world, log_path=log_path, initial_inventory=initial,
        search_condition=job.condition,
        search_index_dir=str(Path(index_dir) / job.condition),
    )
    api = PublicSemanticAdapter(handle["api"])
    policy = _execution_policy()
    agent = ArbiterAgent(
        Goal(task["target"]["item"], int(task["target"]["count"])),
        [ItemStack(row["name"], int(row["count"])) for row in initial_rows], api,
    )
    if aggregation is not None:
        agent.configure(operators, aggregation.belief)
    probe_prefix: list[str] = []
    probe_trace: list[dict[str, Any]] = []
    temporal_filter: dict[str, Any] | None = None
    try:
        if job.method == "probe_then_rag":
            identification = _probe_then_rag_identify(api)
            probe_prefix = list(identification["mechanisms"])
            probe_trace = list(identification["trace"])
            identified = identification["identified_version"]
            if identified is None:
                api.submit({"feasible": None})
                result = {
                    "success": False, "feasible": None,
                    "used_mechanism_ids": [],
                    "probe_mechanism_ids": probe_prefix,
                    "policy_trace": [],
                }
                temporal_filter = {"failure": identification["failure"]}
            else:
                api.report_version(identified)
                filtered = _probe_then_rag_filter_bundle(bundle, identified)
                temporal_filter = filtered["temporal_filter"]
                derived, operators = _claims(filtered, normalizer=normalizer)
                if not derived:
                    api.submit({"feasible": False})
                    result = {
                        "success": False, "feasible": False,
                        "used_mechanism_ids": [],
                        "probe_mechanism_ids": probe_prefix,
                        "policy_trace": [],
                    }
                else:
                    aggregation = naive_rag(derived)
                    agent.configure(operators, aggregation.belief)
                    probe_spend = sum(
                        float(event["cost"]["primary"])
                        for event in _jsonl(log_path)
                    )
                    remaining = max(0, policy.max_actions - int(probe_spend))
                    result = agent.solve(
                        policy=replace(policy, max_actions=remaining),
                        strategy=NAIVE_EXECUTION,
                    )
        elif aggregation is None:
            api.submit({"feasible": False})
            result = {
                "success": False, "feasible": False,
                "used_mechanism_ids": [], "probe_mechanism_ids": [],
            }
        else:
            strategy = (
                ARBITER_EXECUTION if job.method == "arbiter"
                else VERIFY_ALL_EXECUTION if job.method in {
                    "verify_all", "probe_then_parametric",
                }
                else NAIVE_EXECUTION
            )
            result = agent.solve(policy=policy, strategy=strategy)
        final_inventory = api.inventory()
        handle["evaluator"].finish_episode()
    finally:
        handle["shutdown"]()
    events = _jsonl(log_path)
    true_feasible = bool(task["feasible"][job.world])
    reported = result["feasible"]
    produced = _goal_count(final_inventory, task["target"]) >= int(task["target"]["count"])
    success = bool((true_feasible and reported is True and produced)
                   or (not true_feasible and reported is False))
    used = probe_prefix + list(result["used_mechanism_ids"])
    role_metrics = _action_role_metrics(events, used, job.world, conflict_vectors)
    return {
        "schema_version": 2, "record_type": "episode", "method": job.method,
        "condition": job.condition, "world": job.world, "task_id": job.task_id,
        "seed": job.seed, "conflict_mechanism_ids": task["conflict_ids"],
        "reading_mode": bundle.get("reading_mode", "all_public"),
        "events": events, "metrics": {
            "SR": int(success),
            "HTR": role_metrics["HTR_all"],
            "HTR_all": role_metrics["HTR_all"],
            "HTR_commit": role_metrics["HTR_commit"],
            "probe_count": role_metrics["probe_count"],
            "probe_cost": role_metrics["probe_cost"],
            "primary_cost": role_metrics["primary_cost"],
        },
        "probe_count": role_metrics["probe_count"],
        "probe_cost": role_metrics["probe_cost"],
        "stale_action_indices": role_metrics["stale_action_indices"],
        "stale_commit_action_indices": role_metrics["stale_commit_action_indices"],
        "reader": ({
            "search_count": 0, "unique_chunk_count": 0, "raw_claim_count": 0,
            "extractor_claim_counts": {}, "search_events": [],
            "admitted_claim_count": 0,
        } if job.method in {"parametric", "probe_then_parametric", "oracle"} else {
            **bundle.get("reader", {}), "admitted_claim_count": len(derived),
        }),
        "reported_feasible": reported, "true_feasible": true_feasible,
        "used_mechanism_ids": used,
        "policy_trace": probe_trace + result.get("policy_trace", []),
        "method_version": (
            "post-run-probe-then-rag-v1"
            if job.method == "probe_then_rag" else "shared-shell-v1"
        ),
        **({"temporal_filter": temporal_filter} if temporal_filter is not None else {}),
    }


def run_matrix(
    jobs: list[P6Job], tasks: list[dict[str, Any]], *, root: Path,
    pin_path: Path, index_dir: str, output_dir: Path, top_k: int = 5,
    preview: bool = False, reading_mode: str | None = None,
    prior_bundle: dict[str, Any] | None = None,
) -> Path:
    verify_webcraft_pin(pin_path, root, require_ready=True, allow_preview=preview)
    reader = _reader_config()
    selected_mode = reading_mode or str(reader["reading_mode"])
    task_map = {task["id"]: task for task in tasks}
    conflict_vectors = load_conflict_vectors(root / "data/conflicts.jsonl")
    evidence_dir = output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    caches = {
        condition: load_claims_cache(
            str(Path(index_dir) / condition), root=root, pin_path=pin_path,
            allow_missing=preview, reading_mode=selected_mode,
            llm_extractor_version=str(reader["llm_extractor_version"]),
        )
        for condition in sorted({job.condition for job in jobs})
    }
    fallback = CachedClaimExtractor(GatewayClient()) if any(
        value is None for value in caches.values()
    ) else None
    fallback_dir = output_dir / "preview_claim_cache"
    bundles: dict[tuple[str, str, int], dict[str, Any]] = {}
    for job in jobs:
        key = (job.condition, job.task_id, job.seed)
        if key in bundles:
            continue
        path = evidence_dir / f"{job.condition}__{job.task_id}__{job.seed}.json"
        if path.is_file():
            bundles[key] = json.loads(path.read_text())
            expected_source = (
                "precomputed_cache" if caches[job.condition] is not None
                else "preview_fallback"
            )
            if (bundles[key].get("schema_version") != 2
                    or bundles[key].get("claim_source") != expected_source
                    or bundles[key].get("reading_mode") != selected_mode):
                raise RuntimeError("existing evidence belongs to a different P6 mode")
        else:
            bundle = build_evidence(
                task_map[job.task_id], job.condition, index_dir=index_dir,
                root=root, pin_path=pin_path, claims_cache=caches[job.condition],
                fallback=fallback, fallback_dir=fallback_dir, top_k=top_k,
            )
            bundle["reading_mode"] = selected_mode
            path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
            bundles[key] = bundle
    output = output_dir / "episodes.jsonl"
    completed = set()
    if output.is_file():
        for row in _jsonl(output):
            completed.add((
                row["condition"], row["task_id"], row["world"],
                row["method"], int(row["seed"]),
            ))
    for job in jobs:
        key = (job.condition, job.task_id, job.world, job.method, job.seed)
        if key in completed:
            continue
        record = run_episode(
            job, task_map[job.task_id], bundles[(job.condition, job.task_id, job.seed)],
            root=root, pin_path=pin_path, index_dir=index_dir,
            output_dir=output_dir, conflict_vectors=conflict_vectors,
            preview=preview,
            prior_bundle=prior_bundle,
        )
        with output.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return output
