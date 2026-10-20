# CP-RAG injected-evidence isolation amendment

**Recorded:** 2026-10-16 UTC, after the first dev freeze and while the first
evaluation process still held outputs in a temporary directory. No evaluation,
EXT-v2, or CDS CP-RAG result had been exported or inspected.

Static review found that the frozen dynamic re-search callback selected a
public index whenever a condition name existed. This would allow an
`oracle_reading` injected bundle to fall through to the public all-public
index after candidate exhaustion. The first evaluation process was stopped.

The callback is amended to permit dynamic public-index search only when the
bundle reading mode is exactly `all_public` or `llm_only`. Injected
`oracle_reading` and CDS bundles treat their fixed material as the complete
search space. The policy, query, thresholds, metrics, and all other rules are
unchanged.

Dev8 was rerun twice over the five registered cells. All 40 paired action
sequences and terminals were identical; dynamic re-search was exercised 32
times across both repeats. The superseding implementation SHA-256 is:

`1385f8fad52f58c4b0efb566c5bfc316b739b67be2e3bc19381f071dd70109d7`

Evaluation must abort unless the script matches this superseding hash.
