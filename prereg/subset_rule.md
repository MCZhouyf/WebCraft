# WebCraft Stage 1 Preregistration

Frozen before any LLM-agent experiment. B1 deterministic reset and leakage validation has completed; B2-B4 live oracle and scripted stale-agent checks are pending because the mandatory memory gate did not pass.

## Evaluation Subset

The method-paper evaluation subset is every row in `data/tasks.jsonl` with `split=eval`. It contains 34 tasks:

| Task type | Evaluation | Development |
|---|---:|---:|
| Planning | 7 | 5 |
| Feasibility | 8 | 2 |
| Efficiency | 7 | 1 |
| Control | 12 | 0 |
| Total | 34 | 8 |

The eight development tasks may be used for pilot experiments and tuning only. They may not be moved into the evaluation subset. The planning subset is smaller than the target because strict complete-path conflict reuse at cap 6 permits only 12 planning tasks after reserving distinct feasibility and efficiency targets. No inclusion threshold was relaxed to increase that count.

## Worlds And Corpus Conditions

Evaluation worlds are Minecraft Java 1.16.5, 1.19, and 1.21.11. Every world uses the same absolute arena coordinates, standard starting inventory, common-ID resource array, frozen semantic interface, action budget, and cost model. Environment truth comes only from the corresponding files under `truth/`; neither truth labels nor the current world version are exposed to the agent.

Corpus conditions are:

1. **Matched:** corpus snapshot corresponding to the current world.
2. **Mismatched old-to-new:** 1.16.5-time corpus in the 1.21.11 world.
3. **Mismatched new-to-old:** 1.21.11-time corpus in the 1.16.5 world.
4. **Mixed:** all three historical snapshots plus the current-page snapshot and dated provenance.
5. **Contaminated:** the mixed corpus plus a fixed 0.3 proportion of locally generated counterfactual pages marked privately as synthetic; the marker is never returned by search.

Each world-corpus-method combination uses exactly two evaluation seeds: `20261006` and `20261007`. The task order is independently shuffled per seed; arena state and task truth do not change with seed. Development-set construction used seed `20261005` and is already frozen.

## Metrics

Primary metrics are success rate (SR), committed historical-truth reliance
(`HTR_commit`), and all-action historical-truth reliance (`HTR_all`). For an
episode, `HTR_all=1` exactly when the original HTR definition finds at least one
attempted semantic action that relies on a mechanism false in the active world
and true in another benchmark world. `HTR_commit` applies the same test after
excluding action-log records whose agent-declared `intent` is exactly `probe`.
Missing or any other `intent` is treated as a committed action. Thus `HTR_all`
is definitionally identical to the previously registered HTR, and the legacy
`HTR` output key remains its compatibility alias.

The agent, not the evaluator, writes `intent`; the evaluator reads it without
checking whether the declaration is truthful. Every action still contributes
its full primary cost and remains eligible for OVR regardless of intent.
Reports therefore include `HTR_commit`, `HTR_all`, probe count, and probe cost
as separate columns. This prevents an all-probe declaration from hiding either
harmful reliance under `HTR_all` or the cost and over-verification burden.

All metrics are computed in two separate reading modes. `all_public` is the
main/default mode and exposes every public cache claim without extractor-based
filtering. `llm_only` is the control mode and exposes only claims from the
frozen LLM extractor. Each mode receives a complete, separate table suite; rows
are not pooled. The P6 gate is determined exclusively from `all_public`, while
`llm_only` is reported only as a control.

Secondary metrics are identification cost (IC), indirect assertion accuracy (IAA), feasibility precision/recall/F1 and false-abstention rate, over-planning rate (OPR), and over-verification rate (OVR). OPR remains a secondary metric because A6 generated eight efficiency tasks, above the four-task threshold. Primary semantic-action cost is defined by `config/cost_model.json`; wall time, consumed items, and net displacement are auxiliary only.

All point estimates are accompanied by a task-level mechanism-cluster bootstrap with 10,000 resamples and bootstrap seed `20261005`. A task's cluster is its least prevalent active grade-A conflict mechanism in the frozen evaluation set; ties use lexicographic conflict ID. Tasks with no active grade-A mechanism use the fixed cluster `control`. Whole clusters, not individual task rows, are sampled with replacement. This clustered analysis is mandatory because the planning reuse cap was relaxed from 4 to 6 and several tasks share ore-drop mechanisms.

## Live-Oracle Exclusion Rule

Live validation is performed without inspecting any evaluated method result. For each evaluation task and world:

1. Reset to the frozen arena and execute the frozen oracle plan once.
2. A transport, startup, or timeout failure receives exactly one clean-reset retry with the same plan and configuration.
3. If both attempts fail at the same semantic action, record the action, observed public error, and truth evidence. Do not edit the plan, truth, interface, task package, or expected result.
4. Exclude the entire task from all method comparisons only when at least one world marked feasible fails both identical oracle attempts, or when B3 demonstrates that a required frozen truth assertion is inconsistent with the live game.
5. Do not exclude a task because an evaluated agent fails, abstains, exceeds budget, encounters stale information, or performs poorly. Do not replace an excluded task.

Every exclusion is reported by task ID, world, reason, and both evidence-log hashes. Counts before and after exclusion are reported by task type. Expected-infeasible task/world pairs are retained unless B3 falsifies their relevant frozen truth.

## Scripted-Agent Expectations

`data/stale_agent_expectations.jsonl` freezes the per-task expected result for executing a 1.16.5-truth policy in 1.21.11. B4 compares live results row by row. New-route-only tasks may have HTR zero when an old route remains valid; replacement, removal, rename, feasibility disagreement, and replay mismatch are treated as stale behavior. No corpus-wide near-100% HTR target is used.

## LLM Gateway Provenance

LLM agents use an OpenAI-compatible gateway with requested model ID `glm-5.2`; endpoint and credential are loaded only from the private `env/llm.env`. Every response record must include the provider-returned `model` field. A missing or changed field stops the run and leaves subsequent cells unexecuted.

After every 200 ordinary model calls, insert one fixed canary request with identical input, temperature 0, and fixed decoding parameters. Store its request hash, returned model field, token usage, response hash, and latency. A model-field change or non-identical deterministic canary response is reported as a gateway-integrity event and stops the affected run; it is never silently pooled with earlier responses.

## Frozen Hash Manifest

| File | SHA-256 |
|---|---|
| `config/cost_model.json` | `10b481041b5489a11eb580a968ff8d15fb62aeb25179cbe43ee02ce910a4e80b` |
| `config/resource_model.json` | `c8b533f8922d42a38194922f68b164e453a627407840ff5351cf8edd1092b312` |
| `config/recipe_type_policy.json` | `b36749ef2a4a63ad40ddd9e17ff6e02cad027dd388a850c2cb46de0ff59cf732` |
| `data/conflicts.jsonl` | `e655dc89e7a51c9c2973649b0c4cd605f56efabf989499e90bfece7411db7f44` |
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` |
| `data/stale_agent_expectations.jsonl` | `2bd2f75ddbdbd6302595cabff29c91e7072d0f97e0e711decfe1ead4e1f4c832` |

The applied arena configuration has SHA-256 `e796daacbe1639b2630941bc0a13615cfb77fd39245a42dc162a3bc3d87ef4e6`. B1 produced one identical reset-state hash across all nine version/repetition combinations: `8eddc2c10cc3b9d5dfdd65045c55b414d6bf2bc180ce9b45c410a4aa3f5274f3`.

Any post-registration change to a frozen file requires a new commit and an explicit amendment in `reports/STAGE1_REPORT.md` before further agent experiments.
