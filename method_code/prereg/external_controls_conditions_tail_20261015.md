# External-controls conditions exact tail

Date: 2026-10-15

The outer execution session ended while two infrastructure workers were active.
Only fully saved episodes are retained. Across the original run and two exact
continuations, 237 episode rows are present, all 237 keys are unique, and no
uncompleted episode is scored.

The remaining matrix positions are `direct_llm` offsets 59--63 and 123--127,
and `react` offsets 82--87, 106--111, and 127--131. These 27 exact keys are
frozen as one-episode jobs in
`prereg/external_controls_conditions_tail_jobs_20261015.json`. Single-episode
jobs limit any further credential isolation to one unsaved matrix key while
healthy slots continue.

The run retains the five-second transport interval, two same-request network
retries, exact `glm-5.2` model lock, and failed-slot isolation. A fresh canary
is required. Final assembly must prove exactly 264 unique keys and only the
registered model identifier before metrics are exported. This recovery changes
no prompt, method, model parameter, observation, action, budget, task, scorer,
or interpretation.
