# External second-model robustness probe, 2026-10-11

This is a post-review robustness probe. It does not change any frozen method,
prompt, parameter, or previously reported number. This file and the frozen
sample manifest must be committed before the first model request.

## Model and decoding

- Supplier/model family: Anthropic Claude, distinct from the first-round GLM
  supplier and family.
- Exact requested model ID: `claude-sonnet-4-5-20250929`.
- API protocol: Anthropic Messages API through `ANTHROPIC_BASE_URL`; the key is
  read only from `ANTHROPIC_API_KEY` and is never persisted.
- Temperature: 0.0; maximum completion tokens: 4096; thinking: disabled.
- The first-round external prompt and action parser are unchanged. Anthropic
  has no OpenAI `response_format=json_object` request field, so the identical
  JSON-only instruction remains in the prompt and the identical strict parser
  validates the response.
- Every successful request records the server-returned model identifier. A
  method stops immediately if two or more distinct identifiers occur within
  that method. No mixed-identifier continuation is allowed.

Prompt provenance:

| Item | SHA-256 | Bytes |
|---|---|---:|
| `public_system_prompt(direct_llm)` | `eb39e819b15453aa8b2d8a811b627a1a0d6bc3e3b93307b87637bba59f94a352` | 873 |
| `public_system_prompt(xenon)` | `e868699655cc85ff45660c2faf00c485520baebae706f6d2c1d7edc37ac5a35a` | 900 |
| `src/arbiter/evaluation/external_agents.py` | `37c96dbb0eeefb749d38153ccf9c5916c6441f6412b264a92bccfe43c6064734` | - |

## Frozen scope and sample

1. Direct LLM runs all 110 instances: 22 conflict-bearing evaluation tasks by
   five single-world cells (`matched` by three worlds and the two mismatch
   directions), seed 20261006.
2. XENON runs 40 gold-feasible instances selected from the 72 feasible
   instances. `scripts/select_external_second_model_probe.py` uses seed
   20261011 and proportional `task_type x corpus_condition` allocation: floors
   first, then largest fractional remainders with lexical ties; within each
   stratum it ranks SHA-256 of seed, stratum, and instance identity.
3. Frozen manifest:
   `prereg/external_second_model_sample_20261011.json`, SHA-256
   `caf5cd3f329d8cb1a0e0eba71c67e706b914e78afb772d97cc57a2e814452207`.
   It contains only the preregistered identity, task type, public goal, and
   public initial inventory needed at run time. It contains no feasibility,
   oracle plan/cost, conflict truth, hidden state, or candidate-version list.

The ARBITER WebCraft pin remains commit
`166d48a317fc3a8de72a5329ed45412826c29ad2`, pin-file SHA-256
`697b81a8d33b27ecef36cffcff082a9ea20eed0548799306a8cd98741199389d`.
At selection time WebCraft HEAD was
`b3b20445a664c0bf561b0fed41f0abbd76baccb3`; all 112 files listed in the
frozen pin matched exactly. The discrepancy is commit-only drift from another
read-only consumer. The probe validates all 112 frozen hashes and reads no
newly added WebCraft file. WebCraft remains read-only.

## Runtime invariants

- Direct and XENON use the unchanged `run_external_episode` loop and prompt.
- Limits are 90 charged semantic actions and 40 model calls per episode.
- Reading uses raw public `search` snippets from the frozen `formal_p1` index;
  no claims cache or compiler is used.
- The runtime receives only the frozen public manifest. It does not open task
  metadata, conflict/truth files, oracle plans, or candidate-version lists.
- No GPU is used. The authorized memory reserve is 1.8 GiB plus the planned
  process allocation. Existing non-task processes are not modified.
- Only network/provider-service errors may retry, at most two retries after the
  first attempt per episode. A model refusal is a scored terminal and is not
  retried. Protocol, model-output, and model-identity failures are not retried.
- One canary is required before each method and after each 200 non-canary
  calls. Canary calls and all experiment calls are included in the call and
  token accounting.

## Scoring and uncertainty

Gold scoring, feasibility, false-abstention denominator, and terminal priority
are exactly `prereg/external_rescore_20261010.md`. No new scoring rule is
introduced. Confidence intervals use 10,000 independent episode bootstrap
draws with seed 20261011. This probe is interpreted at the model-family level;
it is not a method ranking or a cross-model ranking.

- Judgment A: whether Direct feasible-task SR remains at most 10%, against
  Oracle 100%.
- Judgment B: XENON solved count among the 40 feasible instances and its 95%
  bootstrap upper bound.
- Judgment C: whether Direct and XENON HTR_all each remain above the frozen
  pipeline reference 0.155.

The gate is fixed before execution: trigger step 3 if Direct feasible-task SR
is at least 20% **or** XENON solves at least 8 of 40. Otherwise step 3 is not
run. Scope may not be expanded or reduced based on the result.

Outputs go to
`reports/main/post_review/external_second_model_probe/main_table.csv` and
`REPORT.md`, alongside immutable raw artifacts. The table includes the second
model probe, the first-round five external methods, five frozen pipeline
methods, Always-Abstain (34.5% on the 110-instance matrix), and Oracle. Actual
provider cost is reported when exposed; otherwise token counts and a clearly
labelled nominal public-list estimate are reported, never a fabricated bill.

Step 3, if and only if the gate triggers, uses this same preregistration to run
all five external methods on all 110 instances. Its ETA must be reported before
execution.
