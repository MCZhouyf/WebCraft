# WebCraft P0 Corpus Freeze

Frozen before formal ARBITER P6 and all main experiments. Raw Wiki text and private
claim annotations remain local and are not publication artifacts.

## P0 Coverage Rule

For each grade-A conflict used by the eight development tasks, the mixed corpus must
contain at least one dated old/new side. Conflicts with both sides are `two_sided`;
those with exactly one side are `one_sided` and retain the audited reason code. P0 also
requires all six indexes to pass leakage and SearchBackend contract tests, and requires
completion or explicit accounting of extraction evaluation and retrieval-free priors.

The frozen result is 15 `two_sided`, 3 `one_sided`, and 0 uncovered conflicts. The
`one_sided` subset is:

- `cf_0d210ee00d1c6174`: old side genuinely silent; wooden spear did not exist.
- `cf_6907e4f26c9f077c`: old side genuinely silent; boats with chests did not exist.
- `cf_c40cc17b8917d6bc`: old side genuinely silent; no released bundle recipe existed.

Formal results will report `all`, `two_sided`, and `one_sided` subsets separately.

## Sources and Timepoints

| Label | Timepoint | Pages | Acquisition |
|---|---|---:|---|
| T1 | 2021-02-13T16:05:32Z | 97 | Fandom WikiTeam3 full-history dump, Internet Archive identifier recorded in `corpus/PROVENANCE.md` |
| T2 | 2022-07-07T09:42:18Z | 104 | Same WikiTeam3 full-history dump |
| T3 | 2026-01-08T12:23:30Z | 115 | Author browser `Special:Export` full-history batches; latest revision at or before cutoff |
| current | export on 2026-10-06 | 115 | Author browser `Special:Export`, current revisions only |
| Fandom | WikiTeam3 dump boundary 2023-08-22 | 105 | Latest revisions in the same Internet Archive dump |

The Minecraft Wiki files were supplied manually; no programmatic request to
`minecraft.wiki` was made. T1/T2/Fandom include `Pickaxe`, recovered locally from the
already downloaded dump after redirect diagnosis.

## Revision Manifests

Revision-list hashes are SHA-256 over sorted UTF-8 lines
`page_title<TAB>revid<TAB>timestamp<LF>`.

| Source | Revision-list SHA-256 |
|---|---|
| T1 | `190158868bd9beb272faa55d9923343c3eb6034aa365cc39813d804591292595` |
| T2 | `d22c0b8864a3eec201908e7459722c63bf8b7dc32ca8836c05562242fc35c3b0` |
| T3 | `8cff9d3efffec547e59daa4b374a5664be2920aedd03d1c8f98c3e42a033e57d` |
| current | `f81c24aa9b04b85dceab73f6e20b1965d0646ad6888477a7461fb376c520c9cb` |
| Fandom | `ab87c9c3d11b5b6837fba383593d34fdd769b8247a6b6c311c109b219ccd1cfc` |

| Manifest | File SHA-256 |
|---|---|
| `corpus/release_times.json` | `e7c6630c95008a3cb2910f547ed5368d93a6f7f8d0b7eea15497aeca6db530d0` |
| `corpus/manifests/fandom_history_snapshots.json` | `294ef366c64541940fccede7a3f4c0a6f302c9f951297bcda70d1d6d9fb7974a` |
| `corpus/manifests/fandom_2023_snapshot.json` | `9c0f4ca445e3ea3ac5bf03ae86d8040126c6fd120ed9d382a19f52689db9d31b` |
| `corpus/manifests/mcwiki_export_snapshots.json` | `414f879ce298c5189ae8f1761f48c383f42acabad427329ba9644de2a84e614a` |

The Minecraft Wiki manifest includes SHA-256 for the current export and all 12 history
batch XML files.

## Index Freeze

Directory hashes are SHA-256 over each recursively sorted relative path, a NUL byte,
and the exact file bytes. All directories are below `corpus/indexes/formal/`.

| Index | Pages | Chunks | Directory SHA-256 |
|---|---:|---:|---|
| `matched_1.16.5` | 97 | 304 | `a9cb7d82ccd7eab1890d1fb6667285dab95ccc85aa02176b622809f18a698f23` |
| `matched_1.19` | 104 | 344 | `52ff604ca13d483ca981629a2fd56c3ca5207c066e784c945afbaf00fb447364` |
| `matched_1.21.11` | 115 | 256 | `a7e41fba61cb9f6e5653a7510185c42ee2a89248238e6ec35f20c94528a92e14` |
| `mismatched_old2new` | 97 | 304 | `485d2a22d486622c143f03610e06ed29b98b51bcaf1a26174bad96662bbcfb7c` |
| `mismatched_new2old` | 115 | 256 | `5460101fd8ca464db76ea7db4e6afbb984809cfd3aa87dff3a492de2e5b066d4` |
| `mixed` | 436 | 1,435 | `fdefc3fe340be1566d1dd82876dd48b05ae66c5753a0a0c6d38227e6e900382d` |

`corpus/search_backend.py` is frozen as SearchBackend version
`sha256:c1545c55b2c8daff869b47f6cc06a9368e7d624eecfd2531608f7c81138e0b09`.
`SearchBackend.search` returns exactly `text`, `source_site`, `page_title`, `revid`,
`timestamp`, and `chunk_id`. The corpus suite passed 53 tests, including 200 random
leakage queries for each formal index.

## Coverage and Claims

`reports/coverage_diagnosis_p0.csv` has SHA-256
`25a4dd5a0fd012876a5e5faa55486c7abc5788a01f97ef2e00ee3bc922be79a5`.
`reports/coverage_p0.csv` has SHA-256
`76ee1ee6f8d7a0b680aa68e494a1d93c2d9388646ea92dd0aabe3b2434f41ba0`.
The latter reports template-only and template-union-LLM counts separately. Private gold
and validated LLM claims are not returned by retrieval interfaces.

## Claims cache v2 (solvability repair)

This append-only cache freeze leaves every P0 index byte-for-byte unchanged. The
evaluator-only audit expands each feasible oracle plan into unique recipe, smelting,
and drop requirements per task/world, then requires a current positive public claim
whose subject and payload match that truth-valid plan step. There are 531 requirement
rows across 92 feasible task/world plans. The target thresholds were 95% matched-cache
mechanism coverage and 90% matched-cache task/world closure.

Deterministic family expansion retains each source claim as the parent and adds only
source-span-preserving children with `parent_claim_id` and
`normalizer_version=6fcabdf4`. The actual display-name repair is `iron_chain` to
`chain`. A source-only, temperature-zero incremental pass re-extracted 95 implicated
chunk texts shared across P0/P1; no version or world was supplied to the extractor.

P0 matched coverage changed from 323/531 (60.83%) and 8/92 closed plans (8.70%) to
362/531 (68.17%) and 15/92 (16.30%). Mixed changed from 424/531 (79.85%) and 32/92
(34.78%) to 432/531 (81.36%) and 39/92 (42.39%). The thresholds remain unmet because
the archived `Oak planks` pages are redirects and the selected pages do not explicitly
state several basic drop/producer mechanisms. In particular, no source-supported
`oak_log -> oak_planks` recipe exists in all three matched corpora. Truth-derived or
synthetic public claims were not added; deterministic permitted repairs are exhausted.

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 480 | `ef674e4fdcbb3271fc966c41d0d480c80dd36755719d1de1e20deba654750191` |
| `matched_1.19` | 602 | `56e4a8a601947c36f1befd676a455a924c4422eec9f53a58bc9c6e8201665a19` |
| `matched_1.21.11` | 288 | `ae281854524e49043d74078524b39206b83c5a2b75909a754e5806b68805dbce` |
| `mismatched_old2new` | 480 | `ef674e4fdcbb3271fc966c41d0d480c80dd36755719d1de1e20deba654750191` |
| `mismatched_new2old` | 288 | `ae281854524e49043d74078524b39206b83c5a2b75909a754e5806b68805dbce` |
| `mixed` | 2,139 | `0fcc501f2f0d2ed7f07ec5f83f462df179048521bb64d5faf5fef462e2a529d9` |

The P0 solvability CSV SHA-256 is
`85280666ee5cc1448ab82755e70502e746e730f5460713cbf83d4c0ed30196a3`.
All 86 corpus tests pass, including complete cache coverage, forbidden-field checks,
exact key-set validation, and sampled source-span containment. At this freeze there
are no agent results using these cache hashes.

## v5 (redirect and source-mechanism repair)

This append-only freeze supersedes the P0 v2 indexes and caches. The 115-item mapping
was audited across T1, T2, Fandom2023, T3, and current. Redirect chains were resolved
to non-redirect pages at the registered cutoff; original redirect records remain in
the manifest but do not enter an index. Resolved revision-list hashes are T1
`821354c74b5f9d041b64a13752a321b02c782ce35f8e24669492bc2e92bc9567`, T2
`39523be6669562f6248b492a60dfba47e017582f8d814a6acb8bb8aff1e7a899`, T3
`758b0e78002a941a58969b525726c5118ddf2886ffe5c09e86407a21f44bbb76`, current
`57e25c34e0201730e9feb83e60b3b5c3103f4c2f2fdcdcb6b61d0f6d1cfaff0c`, and
Fandom2023 `941bcbd5314eb9440590d6076613dbf67a806ed1da81b41dc97d0a9e867c3f8b`.

The public cache deterministically parses intact Crafting/Smelting templates, explicit
Block-family drop/tool fields, and explicit current-tense drop sentences. It expands
source-stated matching families and old display names. Every claim retains a verbatim
source span; no truth-derived text or claim was added. Parser, LLM extractor, and
normalizer versions are `72f8b3e7`, `96b07e80`, and `fc317f99`.

Matched solvability improved from 362/531 mechanisms and 15/92 closed task/world
plans to 527/531 (99.25%) and 88/92 (95.65%). Mixed improved from 432/531 and 39/92
to 531/531 and 92/92. The four remaining matched rows are the same Sand self-drop
requirement in two tasks at T1/T2; both archived pages describe breaking but do not
explicitly state that the dropped item is Sand. They are frozen as `true_silence`.

| Index | Pages | Chunks | Directory SHA-256 |
|---|---:|---:|---|
| `matched_1.16.5` | 77 | 647 | `38d67a5ce186e172ef91469c19609b43f87642f29e4cef90bde1cafb6edc5588` |
| `matched_1.19` | 83 | 744 | `da2b88ec6ac2db876bcab80f2de267bc2351d179712e5aa3f5d27035cd1312ac` |
| `matched_1.21.11` | 112 | 522 | `bb78c3e6840d01c3cc5a9e838678299d7ba364172f37a64018af9529ab2c6ff5` |
| `mismatched_old2new` | 77 | 647 | `d115d327975182e5fe4ae976597fd22df15652d0a3d7bf00ed2c502d08d41948` |
| `mismatched_new2old` | 112 | 522 | `fe63b70ba0698b4c22e92f93e8477f2ef2c9c2efa094ba3457e76466eca178bd` |
| `mixed` | 468 | 3,229 | `c67c31b3bb15ef4af8fd0d06c3b6897860499eca95f8ebf7134b84c1ded5f519` |

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 1,960 | `d3ddd8472cc13844c9bac4be88f4636f9d25e1cabe0ed5fe7a0d3f236c26a3f7` |
| `matched_1.19` | 2,311 | `2b7f98f0563cc10d07c32c7138428751693fae21a7b05aa0c68144c7605ef8ba` |
| `matched_1.21.11` | 2,142 | `c5945c29dd1009f5f7a988c64162e73a87c7226d8af9b4c319e3b4279b5be68e` |
| `mismatched_old2new` | 1,960 | `d3ddd8472cc13844c9bac4be88f4636f9d25e1cabe0ed5fe7a0d3f236c26a3f7` |
| `mismatched_new2old` | 2,142 | `c5945c29dd1009f5f7a988c64162e73a87c7226d8af9b4c319e3b4279b5be68e` |
| `mixed` | 11,320 | `f5f10819947f1ea974f4c63daeda620fdb7ab24336cba5594e9ecf1c3236cfb8` |

`reports/corpus_stats/solvability_formal.csv` has SHA-256
`b3aca43f6d23d9d488a451aa320c18846e3e0ad92d0e190e46b52b12a47448e1`.
All 115 corpus tests pass. No agent result used these v5 indexes or caches before this
freeze.

## v6 structured reading mode

This append-only freeze supersedes v5 for public claims caches and solvability
statistics only. The six formal indexes are byte-for-byte unchanged. Deterministic
`recipe`, `smelting`, and `drop` claims are mapped from the archived gold-template
parse back to the exact raw-template chunk, stripped of `source` and `truth_vector`,
and published under contract v2. Their payload records `extractor=template` and
`deterministic=true`; family-expanded children inherit both markers. LLM claims remain
alongside them and are not semantically deduplicated or merged.

The audit defines template-only by `payload.extractor=template`, LLM-only by extractor
version `96b07e80`, and union as exactly those two sets. Existing
`deterministic_prose` claims remain public but are excluded from all three structured
reading columns. Template/normalizer versions are `9994aec2`/`661c9376`.

| Condition | Template-only | LLM-only | Union |
|---|---:|---:|---:|
| matched mechanisms | 315/531 | 381/531 | 464/531 |
| matched closed plans | 18/92 | 16/92 | 40/92 |
| mixed mechanisms | 315/531 | 460/531 | 483/531 |
| mixed closed plans | 18/92 | 39/92 | 51/92 |

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 1,721 | `752eb71ea99fee4f9f86e8c33be594b9c9c3f957ea119e39a120c2563d6b9d30` |
| `matched_1.19` | 2,067 | `0046d6695eb53798cd6881f2c1886675145134c75611283d585bf12f1965b072` |
| `matched_1.21.11` | 2,120 | `6bd4b6cb0a846edf3558b4e7ecb3ae20ded8c962585cb8f27f1343fbe8a7917f` |
| `mismatched_old2new` | 1,721 | `752eb71ea99fee4f9f86e8c33be594b9c9c3f957ea119e39a120c2563d6b9d30` |
| `mismatched_new2old` | 2,120 | `6bd4b6cb0a846edf3558b4e7ecb3ae20ded8c962585cb8f27f1343fbe8a7917f` |
| `mixed` | 10,777 | `7deea7bb99db00c4f7c5ad1f3a8c933fd525ef9fba732cff188c9426ed35734c` |

`reports/corpus_stats/solvability_formal.csv` has SHA-256
`f7ad81bff1aff137680aee73f09f9c4ff3432a2d1c64d9f8dddf0d506b423daf`.
All 119 corpus tests pass, including public-contract, forbidden-field, source-span,
reading-mode, and 200-query leakage checks. No LLM call was made for v6.
