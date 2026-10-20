from collections import Counter

import pytest

from scripts.export_human_audit_v3 import quotas


def test_largest_remainder_preserves_total_and_unique_capacities():
    counts = Counter({'propagation': 12, 'query': 28, 'execution': 25,
                      'nonspecific': 34, 'S4_no_contact_abstention': 20})
    result = quotas(counts)
    assert result == {'propagation': 8, 'query': 19, 'execution': 17,
                      'nonspecific': 23, 'S4_no_contact_abstention': 13}
    assert sum(result.values()) == 80
    assert all(result[key] <= counts[key] for key in counts)


def test_allocation_above_population_cannot_silently_resample_with_replacement():
    counts = Counter({'propagation': 1, 'query': 1, 'execution': 1,
                      'nonspecific': 1, 'S4_no_contact_abstention': 1})
    with pytest.raises(RuntimeError, match='capacity'):
        quotas(counts, total=80)
