"""MAP-EM context-mixture truth discovery with data-selected context count."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

import numpy as np
from scipy.special import logsumexp
from sklearn.cluster import KMeans


@dataclass(frozen=True)
class SourceEvidence:
    source: str
    statements: Mapping[str, int | bool]
    timestamp: float | str | None = None
    tense: str = "current"

    @property
    def identity(self) -> str:
        # Historical statements are independent sources by construction.
        return f"{self.source}::{self.tense}"


@dataclass(frozen=True)
class ProbeEvidence:
    mechanism: str
    outcome: int | bool
    weight: float = 1.0


def _timestamp(value: float | str | None) -> float:
    if value is None:
        return np.nan
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return np.nan


def _normalize_rows(log_values: np.ndarray) -> np.ndarray:
    return np.exp(log_values - logsumexp(log_values, axis=1, keepdims=True))


class ContextMixtureModel:
    def __init__(
        self,
        *,
        alpha: float = 1.2,
        beta: float = 1.2,
        noise_alpha: float = 3.0,
        noise_beta: float = 19.0,
        probe_lambda: float = 8.0,
        max_k: int = 6,
        random_restarts: int = 8,
        max_iter: int = 200,
        tol: float = 1e-6,
        random_state: int = 2026,
        use_time_prior: bool = True,
    ) -> None:
        if alpha <= 1 or beta <= 1 or noise_alpha <= 1 or noise_beta <= 1:
            raise ValueError("MAP beta hyperparameters must exceed one")
        self.alpha = alpha
        self.beta = beta
        self.noise_alpha = noise_alpha
        self.noise_beta = noise_beta
        self.probe_lambda = probe_lambda
        self.max_k = max_k
        self.random_restarts = random_restarts
        self.max_iter = max_iter
        self.tol = tol
        self.random_state = random_state
        self.use_time_prior = use_time_prior
        self.mechanisms_: list[str] = []
        self.source_ids_: list[str] = []
        self.k_: int | None = None
        self.responsibilities_: np.ndarray | None = None
        self.world_responsibilities_: np.ndarray | None = None
        self.phi_: np.ndarray | None = None
        self.source_noise_: np.ndarray | None = None
        self.bic_scores_: dict[int, float] = {}
        self.log_likelihood_: float | None = None
        self._timestamps: np.ndarray | None = None

    def fit(
        self,
        sources: Sequence[SourceEvidence],
        probes: Sequence[ProbeEvidence] = (),
    ) -> "ContextMixtureModel":
        if len(sources) < 2:
            raise ValueError("at least two source records are required")
        mechanisms = sorted({m for source in sources for m in source.statements})
        mechanisms.extend(sorted({p.mechanism for p in probes if p.mechanism not in mechanisms}))
        if not mechanisms:
            raise ValueError("no mechanism evidence")
        self.mechanisms_ = mechanisms
        self.source_ids_ = [source.identity for source in sources]
        index = {mechanism: i for i, mechanism in enumerate(mechanisms)}
        values = np.full((len(sources), len(mechanisms)), np.nan)
        for row, source in enumerate(sources):
            for mechanism, value in source.statements.items():
                values[row, index[mechanism]] = int(bool(value))
        timestamps = np.array([_timestamp(source.timestamp) for source in sources])
        self._timestamps = timestamps
        probe_rows = [(index[p.mechanism], int(bool(p.outcome)), p.weight) for p in probes]

        candidates: list[dict[str, np.ndarray | float | int]] = []
        upper = min(self.max_k, len(sources) - 1)
        for k in range(1, upper + 1):
            best = self._fit_k(values, timestamps, probe_rows, k)
            observed = int(np.isfinite(values).sum()) + len(probe_rows)
            parameter_count = k * len(mechanisms) + (k - 1)
            bic = -2 * float(best["log_likelihood"]) + parameter_count * np.log(max(observed, 2))
            best["bic"] = float(bic)
            candidates.append(best)
            self.bic_scores_[k] = float(bic)
        selected = min(candidates, key=lambda row: float(row["bic"]))
        self.k_ = int(selected["k"])
        self.responsibilities_ = selected["r"]  # type: ignore[assignment]
        self.world_responsibilities_ = selected["q"]  # type: ignore[assignment]
        self.phi_ = selected["phi"]  # type: ignore[assignment]
        self.source_noise_ = selected["epsilon"]  # type: ignore[assignment]
        self.log_likelihood_ = float(selected["log_likelihood"])
        self._align_labels()
        return self

    def _initial_labels(
        self, values: np.ndarray, timestamps: np.ndarray, k: int, seed: int,
    ) -> np.ndarray:
        finite_time = np.isfinite(timestamps)
        if k > 1 and finite_time.sum() >= k and len(np.unique(timestamps[finite_time])) >= k:
            feature = timestamps.copy()
            feature[~finite_time] = np.nanmedian(feature[finite_time])
            feature = (feature - feature.mean()) / max(feature.std(), 1e-9)
            return KMeans(k, n_init=10, random_state=seed).fit_predict(feature[:, None])
        imputed = values.copy()
        column_mean = np.nanmean(imputed, axis=0)
        column_mean = np.where(np.isfinite(column_mean), column_mean, 0.5)
        missing = np.where(~np.isfinite(imputed))
        imputed[missing] = column_mean[missing[1]]
        if k == 1:
            return np.zeros(len(values), dtype=int)
        return KMeans(k, n_init=10, random_state=seed).fit_predict(imputed)

    def _time_prior(self, timestamps: np.ndarray, labels: np.ndarray, k: int) -> np.ndarray:
        prior = np.full((len(timestamps), k), 1 / k)
        if not self.use_time_prior:
            return prior
        finite = np.isfinite(timestamps)
        if not finite.any() or k == 1:
            return prior
        spread = max(float(np.nanstd(timestamps)), 1.0) / max(k, 1)
        anchors = np.array([
            np.nanmean(timestamps[(labels == cluster) & finite])
            if np.any((labels == cluster) & finite) else np.nanmean(timestamps[finite])
            for cluster in range(k)
        ])
        distances = -0.5 * ((timestamps[:, None] - anchors[None, :]) / spread) ** 2
        distances[~finite] = 0.0
        return _normalize_rows(distances)

    def _fit_k(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        probes: list[tuple[int, int, float]],
        k: int,
    ) -> dict[str, np.ndarray | float | int]:
        rng = np.random.default_rng(self.random_state + k * 1009)
        starts = max(1, self.random_restarts if not np.isfinite(timestamps).any() else 3)
        best: dict[str, np.ndarray | float | int] | None = None
        observed_mask = np.isfinite(values)
        y = np.nan_to_num(values, nan=0.0)

        for restart in range(starts):
            labels = self._initial_labels(values, timestamps, k, self.random_state + restart + k)
            if restart:
                labels = rng.integers(0, k, size=len(values))
            r = np.eye(k)[labels]
            time_prior = self._time_prior(timestamps, labels, k)
            phi = np.empty((k, values.shape[1]))
            for cluster in range(k):
                weights = r[:, cluster, None] * observed_mask
                phi[cluster] = (self.alpha - 1 + (weights * y).sum(axis=0)) / (
                    self.alpha + self.beta - 2 + weights.sum(axis=0)
                )
            phi = np.clip(phi, 0.01, 0.99)
            epsilon = np.full(len(values), 0.1)
            previous = -np.inf

            for _ in range(self.max_iter):
                p_one = epsilon[:, None, None] + (1 - 2 * epsilon[:, None, None]) * phi[None]
                likelihood = np.where(y[:, None, :] == 1, p_one, 1 - p_one)
                log_r = np.log(np.clip(time_prior, 1e-12, 1.0)) + (
                    observed_mask[:, None, :] * np.log(np.clip(likelihood, 1e-12, 1.0))
                ).sum(axis=2)
                r = _normalize_rows(log_r)

                truth_one = np.empty_like(p_one)
                for source_index in range(len(values)):
                    eps = epsilon[source_index]
                    numerator = phi * np.where(y[source_index] == 1, 1 - eps, eps)
                    denominator = numerator + (1 - phi) * np.where(y[source_index] == 1, eps, 1 - eps)
                    truth_one[source_index] = numerator / np.clip(denominator, 1e-12, None)
                weighted = r[:, :, None] * observed_mask[:, None, :]
                phi = (self.alpha - 1 + (weighted * truth_one).sum(axis=0)) / (
                    self.alpha + self.beta - 2 + weighted.sum(axis=0)
                )
                phi = np.clip(phi, 0.005, 0.995)

                mismatch = np.where(y[:, None, :] == 1, 1 - truth_one, truth_one)
                mismatch_sum = (weighted * mismatch).sum(axis=(1, 2))
                evidence_sum = weighted.sum(axis=(1, 2))
                epsilon = (self.noise_alpha - 1 + mismatch_sum) / (
                    self.noise_alpha + self.noise_beta - 2 + evidence_sum
                )
                epsilon = np.clip(epsilon, 0.005, 0.45)

                source_ll = float(logsumexp(log_r, axis=1).sum())
                if abs(source_ll - previous) < self.tol:
                    break
                previous = source_ll

            q_log = self._world_prior(r, timestamps) if self.use_time_prior else np.log(
                (r.sum(axis=0) + 0.5) / (r.sum() + 0.5 * k)
            )
            for mechanism, outcome, weight in probes:
                probability = phi[:, mechanism] if outcome else 1 - phi[:, mechanism]
                q_log += self.probe_lambda * weight * np.log(np.clip(probability, 1e-12, 1.0))
            q = np.exp(q_log - logsumexp(q_log))
            probe_ll = float(logsumexp(q_log)) if probes else 0.0
            total = previous + probe_ll
            result: dict[str, np.ndarray | float | int] = {
                "k": k, "r": r.copy(), "q": q.copy(), "phi": phi.copy(),
                "epsilon": epsilon.copy(), "log_likelihood": total,
            }
            if best is None or total > float(best["log_likelihood"]):
                best = result
        assert best is not None
        return best

    @staticmethod
    def _world_prior(r: np.ndarray, timestamps: np.ndarray) -> np.ndarray:
        finite = np.isfinite(timestamps)
        if finite.any():
            centered = timestamps - np.nanmax(timestamps)
            scale = max(float(np.nanstd(timestamps)), 1.0)
            recency = np.exp(np.clip(centered / scale, -20, 0))
            recency[~finite] = 0.25
            prior = (r * recency[:, None]).sum(axis=0) + 0.5
        else:
            prior = r.sum(axis=0) + 0.5
        return np.log(prior / prior.sum())

    def _align_labels(self) -> None:
        assert self.responsibilities_ is not None and self._timestamps is not None
        assert self.phi_ is not None and self.world_responsibilities_ is not None
        finite = np.isfinite(self._timestamps)
        if finite.any():
            anchors = []
            for k in range(self.responsibilities_.shape[1]):
                weights = self.responsibilities_[finite, k]
                anchors.append(float(np.average(self._timestamps[finite], weights=weights)))
            order = np.argsort(anchors)
        else:
            order = np.lexsort(self.phi_.T[::-1])
        self.responsibilities_ = self.responsibilities_[:, order]
        self.world_responsibilities_ = self.world_responsibilities_[order]
        self.phi_ = self.phi_[order]

    def mechanism_probabilities(self) -> dict[str, float]:
        if self.phi_ is None or self.world_responsibilities_ is None:
            raise RuntimeError("model is not fitted")
        values = self.world_responsibilities_ @ self.phi_
        return dict(zip(self.mechanisms_, map(float, values)))

    def source_posteriors(self) -> dict[str, list[float]]:
        if self.responsibilities_ is None:
            raise RuntimeError("model is not fitted")
        return dict(zip(self.source_ids_, self.responsibilities_.tolist()))
