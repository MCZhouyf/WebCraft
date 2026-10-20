# A21 development closeout after the permitted retry

Rule formation: 2026-10-19 UTC, after both development gates were observed
at 2/8 = 0.25. This is a descriptive, post-result, zero-call export and log
diagnosis. It changes no gate, result, material, agent, or scorer. The failed
second gate prohibits a third development retry and formal control execution.

## Scope And Exports

- Keep the original and shortened eight-task runs separate. Export 16 rows
  with a round/stage field; the denominator is eight within each round.
- Reuse each saved gold terminal and the frozen A21 v3-d scorer. Export SR,
  feasible SR, contact/propagation, action/call budgets, source logs and hashes.
  These are development diagnostics, not formal MDS contrasts or a pooled
  16-instance performance estimate. No new significance tests or CIs.
- Export all six frozen terminal categories, including zero cells, separately
  from whether an episode ever contained a failed action.
- For each failed semantic action, preserve method, parameters and public
  feedback. Count additional failures of the same method and exact parameter
  JSON after a previous failure within that episode. This is descriptive
  repetition, not a new propagation or causal label.
- Export recorded per-episode logical usage and historical replay counts
  separately from actual HTTP ledger usage. Scan all retained A21 *ledger.jsonl
  files; remove only byte-identical canonical JSON success-record duplicates
  for cost accounting, reporting duplicate counts. Canary and task calls are
  separate. Do not count replayed decisions as newly billed requests.
- Monetary values are estimates at the previously registered CNY-per-million
  rates (8 uncached input, 2 cached input, 28 output), not invoices. Unledgered
  diagnostic calls and unsuccessful-request billing are excluded/unknown.
- Record both failed gate files, oracle costs and the fact that all formal
  conditions and contrasts were not run (never encode them as zero SR).

## Interpretation Limits

Audit the unchanged fact serializer and context construction as code, without
changing them. Flattened recipe facts do not encode grid placement or output
quantity; initial-only knowledge injection has the previously disclosed
retention limitation. Budget exhaustion does not establish a unique cause
such as intrinsic model planning inability. The export must state these
limitations and must not infer a formal K+S effect or method ranking.

No model invocation, new simulated episode, WebCraft modification, unrelated
process action, or remote push is authorized by this closeout. Read existing
development logs only, and never read a formal task to explain a dev failure.
