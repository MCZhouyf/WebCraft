# WebCraft-Versions review release

This CPU-only package re-executes the ten reported pipeline methods for
11,220 main episodes, 600 EXT-v2 episodes, and 2,600 CDS episodes. It contains
method source, shared reader/compiler/MIP planner, frozen parameter prior,
derived public snippet indexes and claim caches, exact reading-mode evidence,
frozen per-episode actions/results, scoring, tests, and table inputs/builders.
No LLM endpoint or credentials are required. Runtime network calls are blocked.

Run `WC_REVIEW_PASSWORD_FILE=/path/to/reviewer-password ./reproduce_main.sh`.
The script installs the exact wheelhouse lock into a new virtual environment
and writes comparison CSVs and new action logs into `runs/reproduction/`.
The password is supplied separately by the author through the submission
system. It decrypts the restricted polluted-condition index/cache, retrieved
evidence and complete main/CP reference logs containing polluted search text.
Original Wiki pages and plaintext synthetic pages are
absent from the distributed package. Frozen snippets carry source attribution
in `corpus/source_attribution.csv`; derivative caches are not original pages.

Three modes share seven physical corpus-condition indexes. `llm_only` filters
the cache to the registered extractor; `all_public` reads all public claims;
`oracle_reading` supplies the frozen gold-path evidence bundles according to
condition availability. It does not introduce an additional retrieval index.

`method_code/src/arbiter/` contains executable methods; `provenance/ff3bf4a.tar`
preserves the method/protocol lock. Post-freeze Probe-then-RAG and scoring
additions are retained in the packaged snapshot. CP-RAG and diagnostic
intervention scripts are included with exact source hashes under
`provenance/scripts/`; they do not change the ten-method denominator.

`cds_materials/` is agent-visible knowledge; `evaluator/cds/` is separate
scorer-only truth. `data/ext_v2/` and `config/ext_v2/` define extensions,
diagnostic item packages, family assignments and cells. Evaluator metadata
must never be given to an agent.

`assets/frozen/` provides immutable reference episodes. `assets/evidence/`
holds frozen retrieved evidence, and `assets/prior/` the parameter prior.
`reports/paperA/build_paper_tables_v13.py` regenerates Markdown/HTML tables
from bundled CSVs. New replay outputs are separate from frozen references.
Manifest verification covers packaged bytes; restricted plaintext is verified
against `docs/RESTRICTED_FILES.json` after decryption.

Resource status: Python 3.10.8, PYTHONHASHSEED=0, CBC single thread, CPU only,
one worker at nice 19, approximately 2 GB planned memory plus headroom.
External-agent model runs are not replayed by this script; their frozen
scored exports remain available for audit. No remote repository is published.

The offline wheelhouse targets Linux x86-64 and CPython 3.10. The Python
interpreter and OpenSSL executable are host prerequisites rather than bundled
model or server components. Install Python 3.10.8 before invoking the script.

For the separate CP-RAG row, use the same isolated environment with
`python -I repro/run_cp.py --output runs/cp_rag`. This entry uses the frozen
policy body and packaged assets; `--limit 1` is a small executor smoke check.
Version-Scoped and corrected-reading source is retained under
`reports/paperA/review_supplements/`; their reported result CSVs are separate
from the ten-method main replay denominator.
