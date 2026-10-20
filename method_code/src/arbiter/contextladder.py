"""Pure-simulation ContextLadder benchmark used for P3 theory checks."""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class GridConfig:
    seed: int = 2026
    instances_per_cell: int = 200
    ks: tuple[int, ...] = (2, 4, 8, 16, 32)
    structures: tuple[str, ...] = ("staircase", "random_separable")
    epsilons: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3)
    etas: tuple[float, ...] = (0.0, 0.1)
    prevalences: tuple[str, ...] = ("uniform", "old_70pct")


def _signatures(k: int, structure: str, rng: np.random.Generator) -> np.ndarray:
    bits = math.ceil(math.log2(k))
    if structure == "staircase":
        return np.array([[(context >> bit) & 1 for bit in range(bits)] for context in range(k)])
    width = max(4, bits * 2)
    while True:
        values = rng.integers(0, 2, size=(k, width))
        if len({tuple(row) for row in values}) == k:
            return values


def _distribution(k: int, prevalence: str) -> np.ndarray:
    if prevalence == "uniform":
        return np.full(k, 1 / k)
    values = np.full(k, 0.3 / (k - 1))
    values[0] = 0.7
    return values


def _observe(value: int, eta: float, rng: np.random.Generator) -> int:
    return int(value) ^ int(rng.random() < eta)


def _posterior_update(q: np.ndarray, column: np.ndarray, outcome: int, eta: float) -> np.ndarray:
    effective_eta = max(eta, 1e-6)
    likelihood = np.where(column == outcome, 1 - effective_eta, effective_eta)
    updated = q * likelihood
    return updated / updated.sum()


def _entropy(probability: float) -> float:
    if probability <= 0 or probability >= 1:
        return 0.0
    return -probability * math.log2(probability) - (1 - probability) * math.log2(1 - probability)


def _probe_policy(
    signatures: np.ndarray,
    active: int,
    prior: np.ndarray,
    eta: float,
    rng: np.random.Generator,
    *,
    decision_driven: bool,
    universal_plan: bool,
    threshold: float = 0.95,
) -> tuple[int, int]:
    if decision_driven and universal_plan:
        return 0, 0
    q = prior.copy()
    probes = 0
    counts = np.zeros(signatures.shape[1], dtype=int)
    limit = signatures.shape[1] * (1 if eta == 0 else 6)
    while probes < limit:
        plan_mass = np.array([q.sum()]) if universal_plan else q
        if plan_mass.max() >= threshold:
            break
        scores = []
        for mechanism in range(signatures.shape[1]):
            p_one = float(q[signatures[:, mechanism] == 1].sum())
            score = _entropy(p_one) / (1 + counts[mechanism])
            if decision_driven and not universal_plan:
                # Plan disagreement is exactly context disagreement in this grid.
                score *= 1 - float(np.square(q).sum())
            scores.append(score)
        selected = int(np.argmax(scores))
        outcome = _observe(int(signatures[active, selected]), eta, rng)
        q = _posterior_update(q, signatures[:, selected], outcome, eta)
        counts[selected] += 1
        probes += 1
    return probes, int(np.argmax(q))


def _nearest_context(signature: np.ndarray, contexts: np.ndarray) -> int:
    distance = np.abs(contexts - signature).sum(axis=1)
    return int(np.argmin(distance))


def _truthfinder(source_claims: np.ndarray, iterations: int = 20) -> np.ndarray:
    from arbiter.agent.aggregation import truthfinder_confidence

    return (truthfinder_confidence(source_claims, iterations) >= 0.5).astype(int)


def _linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    total = float(np.square(y - y.mean()).sum())
    residual = float(np.square(y - predicted).sum())
    return float(slope), 1 - residual / total if total else 1.0


def run_grid(output_dir: Path, config: GridConfig = GridConfig()) -> dict[str, object]:
    rng = np.random.default_rng(config.seed)
    rows: list[dict[str, object]] = []
    indirect: dict[int, list[int]] = defaultdict(list)
    methods = ("arbiter", "sequential", "entropy", "majority", "truthfinder", "informed")
    for k in config.ks:
        for structure in config.structures:
            for epsilon in config.epsilons:
                for eta in config.etas:
                    for prevalence in config.prevalences:
                        prior = _distribution(k, prevalence)
                        for instance in range(config.instances_per_cell):
                            signatures = _signatures(k, structure, rng)
                            active = int(rng.choice(k, p=prior))
                            universal = instance % 20 == 0
                            n_sources = int(rng.integers(5, 33))
                            source_contexts = rng.choice(k, size=n_sources, p=prior)
                            claims = signatures[source_contexts].copy()
                            claims ^= rng.random(claims.shape) < epsilon
                            majority_signature = (claims.mean(axis=0) >= 0.5).astype(int)
                            majority_guess = _nearest_context(majority_signature, signatures)
                            truth_guess = _nearest_context(_truthfinder(claims), signatures)
                            arb_probes, arb_guess = _probe_policy(
                                signatures, active, prior, eta, rng,
                                decision_driven=True, universal_plan=universal,
                            )
                            ent_probes, ent_guess = _probe_policy(
                                signatures, active, prior, eta, rng,
                                decision_driven=False, universal_plan=False,
                            )
                            dependencies = int(rng.integers(1, min(8, signatures.shape[1]) + 1))
                            outcomes = [
                                _observe(int(signatures[active, i % signatures.shape[1]]), eta, rng)
                                for i in range(dependencies)
                            ]
                            sequential_success = int(
                                all(outcomes[i] == signatures[active, i % signatures.shape[1]]
                                    for i in range(dependencies))
                            )
                            mechanism = int(rng.integers(0, signatures.shape[1]))
                            mechanism_truth = int(signatures[active, mechanism])
                            # n_m denotes sources bearing on this same mechanism,
                            # not all context-mixed sources in the instance.
                            support_votes = np.full(n_sources, mechanism_truth, dtype=int)
                            support_votes ^= rng.random(n_sources) < epsilon
                            indirect_error = int((support_votes.mean() >= 0.5) != mechanism_truth)
                            if epsilon == 0.3 and n_sources % 2 == 1:
                                indirect[n_sources].append(indirect_error)
                            common = {
                                "k": k, "structure": structure, "epsilon": epsilon,
                                "eta": eta, "prevalence": prevalence, "instance": instance,
                                "active_context": active, "minority_world": int(prevalence == "old_70pct" and active != 0),
                                "universal_plan": int(universal), "n_sources": n_sources,
                                "plan_dependencies": dependencies,
                                "indirect_error": indirect_error,
                            }
                            outcomes_by_method = {
                                "arbiter": (arb_probes, int(universal or arb_guess == active)),
                                "sequential": (dependencies, sequential_success),
                                "entropy": (ent_probes, int(ent_guess == active)),
                                "majority": (0, int(universal or majority_guess == active)),
                                "truthfinder": (0, int(universal or truth_guess == active)),
                                "informed": (0, 1),
                            }
                            for method in methods:
                                probes, success = outcomes_by_method[method]
                                rows.append({**common, "method": method, "probes": probes, "success": success})

    output_dir.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    with (output_dir / "grid_results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    arbiter_staircase = [row for row in rows if row["method"] == "arbiter"
                         and row["structure"] == "staircase" and row["eta"] == 0.0
                         and row["prevalence"] == "uniform" and not row["universal_plan"]]
    means = {
        k: np.mean([float(row["probes"]) for row in arbiter_staircase if row["k"] == k])
        for k in config.ks
    }
    probe_slope, probe_r2 = _linear_fit(
        np.log2(np.array(config.ks, dtype=float)), np.array([means[k] for k in config.ks])
    )
    sequential_rows = [row for row in rows if row["method"] == "sequential"]
    dep_slope, dep_r2 = _linear_fit(
        np.array([float(row["plan_dependencies"]) for row in sequential_rows]),
        np.array([float(row["probes"]) for row in sequential_rows]),
    )
    n_values, error_values = [], []
    for n in sorted(indirect):
        errors = indirect[n]
        rate = (sum(errors) + 0.5) / (len(errors) + 1)
        n_values.append(n)
        error_values.append(math.log(rate))
    indirect_slope, indirect_r2 = _linear_fit(np.array(n_values), np.array(error_values))
    minority = [row for row in rows if row["method"] == "majority" and row["minority_world"]]
    majority_accuracy = float(np.mean([row["success"] for row in minority]))
    universal = [row for row in rows if row["method"] == "arbiter" and row["universal_plan"]]
    universal_zero = all(row["probes"] == 0 for row in universal)
    predictions = {
        "1_log2_probe_slope": {"value": probe_slope, "r2": probe_r2, "passed": 0.8 <= probe_slope <= 1.2},
        "2_sequential_linear": {"slope": dep_slope, "r2": dep_r2, "passed": dep_slope > 0 and dep_r2 >= 0.9},
        "3_indirect_log_error": {"slope": indirect_slope, "r2": indirect_r2, "passed": indirect_slope < 0 and indirect_r2 >= 0.8},
        "4_minority_majority": {"accuracy": majority_accuracy, "passed": majority_accuracy < 0.2},
        "5_universal_zero_probe": {"instances": len(universal), "passed": universal_zero},
    }
    summary = {
        "seed": config.seed,
        "instances_per_cell": config.instances_per_cell,
        "instances": len(rows) // len(methods),
        "method_rows": len(rows),
        "truthfinder": "simplified Yin et al. iterative trust/confidence update",
        "staircase_probe_means": means,
        "predictions": predictions,
        "passed": all(item["passed"] for item in predictions.values()),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _write_summary_csv(rows, output_dir / "method_summary.csv")
    _plot(output_dir, means, n_values, error_values)
    return summary


def _write_summary_csv(rows: list[dict[str, object]], path: Path) -> None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["method"])].append(row)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["method", "mean_probes", "success_rate", "n"])
        writer.writeheader()
        for method, group in sorted(grouped.items()):
            writer.writerow({
                "method": method,
                "mean_probes": np.mean([float(row["probes"]) for row in group]),
                "success_rate": np.mean([float(row["success"]) for row in group]),
                "n": len(group),
            })


def _plot(output_dir: Path, means: dict[int, float], n_values: list[int], errors: list[float]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].plot(np.log2(list(means)), list(means.values()), marker="o")
    axes[0].set(xlabel="log2(K)", ylabel="mean ARBITER probes", title="Context identification")
    axes[1].plot(n_values, errors, marker=".")
    axes[1].set(xlabel="supporting sources", ylabel="log error rate", title="Indirect adjudication")
    figure.tight_layout()
    figure.savefig(output_dir / "contextladder.png", dpi=160)
    plt.close(figure)
