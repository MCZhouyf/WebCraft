"""Stratify the accepted external-agent rescore by returned model identifier.

This is an offline export. It reads accepted episode logs and the previously
gold-rescored episode table; it never imports a gateway or runs an agent.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/main/post_review/alias_stratified"
RESCORE = ROOT / "reports/main/post_review/external_agents/rescore_v2/episode_metrics.csv"
RUNS = {
    "direct_llm": "direct_llm_assembled",
    "react": "react_assembled",
    "reflexion": "reflexion_assembled",
    "adam": "adam",
    "xenon": "xenon_assembled",
}
EXPECTED = {
    "direct_llm": (16, 94),
    "react": (47, 63),
    "reflexion": (10, 100),
    "adam": (110,),
    "xenon": (110,),
}
METRICS = {
    "SR": ("SR", None),
    "feasible_task_SR": ("feasible_success", "true_feasible"),
    "HTR_all": ("legacy_HTR_all", None),
    "self_reported_HTR_commit": ("legacy_self_reported_HTR_commit", None),
    "overconservatism": ("false_abstention", "true_feasible"),
    "terminal_execution_failure_rate": ("terminal_execution_failure", None),
}
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261005


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty table: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def episode_key(row: dict) -> tuple[str, str, str, str, int]:
    return (
        row["method"], row["task_id"], row["condition"], row["world"],
        int(row["seed"]),
    )


def returned_identifier(row: dict) -> str:
    identifiers = {d.get("model") for d in row.get("diagnostics", []) if d.get("model")}
    if not identifiers and row.get("model"):
        identifiers = {row["model"]}
    if len(identifiers) != 1:
        raise ValueError(
            f"episode must have exactly one returned model identifier: {episode_key(row)} {identifiers}"
        )
    return identifiers.pop()


def ratio(rows: list[dict], numerator: str, denominator: str | None) -> tuple[int, int, float]:
    num = sum(int(row[numerator]) for row in rows)
    den = sum(int(row[denominator]) if denominator else 1 for row in rows)
    return num, den, num / den if den else float("nan")


def difference_ci(left: list[dict], right: list[dict], numerator: str,
                  denominator: str | None, rng: np.random.Generator) -> tuple[float, float, float]:
    def values(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
        num = np.array([int(row[numerator]) for row in rows], dtype=float)
        den = np.array([int(row[denominator]) if denominator else 1 for row in rows], dtype=float)
        return num, den

    lnum, lden = values(left)
    rnum, rden = values(right)
    estimate = lnum.sum() / lden.sum() - rnum.sum() / rden.sum()
    li = rng.integers(0, len(left), (BOOTSTRAP_DRAWS, len(left)))
    ri = rng.integers(0, len(right), (BOOTSTRAP_DRAWS, len(right)))
    ld = lden[li].sum(axis=1)
    rd = rden[ri].sum(axis=1)
    valid = (ld > 0) & (rd > 0)
    samples = lnum[li].sum(axis=1)[valid] / ld[valid] - rnum[ri].sum(axis=1)[valid] / rd[valid]
    if not len(samples):
        return float(estimate), float("nan"), float("nan")
    return float(estimate), float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def main() -> None:
    source_dir = ROOT / "artifacts/post_review/external_agents/final"
    source_paths = {method: source_dir / directory / "episodes.jsonl"
                    for method, directory in RUNS.items()}
    source_hashes = {str(RESCORE.relative_to(ROOT)): sha256(RESCORE)}
    raw: dict[tuple[str, str, str, str, int], dict] = {}
    identifier_counts: dict[str, Counter] = {}
    for method, path in source_paths.items():
        source_hashes[str(path.relative_to(ROOT))] = sha256(path)
        counts = Counter()
        for row in read_jsonl(path):
            if row["method"] != method:
                raise ValueError(f"method mismatch in {path}")
            key = episode_key(row)
            if key in raw:
                raise ValueError(f"duplicate raw episode: {key}")
            identifier = returned_identifier(row)
            counts[identifier] += 1
            raw[key] = {"server_model_identifier": identifier}
        identifier_counts[method] = counts
        if tuple(sorted(counts.values())) != EXPECTED[method]:
            raise ValueError(f"unexpected identifier strata for {method}: {counts}")

    rescored: dict[tuple[str, str, str, str, int], dict] = {}
    with RESCORE.open() as handle:
        for row in csv.DictReader(handle):
            if row["source"] != "external":
                continue
            key = episode_key(row)
            if key in rescored:
                raise ValueError(f"duplicate rescored episode: {key}")
            rescored[key] = row
    if raw.keys() != rescored.keys() or len(raw) != 550:
        raise ValueError("raw and gold-rescored episode matrices do not match exactly")

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    episodes = []
    for key in sorted(raw):
        row = {**rescored[key], **raw[key]}
        groups[(key[0], row["server_model_identifier"])].append(row)
        episodes.append({
            "method": key[0], "task_id": key[1], "condition": key[2],
            "world": key[3], "seed": key[4],
            "server_model_identifier": row["server_model_identifier"],
            "true_feasible": int(row["true_feasible"]), "SR": int(row["SR"]),
        })

    strata = []
    for (method, identifier), rows in sorted(groups.items()):
        values = {}
        for name, (numerator, denominator) in METRICS.items():
            _, _, values[name] = ratio(rows, numerator, denominator)
        strata.append({
            "method": method, "server_model_identifier": identifier, "n": len(rows),
            "feasible_n": sum(int(row["true_feasible"]) for row in rows),
            **values,
        })

    differences = []
    conclusions = {}
    for method in RUNS:
        model_groups = sorted((identifier, rows) for (m, identifier), rows in groups.items() if m == method)
        if len(model_groups) == 1:
            conclusions[method] = (
                f"{method}: only one returned-model stratum ({model_groups[0][0]}, n=110); "
                "no cross-stratum comparison applies."
            )
            continue
        if len(model_groups) != 2:
            raise ValueError(f"expected one or two identifier strata for {method}")
        # Keep the larger stratum on the left so signs are stable and easy to audit.
        model_groups.sort(key=lambda pair: (-len(pair[1]), pair[0]))
        (left_id, left), (right_id, right) = model_groups
        metric_results = []
        for metric_index, (name, (numerator, denominator)) in enumerate(METRICS.items()):
            rng = np.random.default_rng(BOOTSTRAP_SEED + metric_index)
            estimate, low, high = difference_ci(left, right, numerator, denominator, rng)
            excludes_zero = bool(low > 0 or high < 0)
            differences.append({
                "method": method, "larger_stratum": left_id, "larger_n": len(left),
                "smaller_stratum": right_id, "smaller_n": len(right), "metric": name,
                "difference_larger_minus_smaller": estimate, "ci95_low": low,
                "ci95_high": high, "zero_in_ci": not excludes_zero,
                "bootstrap_unit": "episode", "draws": BOOTSTRAP_DRAWS,
                "seed": BOOTSTRAP_SEED + metric_index,
            })
            metric_results.append((name, estimate, low, high, excludes_zero))
        primary = [result for result in metric_results if result[0] in {"SR", "feasible_task_SR"}]
        inconsistent = any(result[4] for result in primary)
        formatted = "; ".join(
            f"{name} delta={estimate:+.3f}, 95% CI [{low:+.3f}, {high:+.3f}]"
            for name, estimate, low, high, _ in primary
        )
        conclusions[method] = (
            f"{method}: {left_id} (n={len(left)}) versus {right_id} (n={len(right)}): "
            f"{'a primary difference excludes zero' if inconsistent else 'both primary differences include zero'} "
            f"under episode bootstrap ({formatted})."
        )

    rerun = []
    for method in ("direct_llm", "react", "reflexion"):
        candidates = [(identifier, rows) for (m, identifier), rows in groups.items() if m == method]
        minority_id, minority = min(candidates, key=lambda pair: len(pair[1]))
        for row in sorted(minority, key=lambda item: (item["condition"], item["world"], item["task_id"])):
            rerun.append({
                "method": method,
                "instance_id": f'{row["condition"]}|{row["world"]}|{row["task_id"]}',
                "task_id": row["task_id"], "condition": row["condition"], "world": row["world"],
                "seed": row["seed"], "returned_model_identifier": minority_id,
            })
    if len(rerun) != 73:
        raise ValueError(f"expected 73 minority-stratum episodes, got {len(rerun)}")

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "strata_table.csv", strata)
    write_csv(OUT / "strata_differences.csv", differences)
    write_csv(OUT / "rerun_instances.csv", rerun)
    write_csv(OUT / "episode_strata.csv", episodes)
    report = [
        "# Alias-stratified external-agent rescore",
        "",
        "Offline rescore only: zero new model calls and zero simulator episodes. Outcomes come from",
        "the accepted gold-protocol episode rescore; strata come from each episode's server-returned",
        "model identifier. Confidence intervals use 10,000 independent episode bootstrap draws.",
        "",
        "## Conclusions",
        "",
    ]
    report.extend(f"- {conclusions[method]}" for method in RUNS)
    report.extend([
        "",
        "## Conditional rerun list",
        "",
        "If a locked-identifier rerun is authorized, `rerun_instances.csv` contains only the minority",
        "strata: Direct 16 + ReAct 47 + Reflexion 10 = 73 episode IDs. No rerun was performed.",
        "",
        "The original logs and previously reported aggregate tables are unchanged.",
    ])
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    provenance = {
        "gold_protocol": "prereg/external_rescore_20261010.md",
        "new_LLM_calls": 0,
        "new_simulator_episodes": 0,
        "bootstrap_unit": "episode",
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "bootstrap_seed_base": BOOTSTRAP_SEED,
        "input_sha256": source_hashes,
        "identifier_counts": {method: dict(counts) for method, counts in identifier_counts.items()},
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"identifier_counts": provenance["identifier_counts"],
                      "conclusions": conclusions, "rerun_episodes": len(rerun)}, indent=2))


if __name__ == "__main__":
    main()
