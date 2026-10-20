from arbiter.evaluation.confirmatory import confirmatory_decision, paired_cluster_difference
from arbiter.evaluation.reporting import EpisodeRecord


def row(method: str, task: str, conflict: str, htr: float, sr: float) -> EpisodeRecord:
    return EpisodeRecord(
        method=method, condition="mixed", world="1.19", task_id=task,
        seed=20261006, conflict_mechanism_ids=(conflict,), sr=sr, htr=htr,
        probes=0, htr_commit=htr,
    )


def test_confirmatory_gate_uses_paired_shell_and_naive_differences():
    records = []
    for index in range(10):
        task = f"eval_{index}"
        conflict = f"cf_{index}"
        records.extend([
            row("naive_rag", task, conflict, 1, 1),
            row("arbiter_shell_naive", task, conflict, 1, 1),
            row("arbiter", task, conflict, 0, 1),
        ])
    result = confirmatory_decision(records)
    assert result["primary_i"] is True
    assert result["primary_ii"] is True
    assert result["outcome"] == "both"
    assert result["shell_minus_arbiter_HTR_commit"]["difference"] == 1


def test_confirmatory_bootstrap_uses_one_control_cluster():
    records = []
    for task in ("con_001", "con_002"):
        for method, htr in (("naive_rag", 1), ("arbiter", 0)):
            records.append(EpisodeRecord(
                method=method, condition="mixed", world="1.19", task_id=task,
                seed=20261006, conflict_mechanism_ids=(), sr=1, htr=htr,
                probes=0, htr_commit=htr,
            ))

    result = paired_cluster_difference(records, "naive_rag", "arbiter")
    assert result["mechanism_clusters"] == 1
