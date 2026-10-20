import json

from arbiter.evaluation.p6_matrix import (
    build_matrix,
    development_tasks,
    judge_gate,
    one_sided_conflict_ids,
    coverage_conflict_ids,
    result_subsets,
    result_subsets_with_control,
    validate_coverage,
)
from arbiter.evaluation.reporting import EpisodeRecord


def tasks():
    return [{"id": f"dev_{index}"} for index in range(8)]


def records(arbiter_htr=0.0, arbiter_sr=1.0, probes=3.0,
            arbiter_htr_commit=None, naive_htr_commit=None):
    rows = []
    for job in build_matrix(tasks()):
        is_arbiter = job.method == "arbiter"
        htr = arbiter_htr if is_arbiter else 0.25
        commit = (arbiter_htr_commit if is_arbiter else naive_htr_commit)
        rows.append(EpisodeRecord(
            job.method, job.condition, job.world, job.task_id, job.seed,
            (f"cf_{job.task_id}",), arbiter_sr if is_arbiter else 1.0,
            htr, probes if is_arbiter else 0.0, htr_commit=commit,
        ))
    return rows


def test_p6_matrix_is_exactly_144_cells():
    jobs = build_matrix(tasks())
    assert len(jobs) == 144
    assert len({(job.condition, job.task_id, job.world, job.method, job.seed) for job in jobs}) == 144
    validate_coverage(jobs, records())


def test_development_snapshot_does_not_require_full_task_catalog(tmp_path):
    snapshot = tmp_path / "dev.json"
    snapshot.write_text(json.dumps([
        {"id": task_id, "target": {"item": "x", "count": 1}}
        for task_id in (
            "eff_007", "fea_002", "fea_009", "pla_001", "pla_003",
            "pla_008", "pla_009", "pla_011",
        )
    ]))
    resources = tmp_path / "resources.json"
    resources.write_text(json.dumps({
        "standard_inventory": [{"name": "stick", "count": 2}],
    }))
    selected = development_tasks(snapshot, resources)
    assert len(selected) == 8
    assert selected[0]["standard_items"] == [{"name": "stick", "count": 2}]


def test_p6_gate_is_scripted_not_subjective():
    assert judge_gate(records())["decision"] == "PASS"
    assert judge_gate(records(arbiter_htr=0.15))["decision"] == "MARGINAL"
    assert judge_gate(records(arbiter_htr=0.22))["decision"] == "FAIL"
    assert judge_gate(records(arbiter_sr=0.99))["decision"] == "FAIL"
    assert judge_gate(records(probes=4.01))["decision"] == "PASS"


def test_p6_gate_uses_commit_htr_and_reports_all_action_comparison():
    result = judge_gate(records(
        arbiter_htr=0.20, arbiter_htr_commit=0.0, naive_htr_commit=0.25,
    ))
    assert result["decision"] == "PASS"
    assert result["HTR_commit_gap"] == 0.25
    assert result["HTR_all_decision"] == "MARGINAL"
    assert abs(result["HTR_all_gap"] - 0.05) < 1e-12


def test_result_subsets_follow_frozen_one_sided_ids(tmp_path):
    freeze = tmp_path / "freeze.md"
    freeze.write_text("The `one_sided` subset is:\n\n- `cf_0123456789abcdef`: reason.\n\n## Next\n")
    ids = one_sided_conflict_ids(freeze)
    rows = records()
    rows[0] = EpisodeRecord(
        rows[0].method, rows[0].condition, rows[0].world, rows[0].task_id,
        rows[0].seed, ("cf_0123456789abcdef",), rows[0].sr, rows[0].htr,
        rows[0].probes,
    )
    subsets = result_subsets(rows, ids)
    assert len(subsets["all"]) == 144
    assert len(subsets["one_sided"]) == 1
    assert len(subsets["two_sided"]) == 143


def test_corrected_result_subsets_keep_empty_conflicts_as_control():
    rows = [
        EpisodeRecord("m", "c", "w", "control", 1, (), 0, 0, 0),
        EpisodeRecord("m", "c", "w", "two", 1, ("cf_two",), 0, 0, 0),
        EpisodeRecord("m", "c", "w", "one", 1, ("cf_one",), 0, 0, 0),
    ]
    subsets = result_subsets_with_control(rows, frozenset({"cf_one"}))
    assert [row.task_id for row in subsets["control"]] == ["control"]
    assert [row.task_id for row in subsets["two_sided"]] == ["two"]
    assert [row.task_id for row in subsets["one_sided"]] == ["one"]
    assert len(subsets["all"]) == 3


def test_coverage_conflict_ids_reads_frozen_csv(tmp_path):
    coverage = tmp_path / "coverage.csv"
    coverage.write_text(
        "conflict_id,coverage\ncf_0000000000000001,two_sided\n"
        "cf_0000000000000002,one_sided\n"
    )
    assert coverage_conflict_ids(coverage, "one_sided") == {
        "cf_0000000000000002"
    }
