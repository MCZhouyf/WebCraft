# A19 human semantic-audit closure

Prospective registration, 2026-10-17. Commit this document and the input-hash
manifest before first opening the scorer key for A19. The unsealing record
must contain UTC and America/Tijuana timestamps and this registration commit.
Zero LLM calls, zero new episodes, no remote push, no frozen-method changes.
WebCraft, source annotation tables, and original scorer keys remain read-only.
Do not touch non-task processes or model/game services; no GPU use. The
server.lock protocol is preserved; this offline CSV calculation launches no
server and does not acquire, remove, or change another session's lock.

## Gate and immutable cohort

Before key access, run the existing `scripts/validate_v3b.py` without draft
mode on `/root/autodl-tmp/audit_sheet_v3b.csv` (37 rows) and
`/root/autodl-tmp/audit_sheet_cds_v3.csv` (30 rows). Both must PASS. Also require
the 37 neutral IDs to match the frozen v3b export, no immutable evidence-cell
changes, and all 80 main IDs to match the original unique-episode sample.
If a gate fails, report it and stop without reading the key.

The main analysis is exactly **80 unique complete episodes**, sampled from
119 unique episodes underlying the original 147 event-audit rows. Merge the
37 uploaded revised judgments by audit_id with the other 43 existing v3
judgments (expected: 39 no-contact, four contact). Do not treat unresolved v3
placeholders as no-contact. The revised 37 judgments always supersede their
prior labels, including when the new judgment is unable to determine.
The 30 CDS rows are a separate validation cohort, not an extra 30 main cases;
this task does not open the CDS key or pool the cohorts.

Preserve the frozen sample and its original stratum priority:
propagation > query > execution > nonspecific > no-contact abstention.
The selected counts are 8 / 19 / 17 / 23 / 13. Use the archived sample
representative-alias mapping and v2 ID map, not scorer outcomes, to identify
the same complete episode. Join every original event-key row represented by
any alias of that selected episode, not just its representative's event.
Never merge different methods, worlds, conditions, tasks, or full trajectories.
List structural gaps or conflicting identity mappings; do not impute labels.

## Event-to-episode aggregation (frozen before key access)

Contact: if any event is execution, episode contact is execution; otherwise,
if any event is query, episode contact is query; otherwise it is no contact.
Known human unable-to-determine judgments are a separate fourth category,
never silently mapped to any of those three categories. For any genuinely
unresolved scorer event without an established positive event, preserve an
unable-to-determine episode rather than manufacture a negative.

Propagation: if any event is judged propagation, episode propagation is yes.
If none is yes but an event judgment is unable to determine, the episode is
unable to determine. Otherwise it is no. A human no-contact judgment with a
structurally blank propagation cell entails no propagation; a human
unable-to-determine contact with blank propagation remains unable to determine.
If contact is known but propagation is unable to determine, only propagation
is unresolved. Do not discard a determinate contact judgment in that case.

Human rows already describe complete episodes. Do not infer new human labels
from action logs or replace their literal judgments with scorer judgments.
Normalize only documented equivalent label spellings and boolean encodings;
unknown key schema or ambiguous values must be reported, not guessed.

## Tables and denominators

Export contact confusion matrices with human rows and scorer columns in order
no / query / execution / unable to determine, including zero cells. Export
the propagation matrix in order no / yes / unable to determine, also including
zero cells. Main contact detection collapses query+execution to positive only
for precision/recall; the full four-category matrix remains available.

On human-contact-determinate episodes with a determinate scorer prediction:
TP = both contact-positive; FP = scorer-positive, human-no; FN = scorer-no,
human-positive. Precision = TP/(TP+FP), recall = TP/(TP+FN). Explicitly report
eligible n and excluded unresolved counts. Do not classify query/execution
subtype disagreement as a binary contact false positive or false negative;
list subtype disagreements separately.

Propagation precision uses human-propagation-determinate episodes with a
determinate scorer prediction: TP/(TP+FP). Human no-contact episodes are
negative propagation examples; unresolved contact or propagation is excluded,
not counted as negative. Also report propagation TP/FP/FN/TN and recall as a
supporting diagnostic, never substitute contact precision for propagation
precision. Zero denominators are NA, not 0 or 1.

Unresolved rates use all 80 episodes: contact unresolved / 80, propagation
unresolved / 80, and either unresolved / 80, reported separately. Provide
the same matrices/counts/denominators within each original sampling stratum.
These are unweighted descriptive estimates of the selected audit cohort,
not a population-prevalence or overall-benchmark accuracy estimate. No new
significance test, post-hoc threshold, or population weighting is introduced.
Use episodes because the human reviewed full trajectories, propagation depends
on later behavior, and repeated event samples from the same episode are not
independent judgments.

List every contact FP/FN, propagation FP/FN, contact-subtype disagreement, and
unresolved case with neutral audit ID, provenance aliases, method/condition/
world/task, both judgments and human rationale. These are appendix candidates,
not newly adjudicated gold labels. Preserve scorer event lineage separately.

## Optional independent second annotation

Only if a second independent annotator is confirmed, sample 50 of the 80
neutral IDs without replacement from sorted IDs with Python Random seed
20261019, then shuffle with independent seed 20261020. Freeze the selected IDs
before exporting. Use complete evidence from the enriched v3b row when present
and the frozen v3 row otherwise. Do not include any old human labels, notes,
prev_* fields, scorer labels, original trajectory IDs, or sampling strata.
Keep three blank annotation columns contact_type, propagation, notes.

After return, compute unweighted Cohen's kappa separately for contact
(no/query/execution) and propagation (no/yes); exclude either annotator's
unable-to-determine cases per metric and list their counts. No-contact implies
no propagation. Zero expected-agreement denominator is NA. Do not treat repeat
rounds from the same annotator as independent double annotation. Until an
independent second sheet exists, report kappa NOT_AVAILABLE, not zero.

## Outputs

New files only under `reports/main/post_review/a19_human_audit_20261017/`:
validation, immutable-input checks and hashes, key-unseal record, event lineage,
80-row alignment, confusion matrices, metric numerators/denominators, strata,
disagreement examples, and REPORT.md. Record every key access and verify input
hashes unchanged at completion. Submit new code/tests/reports locally and report
the final commit. Do not alter an original audit_key or previous reported result.
