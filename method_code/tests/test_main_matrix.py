import pytest

from arbiter.evaluation.main_matrix import MAIN_CELLS, build_main_matrix


TASK_IDS = [f"eval_{index:02d}" for index in range(34)]


def test_main_matrix_cardinality_and_registered_cells():
    baseline = build_main_matrix(TASK_IDS, "baselines_only")
    full = build_main_matrix(TASK_IDS, "baselines_plus_arbiter")
    assert len(baseline) == 34 * 11 * 8 * 2
    assert len(full) == 34 * 11 * 9 * 2
    assert {(job.condition, job.world) for job in full} == set(MAIN_CELLS)
    assert {job.seed for job in full} == {20261006, 20261007}


def test_one_cell_estimate_matrix_and_development_exclusion():
    jobs = build_main_matrix(TASK_IDS, "baselines_plus_arbiter", cells=MAIN_CELLS[:1])
    assert len(jobs) == 34 * 9 * 2
    with pytest.raises(ValueError, match="development tasks"):
        build_main_matrix(TASK_IDS[:-1] + ["pla_001"], "baselines_only")
