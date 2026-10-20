"""Runner integrity checks on synthetic records; no environment execution."""
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('a22_development', ROOT / 'scripts/run_a22_development.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_seed_and_runtime_only_are_excluded_from_determinism_comparison():
    a = {'seed': 1, 'elapsed_seconds': 3, 'peak_rss_kib': 100, 'metrics': {'SR': 0}}
    b = {'seed': 2, 'elapsed_seconds': 8, 'peak_rss_kib': 200, 'metrics': {'SR': 0}}
    assert module.canonical(a) == module.canonical(b)
    b['metrics']['SR'] = 1
    assert module.canonical(a) != module.canonical(b)


def test_job_output_key_separates_B_and_method():
    context = {'condition': 'synthetic', 'task': {'id': 'dev'}, 'world': 'sandbox'}
    keys = {module.job_key(context, method, B)
            for method, B in [('naive_rag', 0), ('pf_rag', 1), ('pf_rag', 3), ('pf_rag', 5)]}
    assert len(keys) == 4


def test_atomic_output_is_parseable(tmp_path):
    import json
    path = tmp_path / 'nested' / 'status.json'
    module.dump(path, {'status': 'RUNNING'})
    assert json.loads(path.read_text()) == {'status': 'RUNNING'}
    assert not path.with_suffix('.json.tmp').exists()
