# External-controls conditions recovery

Date: 2026-10-14

The six-worker `conditions` run encountered endpoint HTTP 429 responses. Four
workers exhausted the two preregistered network retries and the dynamic
scheduler stopped selecting new chunks. The 26 accepted episode prefixes are
immutable. No failed in-progress episode is scored, and the observed server
model identifier is only `glm-5.2`.

The four partial chunks resume at these exact suffixes:

| method | original offset/size | accepted prefix | continuation offset/size |
|---|---:|---:|---:|
| react | 0/8 | 1 | 1/7 |
| direct_llm | 8/8 | 4 | 12/4 |
| direct_llm | 16/8 | 3 | 19/5 |
| react | 16/8 | 2 | 18/6 |

The two completed chunks (`direct_llm` 0/8 and `react` 8/8) are not rerun.
The 28 chunks that the scheduler had not started retain their registered fixed
offsets and sizes. The complete 32-chunk, 238-episode continuation is frozen in
`prereg/external_controls_conditions_recovery_jobs_20261014.json`.

Continuation requires a fresh `glm-5.2` canary. It uses the same six credential
slots, endpoint, exact returned-model lock, prompts, public reading interface,
action/call budgets, scorer, and two-retry transport policy. Final assembly
must combine the immutable prefix and continuation, then prove exact 264-key
coverage with no duplicate or missing job before metrics are exported. This
operational recovery changes no method, parameter, prompt, task, or judgment.
