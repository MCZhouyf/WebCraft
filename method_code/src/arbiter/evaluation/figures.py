"""Paper figures from frozen table outputs and the existing P3 grid."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def plot_htr_probes(main_table: Path, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = _rows(main_table)
    conditions = sorted({row["condition"] for row in rows})
    colors = plt.get_cmap("tab10")
    figure, axis = plt.subplots(figsize=(7.2, 4.8))
    for index, condition in enumerate(conditions):
        selected = [row for row in rows if row["condition"] == condition]
        probe_field = "probe_count" if "probe_count" in selected[0] else "probes"
        htr_field = "HTR_commit" if "HTR_commit" in selected[0] else "HTR"
        axis.scatter(
            [float(row[probe_field]) for row in selected],
            [float(row[htr_field]) for row in selected],
            label=condition, color=colors(index), s=48,
        )
        for row in selected:
            axis.annotate(row["method"], (float(row[probe_field]), float(row[htr_field])),
                          xytext=(4, 4), textcoords="offset points", fontsize=7)
    axis.set(xlabel="Mean probes", ylabel="Committed harmful trajectory rate",
             ylim=(-0.03, 1.03))
    axis.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    figure.savefig(output, dpi=200)
    plt.close(figure)


def contextladder_slice(grid: Path) -> list[dict[str, float | str]]:
    selected = [
        row for row in _rows(grid)
        if float(row["epsilon"]) == 0.1 and row["prevalence"] == "uniform"
        and row["structure"] == "staircase" and float(row["eta"]) == 0.0
        and int(row["universal_plan"]) == 0
        and row["method"] in {"arbiter", "sequential", "majority"}
    ]
    grouped: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
    for row in selected:
        grouped[(row["method"], int(row["k"]))].append(row)
    return [
        {
            "method": method, "k": k, "log2_k": float(np.log2(k)),
            "mean_probes": float(np.mean([float(row["probes"]) for row in rows])),
            "accuracy": float(np.mean([float(row["success"]) for row in rows])),
        }
        for (method, k), rows in sorted(grouped.items())
    ]


def plot_contextladder(grid: Path, output: Path, slice_csv: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = contextladder_slice(grid)
    slice_csv.parent.mkdir(parents=True, exist_ok=True)
    with slice_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    figure, probe_axis = plt.subplots(figsize=(7.2, 4.8))
    labels = {"arbiter": "ARBITER", "sequential": "Verify-All"}
    for method in ("arbiter", "sequential"):
        selected = [row for row in rows if row["method"] == method]
        probe_axis.plot(
            [row["log2_k"] for row in selected],
            [row["mean_probes"] for row in selected], marker="o", label=labels[method],
        )
    accuracy_axis = probe_axis.twinx()
    majority = [row for row in rows if row["method"] == "majority"]
    accuracy_axis.plot(
        [row["log2_k"] for row in majority], [row["accuracy"] for row in majority],
        color="black", linestyle="--", marker="s", label="Majority accuracy",
    )
    probe_axis.set(xlabel="log2(K)", ylabel="Mean probes")
    accuracy_axis.set(ylabel="Majority accuracy", ylim=(-0.03, 1.03))
    handles_a, labels_a = probe_axis.get_legend_handles_labels()
    handles_b, labels_b = accuracy_axis.get_legend_handles_labels()
    probe_axis.legend(handles_a + handles_b, labels_a + labels_b, frameon=False)
    figure.tight_layout()
    figure.savefig(output, dpi=200)
    plt.close(figure)
