# WebCraft

WebCraft is a deterministic, text-level Minecraft mechanism benchmark for
developing and testing agents that act under versioned web evidence. This public
foundation contains the semantic simulator, three frozen version truth tables,
task and conflict data, formal retrieval indexes, structured claims caches, the
oracle planner, metrics, and unit tests.

The simulator exposes only the following public methods:
`inventory`, `mine`, `craft_grid`, `smelt`, `check_name`, `report_version`,
`submit`, and `search`. It intentionally does not expose navigation, coordinates,
world version, hidden state, or server internals.

## Quick start

Use Python 3.10 or newer. The deterministic simulator is CPU-only.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q tests/sim oracle/test_planner.py
PYTHONPATH=. python - <<'PY'
from sim.runner import load_tasks, run_oracle

result = run_oracle(load_tasks()['eff_007'], '1.21.11')
assert result['success'], result
print(result['primary_cost'])
PY
```

## Method-development interfaces

- `sim/semantic_agent.py`: deterministic eight-method action facade.
- `corpus/search_backend.py`: read-only BM25 retrieval over formal indexes.
- `corpus/claims_cache.py`: public structured claim-cache contract.
- `oracle/planner.py`: frozen quantity-aware reference planner.
- `metrics/episode.py`: episode-level metrics.
- `data/tasks.jsonl` and `data/conflicts.jsonl`: fixed task and conflict inputs.

The included `corpus/indexes/formal*` and `corpus/claims_cache/` directories are
derived public benchmark artifacts. Raw web snapshots, private audit files,
credentials, server assets, generated reports, and release bundles are excluded.

## Scope

This is a method-development and deterministic evaluation foundation, not a
continuous-control environment and not a live Minecraft server. Its semantic
abstractions and declared differences are documented in `sim/README.md`.

## Provenance and licensing

`PUBLIC_EXPORT_MANIFEST.md` and `PUBLIC_EXPORT_FILES.sha256` record the source
commit and hashes of every exported file. The upstream source declares that authors must select release
licenses for code and derived data before formal redistribution; no license is
asserted by this export. See `corpus/PROVENANCE.md` for corpus provenance.
