# A11 BIA protocol audit

Author-instruction date: 2026-10-16. Register before reading outcome-level
audit samples or calculating results. Existing A8-A10 results remain intact.
All audit stages are offline; WebCraft is read-only. No environment episode
or model request is allowed during stages 0-4.

## Inputs, coverage and reproducibility

Use the 416 frozen CDS records and their saved full request/response prompt
logs, visible CDS materials, evaluator boundary labels, A8 prompt_audit.json,
and A9 exports. No reconstruction may silently stand in for a missing prompt.
Audit sample seed is 20261016 with sorted episode keys and NumPy default_rng.
Stage 0 samples three episodes per condition across methods without replacement;
stage 1 samples five episodes from each of Direct/K, ReAct/K, ReAct/K+S,
ReAct/S, using a separate generator with the same seed.

## 0. Action-prompt knowledge injection gate

For each sampled episode, inspect every saved non-boundary task request.
Compare every expected initial public mechanism fact, including its source
and timestamp, with diagnostic_mechanism_facts in the saved request, and
record total and matched counts and any mismatched content. Expected empty
knowledge has zero required entries. Compare the actual serialized fact
objects structurally, not a Python str(dict) representation. Search returns
are supplementary and do not replace mandatory initial injection. If any
sampled action request lacks an expected fact, STOP immediately and report;
do not proceed to boundary rescoring or a new model request. Persist prompt
hashes, sample IDs and all gate rows. Missing/truncated logs also fail the gate.

## 1. Original-question samples

For the 20 fixed samples export the complete original boundary messages,
raw response, parsed vector, candidate indices and IDs (text SHA256 when no
explicit ID exists), saved/evaluator gold labels and target world. Report
whether the request is independent and whether its messages actually include
each expected injected fact. A material file is not evidence of inclusion.

## 2. Full parser and alignment audit

Reparse all 416 saved boundary responses with the frozen parser. Classify
complete parsing, partial parsing, or all-uncertain/default fallback; retain
explicit uncertainty separately from malformed JSON/schema/missing/duplicate
indices and unrecognized labels. Export raw-label to final-label mappings.
Check wording (valid vs outdated), candidate text hashes and order against
the question, saved parsed predictions and evaluator truth, and compare
evaluator target world to the saved episode world. For K, match candidate
fact objects to the injected mechanism facts and verify all matched candidates
are gold-valid. Inspect nested mechanism-fact strings as JSON where possible
to expose misleading timestamp and context comparisons in previous exports.

## 3. Root causes and interpretation

Individually assess (a) missing boundary knowledge, (b) default/inverted parser,
(c) candidate/truth/world mismatch, (d) semantic misunderstanding, and (e)
model behavior. Evidence for d is descriptive only: stratify valid/invalid/
uncertain responses by target world and condition; intent cannot be established
solely by an output vector. Keep unsupported explanations explicitly unresolved.

## 4. Context-match control

Compute candidate text equality after deterministic JSON decoding of a
mechanism-fact string and canonical JSON serialization; normalize plain text
whitespace only. Do not erase payload, source or timestamp differences or use
gold truth to predict. Compare against the complete initial injected fact
objects plus actual saved public-search returns; separately record whether
the original boundary request actually contained the fact. Report per method
and condition valid/invalid counts, class recalls, balanced accuracy, answered
accuracy and agent valid-rate among matched candidates. This corrected
comparator is an additional A11 column, leaving A9/A10 untouched.

## 5. Conditional corrected questioning

Only if stage 0 passes and at least one protocol defect a/b/c/d is supported,
append and commit a concrete corrected-question protocol before any request.
Supply the complete retained action-prompt context and full public episode
trajectory, including initial injected knowledge; explicitly define current
world as the world just acted in. Exclude evaluator fields and world labels.
Bind candidate IDs to predictions and truth. Invalid/unparseable labels map to
uncertain only. Score balanced accuracy, class recalls and uncertainty, with
the context-match comparator on the same scored items. Estimate input/output
tokens and CNY cost and report before starting.

Use the configured glm-5.2 gateway with at most six concurrent requests;
record every returned model identifier and stop all scheduling on any identifier
different from glm-5.2. Ask each of the 416 fixed episodes once, at most 420
provider requests including canaries; no application retries are authorized.
Record failures as missing responses, never cognitive failures. Credential
values and endpoints must not appear in code, exports or commits. No model
service or non-task process may be modified. Sampling/scoring makes zero calls.

For corrected-question comparisons use paired episode IDs, resampling the
pre-existing family_id clusters 10,000 times with seed 20261017. Recompute
pooled answered-item recalls within each cluster resample; emit NA if a class
is absent. Report eligible item/episode/family counts. Keep old and corrected
agent scores and the additional context comparator in parallel tables.

## Outputs

Write audit, samples, parsing/alignment, context controls and reports to
reports/main/post_review/a11_bia_protocol_audit/. New prompts/responses, if
authorized by the gate, go into a distinct artifacts/post_review/a11 directory.
Record source hashes and append final commit/report references without editing
any frozen method, old result or WebCraft file.
