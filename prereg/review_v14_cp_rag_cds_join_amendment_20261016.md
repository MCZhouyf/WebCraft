# CP-RAG CDS evaluation-key amendment

**Recorded:** 2026-10-16 UTC, after the frozen CP-RAG evaluation reached CDS
and raised a `KeyError`, before any CP-RAG evaluation result was exported or
inspected.

The W7a CDS baseline identifies a row by the full frozen material condition
`<cds_instance_id>__<K|K+S|S|empty|K+N>`. The first W7b evaluation adapter
attempted to join those rows using only the short condition (`K`, etc.), which
is not a unique or existing baseline key.

The repair is confined to a new evaluation wrapper. It constructs the same
full condition used by W7a and joins on full condition, target world, task ID,
and method. The frozen CP-RAG implementation, dev8 evidence, policy, query,
thresholds, action order, metrics, and source hash
`1385f8fad52f58c4b0efb566c5bfc316b739b67be2e3bc19381f071dd70109d7`
remain unchanged. Main, EXT-v2, and CDS are rerun from the beginning; no
temporary result from the failed process is retained.

