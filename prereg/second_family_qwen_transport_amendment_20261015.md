# Qwen second-family transport amendment

Date: 2026-10-15 UTC. Prospective status: committed before any Qwen study
observation. This amendment changes transport only. It changes no prompt,
sample, truth, task, method, budget, scoring rule, or interpretation threshold.

The first preregistered preflight used one SSH direct-stdio channel per HTTP
request. Its fixed canary attempt returned no HTTP response, produced no model
response, and yielded no study observation. Per the hard stop, that run ended
and was committed as `ba265e1`. The author then explicitly supplied the intended
usage again and requested a test. A non-study two-token diagnostic used a
temporary local SSH `-L` forward to the same already-running endpoint. It
returned HTTP 200, model ID `coder390`, exact content `OK`, complete usage
(17 prompt, 2 completion tokens), and `finish_reason=stop` in 0.699 seconds.

The diagnostic establishes that the endpoint is usable through the intended
local-forward transport. It is not one of the 204 study calls and is excluded
from M1/M2 and all reported experimental call totals, while being disclosed as
one pre-experiment diagnostic generation. No remote shell command, jump-tunnel
startup, service operation, GPU computation, task/truth access, or retry was
performed. The local forwarding process was closed after the diagnostic.

The motivation segment is now prospectively released in a new immutable output
directory, `motivation_v2/`, through a single temporary local SSH `-L` process.
That process runs only for the segment and is terminated afterward. The already
running jump-side forwarding and model service are reused without inspection or
mutation. The same memory-only key handling, model identity `coder390`, two
fixed canaries, 204 byte-verified prompts, temperature zero, non-thinking mode,
usage requirement, audit hook, no-retry rule, and hard stops remain in force.

The new lock is `reports/main/post_review/second_family_qwen/execution_lock_v2.json`.
Direct and oracle-knowledge segments remain unreleased until their public input
packages and runners receive separate prospective hashes after M1/M2 export.

## Pre-call implementation correction

The first `motivation_v2` launch stopped before opening any network connection:
the author had repeated the identical connection record in this conversation,
while the memory-only loader required exactly one raw occurrence. No attempt or
status artifact was written and no model call occurred. The loader now deduplicates
byte-identical key-and-connection records and still stops unless exactly one
distinct record remains. This correction changes neither transport nor protocol.
The corrected `transport.py` hash is recorded in `execution_lock_v2.json` and is
committed before the first study call.
