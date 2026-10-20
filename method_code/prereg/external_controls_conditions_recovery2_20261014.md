# External-controls conditions recovery 2

Date: 2026-10-14

The first exact continuation preserved 40 additional episodes. One credential
slot stopped after exhausting the registered retries on HTTP 429; all other
started chunks completed. Combined with the original run, 66 accepted episode
keys are immutable: `direct_llm` offsets 0 through 39 and `react` offsets 0
through 25. The exact remaining 198 keys are frozen in
`prereg/external_controls_conditions_recovery2_jobs_20261014.json`.

This continuation uses a five-second request interval. If a slot exhausts its
two same-request network retries, that credential slot is quarantined and the
other healthy slots continue. No failed in-progress episode is scored. If all
slots are quarantined, the scheduler stops. This transport pacing and slot
isolation do not alter prompts, model parameters, observations, actions,
budgets, task selection, scoring, or interpretation.

A fresh exact-model canary is required before launch. Final assembly combines
the original run and every continuation and must prove exactly 264 unique keys,
with no duplicate or missing job and only server model identifier `glm-5.2`.
