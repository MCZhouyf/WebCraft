import inspect
from pathlib import Path

from arbiter.agent import ArbiterAgent
from arbiter.fairness import scan_agent_tree


def test_runtime_agent_has_only_public_constructor_inputs():
    assert list(inspect.signature(ArbiterAgent).parameters) == ["goal", "kit", "api"]


def test_agent_tree_has_no_private_benchmark_references():
    root = Path(__file__).parents[1] / "src/arbiter/agent"
    assert scan_agent_tree(root) == []


def test_adapter_tree_has_no_private_benchmark_references():
    root = Path(__file__).parents[1] / "src/arbiter/adapters"
    assert scan_agent_tree(root) == []

