"""Pre-registered P2 synthetic acceptance grid."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from sklearn.metrics import adjusted_rand_score

from .context_mixture import ContextMixtureModel, ProbeEvidence, SourceEvidence


@dataclass
class SyntheticResult:
    ari: float
    ece: float
    probe_flip_before: float
    probe_flip_after: float
    k_accuracy: float
    passed: bool


def _dataset(k: int, seed: int, mechanisms: int = 24, sources_per_k: int = 60):
    rng = np.random.default_rng(seed)
    prototypes = rng.integers(0, 2, size=(k, mechanisms))
    # Ensure each pair is separated on enough mechanisms.
    for cluster in range(k):
        prototypes[cluster, cluster % mechanisms] = cluster % 2
        prototypes[cluster, (cluster + 7) % mechanisms] = (cluster + 1) % 2
    sources = []
    labels = []
    for cluster in range(k):
        for index in range(sources_per_k):
            epsilon = rng.uniform(0.03, 0.18)
            observed = prototypes[cluster].copy()
            observed ^= rng.random(mechanisms) < epsilon
            sources.append(SourceEvidence(
                source=f"s{cluster}_{index}",
                timestamp=cluster * 100 + rng.normal(0, 3),
                statements={f"m{j}": int(v) for j, v in enumerate(observed)},
            ))
            labels.append(cluster)
    return sources, np.array(labels), prototypes


def _ece(probabilities: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> float:
    total = 0.0
    for lower in np.linspace(0, 1, bins, endpoint=False):
        mask = (probabilities >= lower) & (probabilities < lower + 1 / bins)
        if mask.any():
            total += mask.mean() * abs(probabilities[mask].mean() - outcomes[mask].mean())
    return float(total)


def run_synthetic_validation(seed: int = 2026) -> SyntheticResult:
    sources, labels, prototypes = _dataset(4, seed)
    probes = [ProbeEvidence(f"m{i}", int(prototypes[-1, i])) for i in range(6)]
    model = ContextMixtureModel(random_state=seed).fit(sources, probes)
    predicted = model.responsibilities_.argmax(axis=1)
    ari = float(adjusted_rand_score(labels, predicted))
    estimates = np.array([model.mechanism_probabilities()[f"m{i}"] for i in range(6, 24)])
    ece = _ece(estimates, prototypes[-1, 6:24])

    flip_sources = [
        SourceEvidence("old-a", {"pivot": 1, "anchor": 0}, timestamp=0),
        SourceEvidence("old-b", {"pivot": 1, "anchor": 0}, timestamp=1),
        SourceEvidence("new-a", {"pivot": 0, "anchor": 1}, timestamp=100),
        SourceEvidence("new-b", {"pivot": 0, "anchor": 1}, timestamp=101),
    ]
    before_model = ContextMixtureModel(max_k=2, random_state=seed).fit(flip_sources)
    before = float(before_model.world_responsibilities_[0])
    after_model = ContextMixtureModel(max_k=2, random_state=seed).fit(
        flip_sources, [ProbeEvidence("pivot", 1)]
    )
    after = float(after_model.world_responsibilities_[0])

    correct = total = 0
    for true_k in range(2, 6):
        for repeat in range(5):
            sample, _, _ = _dataset(
                true_k, seed + true_k * 100 + repeat,
                mechanisms=30, sources_per_k=20,
            )
            selected = ContextMixtureModel(random_state=seed + repeat).fit(sample).k_
            correct += int(selected == true_k)
            total += 1
    k_accuracy = correct / total
    passed = ari >= 0.9 and ece <= 0.05 and after > 0.8 and after > before and k_accuracy >= 0.8
    return SyntheticResult(ari, ece, before, after, k_accuracy, passed)
