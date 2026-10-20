# External-controls infrastructure recovery

Date: 2026-10-14

This operational recovery was registered after the Sensenova endpoint stopped
all six `oracle_knowledge` workers within the same minute. Each worker exhausted
the two preregistered in-place retries. The final ledgers contain only HTTP 429,
`RemoteDisconnected`, or `URLError`; no model mismatch, evaluator failure,
model-output failure, task-dependent stop, or accepted partial episode occurred.

The 117 accepted episode prefixes are immutable. In-progress episodes at the
transport stop are excluded. A continuation may begin only after a fresh
`glm-5.2` canary and runs the exact unaccepted suffix in a new directory:

| slot | method | original offset/size | accepted prefix | continuation offset/size |
|---:|---|---:|---:|---:|
| 1 | direct_llm | 0/37 | 19 | 19/18 |
| 2 | direct_llm | 37/37 | 25 | 62/12 |
| 3 | direct_llm | 74/36 | 16 | 90/20 |
| 4 | react | 0/37 | 21 | 21/16 |
| 5 | react | 37/37 | 18 | 55/19 |
| 6 | react | 74/36 | 18 | 92/18 |

The continuation uses the same credential slots, endpoint, exact returned
model lock, prompts, public projections, action/call budgets, scoring rules,
and request retry policy. Assembly must prove exact 220-key coverage with no
duplicates before any metric is exported. This recovery does not modify the
registered scope, interpretation, method, or parameters.
