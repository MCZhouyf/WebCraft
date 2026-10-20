# A7 diagnostic controls

Date: 2026-10-16

Status: preregistered diagnostic-only experiment. These controls use target
world truth-derived facts and are not ordinary baselines or inputs to any
frozen result. They never modify, replace, or enter a reported main-table
number. The only permitted model is the configured `glm-5.2` gateway. Each
response must return server model identifier exactly `glm-5.2`; a second or
different identifier stops the current run. Credentials and endpoints remain
environment-only and are never logged or committed.

## Fixed sampling

Selection seed: `20261016`. The source universe is the saved first-run Direct
110 matrix. It is stratified by feasible status, task type, world, and matched
versus mismatched condition; seed-shuffled round-robin selection fills 24
feasible and 8 infeasible cells. No task is replaced for model performance.

```json
[
 {"task_id":"eff_002","task_type":"efficiency","world":"1.16.5","condition":"matched_1.16.5","feasible":true},
 {"task_id":"eff_005","task_type":"efficiency","world":"1.16.5","condition":"matched_1.16.5","feasible":true},
 {"task_id":"eff_004","task_type":"efficiency","world":"1.16.5","condition":"mismatched_new2old","feasible":true},
 {"task_id":"eff_008","task_type":"efficiency","world":"1.16.5","condition":"mismatched_new2old","feasible":true},
 {"task_id":"eff_005","task_type":"efficiency","world":"1.19","condition":"matched_1.19","feasible":true},
 {"task_id":"eff_008","task_type":"efficiency","world":"1.19","condition":"matched_1.19","feasible":true},
 {"task_id":"eff_005","task_type":"efficiency","world":"1.21.11","condition":"matched_1.21.11","feasible":true},
 {"task_id":"eff_008","task_type":"efficiency","world":"1.21.11","condition":"matched_1.21.11","feasible":true},
 {"task_id":"eff_001","task_type":"efficiency","world":"1.21.11","condition":"mismatched_old2new","feasible":true},
 {"task_id":"eff_003","task_type":"efficiency","world":"1.21.11","condition":"mismatched_old2new","feasible":true},
 {"task_id":"fea_003","task_type":"feasibility","world":"1.16.5","condition":"matched_1.16.5","feasible":true},
 {"task_id":"fea_007","task_type":"feasibility","world":"1.16.5","condition":"matched_1.16.5","feasible":true},
 {"task_id":"fea_003","task_type":"feasibility","world":"1.16.5","condition":"mismatched_new2old","feasible":true},
 {"task_id":"fea_007","task_type":"feasibility","world":"1.16.5","condition":"mismatched_new2old","feasible":true},
 {"task_id":"fea_003","task_type":"feasibility","world":"1.19","condition":"matched_1.19","feasible":true},
 {"task_id":"fea_008","task_type":"feasibility","world":"1.21.11","condition":"matched_1.21.11","feasible":true},
 {"task_id":"fea_010","task_type":"feasibility","world":"1.21.11","condition":"matched_1.21.11","feasible":true},
 {"task_id":"fea_004","task_type":"feasibility","world":"1.21.11","condition":"mismatched_old2new","feasible":true},
 {"task_id":"fea_006","task_type":"feasibility","world":"1.21.11","condition":"mismatched_old2new","feasible":true},
 {"task_id":"pla_012","task_type":"planning","world":"1.16.5","condition":"matched_1.16.5","feasible":true},
 {"task_id":"pla_004","task_type":"planning","world":"1.16.5","condition":"mismatched_new2old","feasible":true},
 {"task_id":"pla_010","task_type":"planning","world":"1.19","condition":"matched_1.19","feasible":true},
 {"task_id":"pla_012","task_type":"planning","world":"1.21.11","condition":"matched_1.21.11","feasible":true},
 {"task_id":"pla_007","task_type":"planning","world":"1.21.11","condition":"mismatched_old2new","feasible":true},
 {"task_id":"eff_006","task_type":"efficiency","world":"1.16.5","condition":"matched_1.16.5","feasible":false},
 {"task_id":"eff_006","task_type":"efficiency","world":"1.16.5","condition":"mismatched_new2old","feasible":false},
 {"task_id":"eff_006","task_type":"efficiency","world":"1.19","condition":"matched_1.19","feasible":false},
 {"task_id":"fea_006","task_type":"feasibility","world":"1.16.5","condition":"matched_1.16.5","feasible":false},
 {"task_id":"fea_001","task_type":"feasibility","world":"1.16.5","condition":"mismatched_new2old","feasible":false},
 {"task_id":"fea_008","task_type":"feasibility","world":"1.19","condition":"matched_1.19","feasible":false},
 {"task_id":"fea_007","task_type":"feasibility","world":"1.21.11","condition":"matched_1.21.11","feasible":false},
 {"task_id":"fea_007","task_type":"feasibility","world":"1.21.11","condition":"mismatched_old2new","feasible":false}
]
```

## Fixed diagnostic evidence and prompt text

Facts are rendered, in deterministic shuffled order, only as:
`Mechanism fact | item: <item> | mechanism: <drop|recipe|smelting|name> |
result: <public result> | source timestamp: <ISO-8601>.`
The rendered data contain all target-world oracle-path requirements, including
drop mechanisms and necessary alternatives, but never a plan/order, version
name, candidate-version list, hidden state, or evaluator tool. K presents only
these target-world facts at episode start. Search remains public and returns
only lexical matches from this same K fact set. K+C appends every conflicting
other-world fact for the task using its factual timestamp; K+N appends an equal
number and equal token-length neutral, task-unrelated target-world facts with
the timestamp multiset of K+C. Facts are shuffled after each condition is
formed with seed `20261016` plus its instance ordinal.

The system prompt, action schema, task text, 90 semantic-action budget,
40-call budget, temperature 0, and JSON requirement are byte-identical to the
first-run Direct/ReAct prompt. The evidence is placed in the initial public
observation as `diagnostic_mechanism_facts`, not in the system prompt.

## Matrix and scoring

Run ReAct on K, K+C, K+N for all 32 instances and Direct on K for all 32.
S runs only if ReAct K feasible-task SR is below .5. S uses K+C and the oracle
state immediately before its first conflict-related action; if the simulator
cannot accept that public diagnostic initial state, S is not run and the cause
is reported. No ordinary run is altered to support S.

Primary metrics are SR, feasible SR, correct infeasible submission, first
conflict decision agreement with target-world fact (pre-registered action
matcher), stale-specific query/execution contact, v3-d propagation, semantic
actions, and model calls. K+C minus K+N and K minus original first-run use
10,000 paired task-cluster percentile bootstrap draws, seed `20261016`.

Interpretation is fixed: K success with K+C below K+N supports an additional
conflict burden; K near its lower bound with successful S supports long-chain
planning burden; S version errors support knowledge-validity judgment failure;
correct judgement with task failure separates judgement from execution; all
other patterns are reported as ability/attribution limitations. No result can
expand or shrink this matrix. A second model family is prohibited unless the
author separately confirms its endpoint is shareable.
