# Stage 2 Corpus Provenance

Raw page material is local-only and is excluded from Git. Public artifacts must
contain only derived structured claims and source attribution.

## Planned Sources

| Source | Intended use | Acquisition mode |
|---|---|---|
| Minecraft Wiki | Current and 2026-01-08 snapshots | Author-supplied Special:Export XML only |
| Minecraft Fandom | 2021-02 and 2022-07 historical snapshots | Internet Archive WikiTeam3 full-history XML mirror |
| Minecraft Fandom | Supplemental current snapshot | Fandom API only if its robots gate later becomes readable |
| MineDojo 2022 | Independent 2022 supplemental snapshot | Public Zenodo dataset, text portion only |

No programmatic Minecraft Wiki access is authorized after the 2026-10-06 source
amendment. No browser impersonation, CDN bypass, or access through a proxy is used.

## Acquisition Events

### Minecraft Wiki policy gate

At 2026-10-06 03:18 UTC, before the source amendment, the prescribed
contact-bearing User-Agent received HTTP 403 from `https://minecraft.wiki/robots.txt`.
A then-present client defect retried the response four times with exponential
backoff and did not preserve the five response bodies. Metadata remains in the
ignored local request log. The client was subsequently corrected to archive every
HTTP response and to stop immediately on non-429 4xx responses. There have been no
programmatic requests to Minecraft Wiki since the author prohibited them.

### Minecraft Fandom policy gate

At 2026-10-06 04:17 UTC, the corrected client made one request to
`https://minecraft.fandom.com/robots.txt` with the prescribed contact-bearing
User-Agent. The response was HTTP 403, 5,590 bytes, and an HTML challenge page with
title `Just a moment...`; SHA-256:
`c8aefe5b4ebd975a935d8a5e70edf8cdf83ffb6b5733109828896bf3d5c7c476`.
The response body and metadata are stored under the ignored local directory
`corpus/raw/fandom/attempts/`. No Fandom API request or connectivity-test request
was made because the robots policy could not be read.

## Human Inputs

The author supplied `mcwiki_current_2026-10-06.xml` through a normal browser
Special:Export workflow. It is 2,932,269 bytes with SHA-256
`8fdd7234fc128674e1b9dce7fb67e5e31d755069182c437bb73904b059db1d83`.
Strict XML parsing found all 115 requested titles, one nonempty current revision per
page, and no title mismatch. The export also contains transcluded template pages.
The author also supplied 12 full-history XML batches. All parse successfully and
cover all 115 requested titles. Their SHA-256 hashes are recorded in
`corpus/manifests/mcwiki_export_snapshots.json`. Two transcluded requested pages
appear in an extra batch (`Furnace` in batch 01 and `Chain` in batch 07); revision
aggregation makes this harmless. No manual oldid wikitext is needed. No
Special:Contact submission is needed or claimed.

## Current Status

The Fandom robots gate remains blocked by a CDN challenge, so its API is unused.

### Public full-history mirror recovery

Source discovery found Fandom's documented S3 dump object at
`wikia_xml_dumps/m/mi/minecraft_pages_full.xml.7z`. A HEAD request reported a
20,651,541-byte object last modified 2025-04-06, but a one-byte GET returned AWS
`InvalidObjectState`: the object is in an archival storage class and cannot be
retrieved until its owner restores it. No access-control bypass was attempted.

Internet Archive item `wiki-minecraft.fandom.com-20230822` provides a WikiTeam3
full-history mirror named `minecraft.fandom.com-20230822-history.xml.zst`. It was
downloaded from the public IA endpoint on 2026-10-06 and verified against IA's
published SHA-1:

- Size: 203,365,727 bytes
- SHA-1: `39c3d3b08382278350ccb75cb734e2f0f1f9353d`
- SHA-256: `cb6d6ca2727de9945fe8a9bbed73553d71fcfb65e81f0f45ec5bf435455f9c5e`
- Acquisition time: 56.367 seconds

The 29.2 GB uncompressed XML was read as a stream and never expanded to disk.
It verified 104 of 115 P1 item-title mappings. Deterministic cutoff selection
produced 96 unique revisions at the 1.16.5 cutoff and 103 at the 1.19 cutoff; all
timestamps are at or before their registered cutoff. The 199 selected wikitext
files remain under ignored local storage in `corpus/raw/fandom_history/`.

The current export verifies the exact candidate titles for all remaining 11
identifiers; their absence from T1/T2 remains a real old-timepoint no-page state.
Of the 115 selected T3 revisions, 24 equal the current revision and 91 were selected
from full history. All are unique and at or before the cutoff; the latest timestamp
is `2026-01-08T07:32:21Z`. The manual-oldid report has zero data rows.

Deterministic T1/T2 claims, LLM extraction evaluation, retrieval-free priors, and
five provisional BM25 indexes have been produced. Six formal indexes now use the
selected Minecraft Wiki T3/current revisions and pass leakage tests. The provisional
2023 proxy remains isolated under `corpus/indexes/provisional/` and is not authorized
for formal experiments.
