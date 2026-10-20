import numpy as np

from arbiter.contextladder import _probe_policy, _signatures


def test_staircase_needs_log2_k_noiseless_probes():
    rng = np.random.default_rng(7)
    signatures = _signatures(8, "staircase", rng)
    probes, guess = _probe_policy(
        signatures, 5, np.full(8, 1 / 8), 0.0, rng,
        decision_driven=True, universal_plan=False,
    )
    assert probes == 3
    assert guess == 5


def test_universal_plan_needs_no_probe():
    rng = np.random.default_rng(7)
    signatures = _signatures(8, "random_separable", rng)
    probes, _ = _probe_policy(
        signatures, 2, np.full(8, 1 / 8), 0.1, rng,
        decision_driven=True, universal_plan=True,
    )
    assert probes == 0

