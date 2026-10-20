# A22 author clarification: public text versus policy inputs

Recorded 2026-10-20 02:33 UTC, before development environment episodes or B
selection. The author explicitly approved: public original text may mention
versions, but PF-RAG may not make version-driven decisions.

This supersedes only the literal zero-version-string gate in 3a2e6e6. Preserve
all original Wiki text and all earlier preflight reports. Continue counting
and locating version-shaped text for provenance, without treating it as a
policy leak merely because it occurs in a permitted public snippet.

The following remain hard failures: a policy parsing version strings, using
a version list or probe-to-version mapping, accessing truth/task/condition
metadata, filtering by a version or time window, or receiving synthesized
version hints outside the permitted public evidence. Existing Naive timestamp
ordering is unchanged. Action parameters must derive from explicit compiled
identifiers, not version text. Evaluator-only world routing stays outside the
policy, and no version is reported by PF-RAG.

The original preflight remains BLOCKED_QA under its original literal gate.
Subsequent preflight uses this explicitly authorized interpretation and writes
to a new output directory. No previous counts or reports are overwritten.
All other preregistered rules, including deadlines and development stability,
are unchanged. This is a prospective author clarification, not a favorable
reinterpretation after seeing method outcomes (none have been collected).
