import csv
import json
from pathlib import Path

from arbiter.evaluation.figures import contextladder_slice, plot_contextladder, plot_htr_probes
from arbiter.evaluation.reporting import (
    EpisodeRecord,
    aggregate_main_table,
    load_episode_records,
    write_main_table,
)


def event():
    return {"schema_version": 2, "method": "mine", "cost": {"primary": 1}}


def episode(method, condition, task, conflict, sr, htr, probes, searches=0,
            htr_commit=None, probe_cost=0):
    return {
        "schema_version": 2, "record_type": "episode", "method": method,
        "condition": condition, "world": "1.16.5", "task_id": task, "seed": 2026,
        "conflict_mechanism_ids": [conflict], "events": [event()],
        "metrics": {"SR": sr, "HTR": htr, "HTR_all": htr,
                    "HTR_commit": htr if htr_commit is None else htr_commit},
        "probe_count": probes, "probe_cost": probe_cost,
        "reader": {"search_count": searches},
    }


def test_main_table_reads_v2_and_cluster_bootstraps(tmp_path):
    path = tmp_path / "episodes.jsonl"
    raw = [
        episode("arbiter", "mixed", "a", "cf_a", 1, 0, 2, 8),
        episode("arbiter", "mixed", "b", "cf_a", 1, 0, 1),
        episode("naive_rag", "mixed", "a", "cf_a", 1, 1, 0),
        episode("naive_rag", "mixed", "c", "cf_b", 0, 1, 0),
    ]
    path.write_text("\n".join(json.dumps(row) for row in raw) + "\n")
    records = load_episode_records(path)
    table = aggregate_main_table(records, iterations=100, seed=7)
    arbiter_row = next(row for row in table if row["method"] == "arbiter")
    assert arbiter_row["mechanism_clusters"] == 1
    assert arbiter_row["SR"] == 1
    assert arbiter_row["HTR_commit"] == 0
    assert arbiter_row["HTR_all"] == 0
    assert arbiter_row["probe_count"] == 1.5
    assert arbiter_row["searches"] == 4
    output = tmp_path / "tables"
    write_main_table(records, output, iterations=100, seed=7)
    assert (output / "main_table.csv").is_file()
    assert (output / "main_table.tex").is_file()
    assert (output / "world_breakdown.csv").is_file()
    assert "95\\% CI" in (output / "main_table.tex").read_text()


def test_episode_rejects_legacy_action():
    row = episode("arbiter", "mixed", "a", "cf_a", 1, 0, 1)
    row["events"][0].pop("schema_version")
    try:
        EpisodeRecord.from_dict(row)
    except ValueError as exc:
        assert "non-schema-v2" in str(exc)
    else:
        raise AssertionError("legacy event was accepted")


def test_control_episodes_share_the_frozen_control_cluster():
    left = EpisodeRecord.from_dict(
        episode("arbiter", "mixed", "a", "cf_a", 1, 0, 0)
    )
    control_a_raw = episode("arbiter", "mixed", "con_001", "cf_a", 1, 0, 0)
    control_a_raw["conflict_mechanism_ids"] = []
    control_b_raw = episode("arbiter", "mixed", "con_002", "cf_a", 1, 0, 0)
    control_b_raw["conflict_mechanism_ids"] = []
    control_a = EpisodeRecord.from_dict(control_a_raw)
    control_b = EpisodeRecord.from_dict(control_b_raw)

    assert control_a.conflict_mechanism_ids == ()
    table = aggregate_main_table([left, control_a, control_b], iterations=100)
    assert table[0]["mechanism_clusters"] == 2


def test_contextladder_frozen_slice_and_plots(tmp_path):
    root = Path(__file__).parents[1]
    grid = root / "experiments/contextladder/results/grid_results.csv"
    rows = contextladder_slice(grid)
    arbiter = [row for row in rows if row["method"] == "arbiter"]
    assert [row["mean_probes"] for row in arbiter] == [1, 2, 3, 4, 5]

    table = tmp_path / "main.csv"
    with table.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["method", "condition", "HTR_commit", "probe_count"])
        writer.writeheader()
        writer.writerows([
            {"method": "arbiter", "condition": "mixed", "HTR_commit": 0.1, "probe_count": 2},
            {"method": "naive_rag", "condition": "mixed", "HTR_commit": 0.8, "probe_count": 0},
        ])
    plot_htr_probes(table, tmp_path / "figure2.png")
    plot_contextladder(grid, tmp_path / "figure3.png", tmp_path / "slice.csv")
    assert (tmp_path / "figure2.png").stat().st_size > 1000
    assert (tmp_path / "figure3.png").stat().st_size > 1000
