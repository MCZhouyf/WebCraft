from collections import Counter
import json
from pathlib import Path

from arbiter.agent.aggregation import (
    SearchDerivedClaim,
    arbiter,
    majority_rag,
    naive_rag,
    parametric,
    operator_from_assertion,
    shared_planner,
    truthfinder_rag,
    verification_targets,
    verify_all,
)
from arbiter.agent.planning import MechanismOperator
from arbiter.normalization import mechanism_id


def claim(source, timestamp, mechanism, value):
    hit = {
        "text": f"{mechanism}={value}", "source_site": source,
        "page_title": "Page", "revid": f"{source}-{timestamp}",
        "timestamp": timestamp, "chunk_id": f"chunk-{source}-{timestamp}",
    }
    return SearchDerivedClaim(hit, mechanism, value)


def test_search_claim_rejects_non_public_provenance():
    row = claim("a", "2020-01-01", "m", True)
    invalid = dict(row.hit)
    invalid["synthetic"] = True
    try:
        SearchDerivedClaim(invalid, "m", True)
    except ValueError as exc:
        assert "six search fields" in str(exc)
    else:
        raise AssertionError("private provenance field was accepted")


def test_observational_aggregators_change_only_belief():
    claims = [
        claim("old-a", "2017-01-01", "m", False),
        claim("new-a", "2025-01-01", "m", True),
        claim("old-b", "2018-01-01", "m", False),
    ]
    assert naive_rag(claims).belief.mechanism_probability("m") == 0.99
    assert majority_rag(claims).belief.mechanism_probability("m") == 0.01
    assert truthfinder_rag(claims).belief.mechanism_probability("m") < 0.5


def test_parametric_uses_smoothed_prior_samples():
    result = parametric([{"m": True}, {"m": False}, {"m": True}])
    assert result.belief.mechanism_probability("m") == 0.6


def test_verify_all_uses_shared_planner_and_returns_dependencies():
    result = verify_all([claim("new", "2025-01-01", "m", True)])
    operator = MechanismOperator("m", "mine", {"node_type": "x"}, (), "x")
    plan = shared_planner(result, [operator], Counter(), "x", 1, limit=1)[0]
    assert verification_targets(result, plan) == ("m",)


def test_arbiter_returns_context_belief_from_public_claims():
    claims = [
        claim("old-a", "2017-01-01", "m", False),
        claim("old-b", "2018-01-01", "m", False),
        claim("new-a", "2024-01-01", "m", True),
        claim("new-b", "2025-01-01", "m", True),
    ]
    result = arbiter(claims)
    assert result.name == "arbiter"
    assert result.verification == "decision_voi"
    assert 0 <= result.belief.mechanism_probability("m") <= 1


def test_operator_compiler_uses_only_explicit_assertion_structure():
    drop = operator_from_assertion({
        "kind": "drop", "block": "iron_ore", "drops": ["raw_iron"],
        "silk_touch": False,
    })
    recipe = operator_from_assertion({
        "kind": "recipe", "station": "crafting_grid", "ordered": False,
        "ingredients": [["oak_planks"], ["oak_planks"]],
        "output": "stick", "count": 4, "recipe_type": "crafting_shapeless",
    })
    assert drop.params == {"node_type": "iron_ore"}
    assert recipe.input_options == (("oak_planks",), ("oak_planks",))
    assert recipe.params["layout"] == [
        "oak_planks", "oak_planks", None, None, None, None, None, None, None,
    ]


def test_operator_compiler_preserves_evaluator_execution_metadata_outside_identity():
    assertion = {
        "kind": "drop", "block": "redstone_ore", "drops": ["redstone"],
        "silk_touch": False,
    }
    operator = operator_from_assertion({
        **assertion,
        "_execution": {"requires": ["iron_pickaxe"], "output_count": 4},
    })
    assert operator.mechanism_id == mechanism_id(assertion)
    assert operator.requires == ("iron_pickaxe",)
    assert operator.output_count == 4


def test_frozen_method_matrix_has_only_converged_methods():
    root = Path(__file__).parents[1]
    matrix = json.loads((root / "config/method_matrix.json").read_text())
    assert [row["name"] for row in matrix["methods"]] == [
        "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
        "truthfinder_rag", "verify_all", "arbiter_shell_naive", "arbiter",
        "oracle",
    ]
    assert matrix["optional"]["name"] == "llm_direct"
    assert matrix["optional"]["enabled"] is True
