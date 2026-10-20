# Baseline and external-agent source

This is the source release, not a claim that all historical experiments can be
replayed without their separately archived inputs and logs.

## Source map

| Method / component | Implementation |
| --- | --- |
| Naive, Majority, TruthFinder, Parametric | `src/arbiter/agent/aggregation.py` |
| Shared planner and execution shell | `src/arbiter/agent/planning.py`, `core.py` |
| Shared reader and compiler | `src/arbiter/evaluation/p6_execution.py`, `claim_compiler.py`, `operator_compiler.py` |
| ARBITER | `src/arbiter/agent/`, `src/arbiter/inference/` |
| PF-RAG | `src/arbiter/agent/pf_rag.py`, `scripts/run_a22_*.py` |
| CP-RAG | `../scripts/run_cp_rag_20261016.py` (`cp_solve`), `../scripts/w14_runtime.py` |
| VB-RAG | `../scripts/w13_runtime.py` (`VBRAG`), `../scripts/w14_runtime.py` |
| Direct, ReAct, Reflexion, ADAM/XENON adaptations, optional Voyager | `src/arbiter/evaluation/external_agents.py` |
| External prompts and model gateway | `src/arbiter/evaluation/external_agents.py`, `src/arbiter/llm/gateway.py` |
| External orchestration | `scripts/run_external_agents.py`, `run_external_parallel.py` |
| Main matrix / analysis | `scripts/run_main.py`, `make_main_table.py` |

The complete committed `src`, `scripts`, `tests`, `config`, `docs`, and `prereg`
directories are provided from ARBITER source revision `c7c4fb7`. The frozen prior
bundle and PF-RAG development selection manifests are included, but raw run logs,
human annotations, credentials and private input files are not.
WebCraft helper scripts come from revision
`9a27a833d7967fba54949f34bacb52089024ba80`.

## Install and test

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e './method_code[dev]'
export PYTHONPATH="$PWD:$PWD/method_code:$PWD/method_code/src:$PWD/method_code/scripts:$PWD/scripts"
python -m pytest -q method_code/tests/test_aggregation.py \
  method_code/tests/test_planning.py method_code/tests/test_external_agents.py \
  method_code/tests/test_a22_pf_policy.py
```

Use `GatewayClient` with environment-provided configuration; never add endpoint
URLs or credentials to tracked configuration. Tests use mocks and do not require
an API key. The exported A21 credential loader reads `ARB_LLM_BASE_URL` and
`ARB_LLM_API_KEYS_JSON` from the environment; it does not read chat/session files.
The two historical gateway preflight tools additionally require
`WC_LLM_AUTHORIZED_BASE_URL` to enforce the operator's endpoint allowlist.

## Portability and experimental boundaries

Agent policy code is retained from the research snapshot. Historical orchestration
scripts may refer to the original sibling checkout, result files, lock, or frozen
pins. Provide explicit paths where supported. Do not disable a pin check merely
to make a historical command run: create and record a new development pin for a
new experiment, leaving historical registrations intact.

`config/webcraft_pin.json` and PF-RAG's historical source hash gate describe the
original checkout, not this repackaged Git tree. Some analysis/replay tests need
the original logs and are not included in the standalone unit-test command.
Archived server provisioning scripts are source only; do not run them on a shared
machine. The public deterministic simulator does not need a live game server.

Keep evaluator truth access outside the agent process. CP/VB public workers accept
public evidence and execution observations; do not use the retired CDS evaluation
adapter as a new agent interface. The historical CDS first-observation knowledge
retention behavior is preserved, not silently corrected in this publication.

No experiment outputs are regenerated or uploaded by this release. W18/W19
source and outputs are not part of the selected ARBITER snapshot. Publication
changes consist of these instructions, package-relative CP/VB worker paths,
environment-only credential loading, and removal of hard-coded gateway allowlists.
