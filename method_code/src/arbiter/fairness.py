"""Static boundary checks for runtime agent code."""
from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_TERMS = {
    "truth/", "conflicts.jsonl", "oracle_cost", "oracle_plans",
    "conflicts_by_world", "true_feasible", "execution_world",
    "source_truth_version", "split", "candidate_versions",
}
FORBIDDEN_AGENT_PARAMETERS = {
    "truth", "metadata", "version", "versions", "world", "task",
    "conflicts", "oracle", "split",
}


def scan_agent_tree(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        lowered = source.lower()
        for term in sorted(FORBIDDEN_TERMS):
            if term.lower() in lowered:
                violations.append(f"{path}:{term}")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                args = {arg.arg for arg in node.args.args[1:]}
                bad = sorted(args & FORBIDDEN_AGENT_PARAMETERS)
                if bad:
                    violations.append(f"{path}:forbidden constructor args {bad}")
    return violations

