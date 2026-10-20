# External-agent capability controls and condition extension

Date: 2026-10-13

Status: post-review supplementary experiment, preregistered before any new
model call. This experiment does not replace or modify any frozen method,
prompt, parameter, episode, score, or previously reported number.

## Common protocol

- Methods: `direct_llm` and `react` only.
- Requested and required returned model identifier: `glm-5.2`, served through
  the Sensenova OpenAI-compatible endpoint. Every response identifier is
  recorded. A response with another identifier, or two identifiers within one
  method, stops that method before another episode is accepted.
- The committed `public_system_prompt`, JSON request construction, public tool
  descriptions, temperature 0, disabled thinking, 4,096 completion-token cap,
  40 model-call limit, and 90 semantic-action budget are reused byte for byte
  from the first external-agent run. Direct retains two trace entries and
  ReAct retains eight. No prompt repair or method-specific exception is
  allowed after this registration.
- The agent receives only the public task goal/inventory, public observations,
  and six-field search rows. During a run it cannot access truth files, task
  metadata, conflict labels, candidate versions, claims-cache rows, compiler
  output, or evaluator mappings. Credentials are read only from process
  environment, and are absent from artifacts, logs, reports, and commits.
- Action and search events use the existing deterministic WebCraft simulator
  and public semantic adapter. WebCraft is read-only. Existing frozen files
  are not edited. No GPU or non-task process is used or modified.
- The gold feasibility and terminal scoring protocol is exactly
  `prereg/external_rescore_20261010.md`. Overall SR, feasible-task SR,
  `HTR_all`, calls, tokens, semantic actions, and terminal categories are
  reported. Network/service failures are excluded and may be retried at most
  twice without resetting or selecting tasks; model refusal is an episode
  outcome.

## Fixed matrix

The seed is `20261006`. The five single-world cells are
`matched_1.16.5`/1.16.5, `matched_1.19`/1.19,
`matched_1.21.11`/1.21.11, `mismatched_old2new`/1.21.11, and
`mismatched_new2old`/1.16.5.

1. `oracle_knowledge`: 22 conflict-bearing evaluation tasks x five cells x
   two methods = 220 episodes. Before the agent starts, evaluator-only code
   selects the target-world oracle-path mechanisms and projects the true gold
   assertions into ordinary six-field search rows. Text is rendered in the
   ordinary corpus style; source and timestamp are retained; truth vectors and
   all evaluator fields are removed. A deterministic lexical search over this
   episode-local public package returns at most the requested `k`. The runtime
   adapter holds only these projected rows. This is a capability control, not
   a reading mode and not part of the main table.
2. `control_all_public`: 12 evaluation control tasks with empty conflict sets
   x five cells x two methods = 120 episodes. Search uses the unchanged
   `all_public` index for each cell.
3. `mixed` and `polluted`: 22 conflict-bearing tasks x three target worlds x
   two conditions x two methods = 264 episodes. Search uses the unchanged
   formal `all_public` indexes.

Total: 604 episodes. Segment 1 (`oracle_knowledge`) and segment 2
(`control_all_public`) run and export before segment 3 (`mixed`/`polluted`).
Each completed segment is committed as an intermediate export. No task is
added, removed, or replaced based on outcomes.

## Fixed interpretation

- **J1:** For each method, test whether feasible-task SR under
  `oracle_knowledge` is at least 50%. If yes, report that the result is
  consistent with a knowledge acquisition/judgment bottleneck rather than a
  planning/interface bottleneck. If no, report a basic capability bottleneck
  and carry it into Section 5.2.
- **J2:** Compare each external method's control-task SR with pipeline Naive
  RAG on the identical 12 tasks and five cells. Report both values and their
  paired difference; do not turn this control into a method ranking.
- **J3:** For each method, compare feasible-task SR and `HTR_all` in `mixed`
  and `polluted` with its three matched cells. For polluted cells, identify
  synthetic chunks only in evaluator-side replay and count whether an action
  consistent with their assertion appeared in the subsequent action log.
  Synthetic provenance is never returned to the agent.

The final outputs are
`reports/main/post_review/external_controls/main_table.csv`,
`pollution_contact.csv`, and `REPORT.md`. They include actual returned model
identifiers and total calls. The report states J1-J3 exactly from the computed
tables without changing these thresholds or expanding the matrix.
