# Adjudication validity audit: pre-execution denominator amendment

This amendment is committed before the audit script is first executed and
before any audit output exists. It resolves one denominator ambiguity in
`adjudication_validity_audit_20261012.md`; all other registered rules remain
unchanged.

The G1 phrase "at least one mechanism family outside ore" is part of the
external-five-method antecedent. Therefore its denominator is frozen as the
`external_failed_semantic` cohort only, pooled across the five external
methods and grouped by mechanism family. The headline outside-ore proportion
in sentence 3 of `REPORT.md` uses the same external-failure cohort. It must not
include Verify-All, ARBITER retirements, ADAM's separately duplicated paid
probe scope, or the Probe-then-RAG positive control, because those records
would mechanically change the gate denominator.

`per_mechanism.csv` still exports every registered scope separately, so the
outside-ore proportion for any individual method or audit object remains
recoverable. No audit computation had been run when this amendment was
committed.
