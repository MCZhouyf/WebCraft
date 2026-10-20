# WebCraft P1 Corpus Freeze

This document is committed in two phases. The pollution-condition definition below is
prospective and must be committed before any synthetic page is generated. Index,
claims-cache, coverage, and analysis hashes will be appended only after P1 construction
and validation are complete.

## Pollution Condition Definition

### Eligible conflicts and page families

The eligible set is the 40 distinct grade-A conflict IDs referenced by the 42 frozen
tasks in `data/tasks.jsonl` (36 recipe assertions and 4 block-drop assertions). The 12
separator probes are not added independently because this P1 instruction scopes
pollution to task paths; one separator conflict already overlaps the task set. Exactly
one canonical synthetic page family will be generated for each eligible conflict.

Each page starts from that conflict's real structured assertion and changes its core
recipe, drop, or name fact to a plausible counterfactual. The resulting core claim must
evaluate false against all three frozen truth files (`1.16.5`, `1.19`, and `1.21.11`).
Failed candidates are discarded and regenerated, with at most three generation attempts
per conflict. A conflict that has no verified candidate after three attempts is excluded
and reported; it is not replaced by an unverified page.

Generation uses the gateway model `glm-5.2`. The exact prompt, request seed where the
gateway supports it, response model field, token usage, and cost are retained in private
metadata. The generation budget is 15 CNY and generation stops before another request
that would exceed the remaining budget. Synthetic page text and metadata remain local
and are not publication artifacts.

### Pollution ratio and sampling

The pollution ratio is measured as synthetic page instances divided by the number of
real page-revision instances in the frozen mixed index:

`rho = N_synthetic / N_mixed_real`.

The P0 mixed index contains 436 real page-revision instances, so the nearest integer
target is `round(0.3 * 436) = 131` synthetic instances, giving realized
`rho = 131 / 436 = 0.30046`. This interpretation keeps the complete mixed corpus in the
polluted condition; the synthetic fraction of the final 567-page condition is 23.10%.

To reconcile one generated canonical page per conflict with the page-count ratio, the
40 verified page families are instantiated deterministically: every family receives
three instances, and 11 conflict IDs selected without replacement receive a fourth.
Instance timestamps are sampled with replacement from the empirical mixed-corpus
timestamp distribution. Allocation, timestamp sampling, and final page order use the
fixed seed `20261007`. Instances from one family retain the same core false claim and
page body but have unique private instance IDs and independently sampled timestamps.
The realized ratio and any exclusions are reported explicitly.

### Privacy and evaluation isolation

Private page metadata contains `synthetic=true`, the source conflict ID, generation
attempt, truth-verification result for all three versions, canonical-family ID,
instance ID, sampled timestamp, prompt hash, and response hash. None of these fields,
nor any truth label, may enter a retrieval result or public claims cache.

The polluted BM25 index contains the complete frozen mixed condition plus the verified
synthetic instances. `SearchBackend.search` retains the six-field P0 contract. Claim
extraction uses the same frozen deterministic chunk extractor as every other method;
it receives only chunk text and no condition, synthetic status, version, world, or
truth information. Leakage tests cover both the P1 index and its claims cache.

## Realized Pollution Condition

All 40 eligible conflicts produced a verified canonical page family; none exhausted
the three-attempt limit. The deterministic allocation produced 131 private instances
over 436 real mixed page revisions (`rho=0.3004587156`). There are 29 families with
three instances and 11 with four. All core truth vectors are false in all three frozen
worlds. Generation used `glm-5.2`, seed `20261007`, and 0.561544 CNY. Synthetic page
text, private metadata, prompts, and responses remain local and are not publication
artifacts.

## P1 Coverage Rule and Result

The P1 task-path set contains 40 distinct grade-A conflicts. Coverage combines
deterministic current recipe/smelting gold with the common span-validated LLM extraction
for prose drop claims; those two sources remain separately counted. A conflict is
`two_sided` when its subject and mechanism have both an endpoint-old
(`T1=true,T3=false`) and endpoint-new (`T1=false,T3=true`) statement, `one_sided` when
exactly one exists, and `uncovered` when neither exists.

The frozen result is 19 `two_sided`, 15 `one_sided`, and 6 `uncovered`. The one-sided
conflicts are:

- `cf_0d210ee00d1c6174`, `cf_1cb7281e828f31da`, `cf_5184496a41467286`
- `cf_531db9be14f175f6`, `cf_6907e4f26c9f077c`, `cf_818aca3c1bd43d7a`
- `cf_8c1aaac95575e35f`, `cf_a813b2bd71ea96fd`, `cf_a8a492b8974f4699`
- `cf_bbd6e1595e421674`, `cf_c40cc17b8917d6bc`, `cf_cb2880dc3e221ba5`
- `cf_e49fc86b111b3bc5`, `cf_ebafe65e846e097f`, `cf_f222b76ffee74fa3`

The uncovered conflicts are `cf_0c226e3fce08af40`, `cf_0c6e974b768575f0`,
`cf_6b48d32f07313566`, `cf_76ff0d1ebadce366`, `cf_89a9359a02752e94`, and
`cf_959aeccc2986d66f`. Per-side reason codes and separate template/LLM counts are frozen
in `reports/corpus_stats/coverage_p1.csv`. No T3/current title is actually missing, so
`reports/mcwiki_extra_titles_needed.txt` is empty and no further author export is
required.

Formal analyses will report `all`, `two_sided`, and `one_sided` subsets separately.
Uncovered conflicts remain in `all` and are additionally reported as a diagnostic
subset; they are never silently promoted or supplied with synthetic evidence outside
the polluted condition.

## P1 Index Freeze

Directory hashes use the P0 algorithm: SHA-256 over each recursively sorted relative
path, a NUL byte, and exact file bytes. Indexes are under
`corpus/indexes/formal_p1/`.

| Index | Pages | Chunks | Directory SHA-256 |
|---|---:|---:|---|
| `matched_1.16.5` | 97 | 304 | `a9cb7d82ccd7eab1890d1fb6667285dab95ccc85aa02176b622809f18a698f23` |
| `matched_1.19` | 104 | 344 | `52ff604ca13d483ca981629a2fd56c3ca5207c066e784c945afbaf00fb447364` |
| `matched_1.21.11` | 115 | 256 | `a7e41fba61cb9f6e5653a7510185c42ee2a89248238e6ec35f20c94528a92e14` |
| `mismatched_old2new` | 97 | 304 | `485d2a22d486622c143f03610e06ed29b98b51bcaf1a26174bad96662bbcfb7c` |
| `mismatched_new2old` | 115 | 256 | `5460101fd8ca464db76ea7db4e6afbb984809cfd3aa87dff3a492de2e5b066d4` |
| `mixed` | 436 | 1,435 | `fdefc3fe340be1566d1dd82876dd48b05ae66c5753a0a0c6d38227e6e900382d` |
| `polluted` | 567 | 1,566 | `417bb064bfa6d0ec6f20669f6c65b2046687b9bed4bc430804a03de1f1fcc45d` |

`SearchBackend` remains
`sha256:c1545c55b2c8daff869b47f6cc06a9368e7d624eecfd2531608f7c81138e0b09`
and returns exactly the six P0 fields. All seven indexes passed 200 seeded leakage
queries.

## P1 Claims-Cache Freeze

The common extractor is
`sha256:96b07e8068e82019d22cb13fdb0432569db98ff0bd79bd6e50c096aac4147260`
(`extractor_version=96b07e80`). P1 caches are under
`corpus/claims_cache/formal_p1/`.

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 411 | `291c502d72a4c12f30811e072356282f0a9e77dff7dfa61895d4f59039476036` |
| `matched_1.19` | 524 | `b1f5da0741116e0fd2da6a198ab4744a1fd0b1383a30b79c64d81afbe55ac723` |
| `matched_1.21.11` | 219 | `a159d7edf623993f0c99559ecb5cadbb85b02ac0f2afa5cf9b0e6d6423d174f2` |
| `mismatched_old2new` | 411 | `291c502d72a4c12f30811e072356282f0a9e77dff7dfa61895d4f59039476036` |
| `mismatched_new2old` | 219 | `a159d7edf623993f0c99559ecb5cadbb85b02ac0f2afa5cf9b0e6d6423d174f2` |
| `mixed` | 1,831 | `5435189f17bed489bf34cb584bdbed34a15f6fd2782fe829edf2aade2be8b7d3` |
| `polluted` | 1,992 | `c0ddb8730be5be91a1f07a9e84d874a1e909384d8ce20fcb12b458a1a22fb212` |

Every cache row passed exact-key, forbidden-field, complete chunk coverage, and source
substring tests. The complete corpus suite passes 72 tests.

## P1 Statistics Freeze

| File | SHA-256 |
|---|---|
| `reports/corpus_stats/coverage_p1.csv` | `f9c1ec99ad7e17ab1059ffde738af5d7d028cf42289fa12edffbbe110dcf861b` |
| `reports/corpus_stats/prevalence_by_type.csv` | `ad4a79ea49eebd7d9a2f4164c3ef740507885f94a89ae6a3bc579b6d96b0cefa` |
| `reports/corpus_stats/source_composition.csv` | `633172d907381abaf78409be9a5ba656ee86a3ded00e455971b4eb84392e7ada` |

In mixed, recipe old:new prevalence is 17:144 and drop prevalence is 4:16. Polluted
retains those real ratios and adds 118 recipe plus 13 drop page instances whose core
claims are false in all three versions.

## v2 (post-diagnosis)

This section is append-only relative to v1. At this freeze point, **no agent result has
used `formal_p1`** (`此时不存在任何使用 formal_p1 的智能体结果`). P0 indexes and
caches under `corpus/indexes/formal/` and `corpus/claims_cache/<index>/` were not
modified.

### Diagnosis and bounded repairs

The audit covered the 27 missing sides of the 21 v1 non-two-sided conflicts. Before
repair the reason counts were `(a)=14`, `(b)=3`, `(c)=0`, `(d)=8`, `(e)=0`, and
`(f)=2`. The 27 source-level decisions and excerpts are frozen in
`reports/coverage_diagnosis_p1.csv`.

`cf_f222b76ffee74fa3` is missing the old side. T1 `Iron Ore` revid 1835306 states:
"Unlike most ores, iron ore always drops itself, regardless of whether the player used
Silk Touch." The complete sentence occurs in chunk `6cc244c88b387d82b6d1`; extraction
incorrectly normalized the conditional as `silk_touch=true`. This is `(f)`, not genuine
silence or a chunk boundary failure. The same extraction error affects
`cf_818aca3c1bd43d7a`.

The deterministic parser was extended for only the observed template forms:
`Any Cobblestone or Blackstone`, `Any Wooden Slab` / `Any wood Slab`, and matching
wool/carpet color recipes. Five regression scenarios cover the two generic ingredient
families plus fixed-color, recoloring, and named-slot matching recipes. `Wool` and
`Carpet` were recovered from the already local WikiTeam3 dump for T1, T2, and the 2023
Fandom snapshot. Gold totals changed from `607/682/168/146/769` to
`675/751/168/146/1320` for T1/T2/T3/current/Fandom; T1 and T2 retain zero current
structured truth disagreements.

There were no `(e)` cases, so the chunker was not changed. There were two `(f)` rows,
below the five-row revision threshold, so `extract_claims.py`, its prompt, and
`extractor_version=96b07e80` were not changed. The page-level T1/T2 baseline remains
98.77/80.81 and 97.09/86.21 accuracy/recall; no second prompt revision was made.

The three Minecraft Wiki canonical content pages still absent from T3/current exports
are `Carpet`, `Harness`, and `Wool`; they are listed in
`reports/mcwiki_extra_titles_needed.txt` for author export. The local mirrors supply
the old/Fandom evidence needed for this freeze. After repair, unresolved missing-side
reasons are `(a)=16`, `(b)=0`, `(c)=0`, `(d)=2`, `(e)=0`, and `(f)=2`.

### Coverage v2

Coverage changed from `19 two_sided / 15 one_sided / 6 uncovered` to
`22 two_sided / 16 one_sided / 2 uncovered`. The one-sided IDs are:

- `cf_0c226e3fce08af40`, `cf_0c6e974b768575f0`, `cf_0d210ee00d1c6174`
- `cf_5184496a41467286`, `cf_531db9be14f175f6`, `cf_6907e4f26c9f077c`
- `cf_818aca3c1bd43d7a`, `cf_89a9359a02752e94`, `cf_8c1aaac95575e35f`
- `cf_a813b2bd71ea96fd`, `cf_a8a492b8974f4699`, `cf_bbd6e1595e421674`
- `cf_c40cc17b8917d6bc`, `cf_e49fc86b111b3bc5`, `cf_ebafe65e846e097f`
- `cf_f222b76ffee74fa3`

The uncovered IDs are `cf_6b48d32f07313566` and `cf_959aeccc2986d66f` (white and
blue harness); both have genuine old-side silence and a missing `Harness` canonical
page for the new side. Formal results will simultaneously report the `all`,
`two_sided`, and `one_sided` subsets; uncovered remains a separately identified
diagnostic subset.

### Source and revision hashes v2

Revision-list hashes use the v1 sorted `title<TAB>revid<TAB>timestamp<LF>` algorithm.

| Source | Pages | Revision-list SHA-256 |
|---|---:|---|
| T1 | 99 | `a3236b771988ebea0c78eba3542bfb275a42402c1da8371a76675cd511e069c5` |
| T2 | 106 | `e0a49eaf5b8fe5b0672f02397672a789878ebdf5303a33973cb0f937a68f4baf` |
| T3 | 115 | `8cff9d3efffec547e59daa4b374a5664be2920aedd03d1c8f98c3e42a033e57d` |
| current | 115 | `f81c24aa9b04b85dceab73f6e20b1965d0646ad6888477a7461fb376c520c9cb` |
| Fandom | 107 | `a4c950707bad0f74ff3bb712171d824fe939ee56ccf3ea757c659f747ff4e2d8` |

The updated history-manifest SHA-256 is
`4f7b7205deead9f049576cefbd3ef31fb47f9dd9427b0ce9fcc5b2bfe88ab9e7`; the updated
Fandom-2023 manifest SHA-256 is
`40db543af2b5b6cb1c78707bcf6c645708c728474d1fffdfc98c6c4a65c7a1be`.
The unchanged Minecraft Wiki export-manifest SHA-256 is
`414f879ce298c5189ae8f1761f48c383f42acabad427329ba9644de2a84e614a`.

### Index and cache hashes v2

Directory hashes use the v1 sorted-relative-path, NUL, exact-file-bytes algorithm.

| Index | Pages | Chunks | Directory SHA-256 |
|---|---:|---:|---|
| `matched_1.16.5` | 99 | 318 | `a758aadd06c072635e8b527db8643cb147c5feed24b4c9cd6abfeb5fb79fb66d` |
| `matched_1.19` | 106 | 360 | `f60b27087037903bf39c58e3d0ea5c817f5068dd80aed4629aa25979b97f4d05` |
| `matched_1.21.11` | 115 | 256 | `a7e41fba61cb9f6e5653a7510185c42ee2a89248238e6ec35f20c94528a92e14` |
| `mismatched_old2new` | 99 | 318 | `d6766356d01da63ee5aa9b215367372527e183c2af159baf852da7acd141227b` |
| `mismatched_new2old` | 115 | 256 | `5460101fd8ca464db76ea7db4e6afbb984809cfd3aa87dff3a492de2e5b066d4` |
| `mixed` | 442 | 1,483 | `0df65349b74673311e58a2db341f116cac6e5f1fad52205cb47852f52a77a073` |
| `polluted` | 573 | 1,614 | `813511ed69a425eaf38e578beeef0a1e86d011f34c0d9e997b433567ada52af2` |

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 447 | `f839e2f686e7492e46bd5b7a18c8dd58d0ea6d6f23b47ecbdb7e5b92d2238e83` |
| `matched_1.19` | 567 | `e8a7023466b587fb7fb2a7389518bbfee69cafd3210f3096b57d72aea0f472d3` |
| `matched_1.21.11` | 219 | `a159d7edf623993f0c99559ecb5cadbb85b02ac0f2afa5cf9b0e6d6423d174f2` |
| `mismatched_old2new` | 447 | `f839e2f686e7492e46bd5b7a18c8dd58d0ea6d6f23b47ecbdb7e5b92d2238e83` |
| `mismatched_new2old` | 219 | `a159d7edf623993f0c99559ecb5cadbb85b02ac0f2afa5cf9b0e6d6423d174f2` |
| `mixed` | 1,924 | `569e2dace91f1addc631cd5df3a65296c3cf62b5a072a9d8d374b2aefa47fd51` |
| `polluted` | 2,085 | `3075a2d84a6ed135dfd94ec22bff5eec60480ae44d085eeeee53964bc2fe0ffc` |

The SearchBackend SHA-256 remains
`c1545c55b2c8daff869b47f6cc06a9368e7d624eecfd2531608f7c81138e0b09`.
All seven indexes pass 200 seeded leakage queries and all caches pass exact contract,
forbidden-field, complete-coverage, and sampled span-substring checks. The complete
corpus suite passes 77 tests.

### Statistics and LLM accounting v2

| File | SHA-256 |
|---|---|
| `reports/coverage_diagnosis_p1.csv` | `e77f9112de4d4e1bb7d648dff565b12ecb1c7825487191936139825238f40df7` |
| `reports/corpus_stats/coverage_p1.csv` | `610ad0eedafa9c47bef809fdbae5ec1318d5dfea7c80cd5583ecc5bf7954507d` |
| `reports/corpus_stats/prevalence_by_type.csv` | `0593e87229ab414a11d60c0cb9dab00e991a157ee79414b2f261304b3ce7ec46` |
| `reports/corpus_stats/source_composition.csv` | `05bc5031cd51d15c511d4e85bbdb3f753223b41ee68353cc172ebd1db910aeb3` |

The rebuild introduced 48 unique chunk texts after hash reuse. Five successful
`glm-5.2` calls used 27,021 prompt, 18,768 completion, and 1,024 cached tokens and cost
0.735528 CNY; three HTTP 429 attempts had no response usage and were retried. Total
persistent LLM spend is 16.388644 CNY. Strict revision/payload cache comparison against
the expanded deterministic gold is 79.79% precision and 41.65% recall; generic matching
templates expand to many color/source combinations while the common chunk extractor
retains a single generalized text claim, so this metric is not comparable to the
unchanged page-level T1/T2 baseline above.

## v3 (author-supplied canonical pages)

This append-only v3 freeze supersedes v2 for every future `formal_p1` result. The
author-supplied current and complete-history exports for `Carpet`, `Harness`, and
`Wool` parsed successfully. T3 selected `Carpet@3349290` (2026-01-05T05:10:31Z),
`Harness@3337395` (2025-12-28T01:30:07Z), and `Wool@3332736`
(2025-12-24T06:41:49Z); their current revisions are 3806904, 3785441, and 3802792.
There are no manual-oldid requests and `reports/mcwiki_extra_titles_needed.txt` is now
empty.

The parser added the exact `Matching Harness` / `Matching Wool` Crafting form and
respects `shapeless=1` when a Wiki template supplies ingredients in named grid fields.
This produces the verified harness craft and recoloring claims rather than treating a
shapeless dye recipe as a shaped layout. T3 has zero current recipe/smelting claims
false in frozen 1.21.11 truth. Current has one retained version-mismatch statement:
`Name Tag` revid 3809448 presents a Paper recipe that is false in all three frozen
worlds; it remains a real Web testimony and does not alter truth.

Coverage is now `22 two_sided / 18 one_sided / 0 uncovered`. Both harness conflicts
(`cf_6b48d32f07313566`, `cf_959aeccc2986d66f`) moved from uncovered to one-sided;
their old sides are genuine silence because harnesses did not yet exist. The v2 analysis
commitment remains: formal results report `all`, `two_sided`, and `one_sided` subsets.

### Sources and gold v3

| Source | Pages | Revision-list SHA-256 |
|---|---:|---|
| T1 | 99 | `a3236b771988ebea0c78eba3542bfb275a42402c1da8371a76675cd511e069c5` |
| T2 | 106 | `e0a49eaf5b8fe5b0672f02397672a789878ebdf5303a33973cb0f937a68f4baf` |
| T3 | 118 | `dab0ee41c950e037bb4c9b9dde4ae149c457e4a1133a93fba335a6c81a6a492e` |
| current | 118 | `47e238a0dd8e455c4faa4d6c0332b6ddbfd35eebf41b7c734db37e37e601ba0f` |
| Fandom | 107 | `a4c950707bad0f74ff3bb712171d824fe939ee56ccf3ea757c659f747ff4e2d8` |

The combined Minecraft Wiki export manifest is
`be7f13969afc6a7506b175965fd1a980d79ff84e1cd62a7649bd297f2fb1e4c2`.
Gold claim totals are T1/T2/T3/current/Fandom = `675/751/906/884/1320`.

### Indexes and caches v3

| Index | Pages | Chunks | Directory SHA-256 |
|---|---:|---:|---|
| `matched_1.16.5` | 99 | 318 | `a758aadd06c072635e8b527db8643cb147c5feed24b4c9cd6abfeb5fb79fb66d` |
| `matched_1.19` | 106 | 360 | `f60b27087037903bf39c58e3d0ea5c817f5068dd80aed4629aa25979b97f4d05` |
| `matched_1.21.11` | 118 | 266 | `f36c2771668ad05e0b1ea23ece41bea9a53b4e55e96e796ad93ea6f99a7c9bec` |
| `mismatched_old2new` | 99 | 318 | `d6766356d01da63ee5aa9b215367372527e183c2af159baf852da7acd141227b` |
| `mismatched_new2old` | 118 | 266 | `39375fe09eda924c934f824bb28c4904c34252a16f8da9ca2b20fea7b1b3467e` |
| `mixed` | 448 | 1,503 | `5dca9babd64cf6334fe8eb1367016961ede7a578e050bc7f8d2d62988c6f13d7` |
| `polluted` | 579 | 1,634 | `90f3b4d3c0ad533168b21053990ba6971959a5bda4164f6a7af4f541f8a16c92` |

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 447 | `f839e2f686e7492e46bd5b7a18c8dd58d0ea6d6f23b47ecbdb7e5b92d2238e83` |
| `matched_1.19` | 567 | `e8a7023466b587fb7fb2a7389518bbfee69cafd3210f3096b57d72aea0f472d3` |
| `matched_1.21.11` | 254 | `9338a496897fcd354a8d71ccda45d934846ddf5cde1dc5502a1d4c4cff25db38` |
| `mismatched_old2new` | 447 | `f839e2f686e7492e46bd5b7a18c8dd58d0ea6d6f23b47ecbdb7e5b92d2238e83` |
| `mismatched_new2old` | 254 | `9338a496897fcd354a8d71ccda45d934846ddf5cde1dc5502a1d4c4cff25db38` |
| `mixed` | 1,981 | `c4f7cdd6568c211c346a688c2edceb235849ab647e88d5a60e28ef1e87dc8e2e` |
| `polluted` | 2,142 | `4e02028b5db27431e35a17248f5f0b358ec1ec9b5455a82134599b3e694a8b1a` |

All seven indexes pass the 200-query leakage scan and all caches pass contract,
forbidden-field, complete-chunk, and sampled span-substring tests. The corpus suite
passes 79 tests. SearchBackend remains
`sha256:c1545c55b2c8daff869b47f6cc06a9368e7d624eecfd2531608f7c81138e0b09`.

### Statistics and LLM accounting v3

| File | SHA-256 |
|---|---|
| `reports/corpus_stats/coverage_p1.csv` | `46c5576ef45ef6c72cbd7625a25b58435bf0818571edac7aee0f45ffa5f5edd9` |
| `reports/corpus_stats/prevalence_by_type.csv` | `4b48620dbbd4c66aab39c33684527558c2ef74cf13372be6e8c7e5e484d27a43` |
| `reports/corpus_stats/source_composition.csv` | `5d54b74a00cc8262816be65859398a2788101dc293cb46423d5a47d98a8e6a45` |

Mixed/polluted recipe old:new prevalence is `63:1573`; drop prevalence is unchanged at
`4:16`, and polluted retains the 118 false recipe and 13 false drop synthetic pages.
Eighteen new unique chunks required two successful `glm-5.2` extraction calls using
10,062 prompt, 5,002 completion, and 256 cached tokens at 0.219016 CNY. The persistent
total is 16.607660 CNY, with 134 ordinary calls and no due canary. The extractor remains
`96b07e80`; no prompt revision occurred.

## v4 (solvability repair)

This append-only v4 supersedes v3 for claims caches only; all seven P1 indexes remain
byte-for-byte at their v3 hashes. Solvability is evaluated over 531 unique-in-plan
requirement rows from 92 feasible task/world oracle plans. A hit is a current positive
public claim whose subject and recipe, smelting, or drop payload matches the oracle
step and is therefore true in that world. The preregistered repair targets were at
least 95% matched-cache mechanism coverage and 90% matched-cache task/world closure.

The repair expands source-stated families (`Any`/`Matching` planks, stone-tier blocks,
wool, carpet, boats, slabs, and related registered families) against the three frozen
registries and valid recipe slots. Every derived claim preserves the exact source
span, records its original `claim_id` as `parent_claim_id`, and has
`normalizer_version=6fcabdf4`. The observed subject alias `iron_chain` is normalized to
`chain`. Page-level/chunk-level gaps triggered a temperature-zero incremental pass on
95 unique chunk texts, shared across both cache generations, without version or world
information in the prompt. It returned 299 claims and cost 1.590040 CNY; no extractor
prompt revision or retry occurred.

| Condition | Before mechanisms | After mechanisms | Before closed plans | After closed plans |
|---|---:|---:|---:|---:|
| matched | 329/531 (61.96%) | 370/531 (69.68%) | 8/92 (8.70%) | 15/92 (16.30%) |
| mixed | 432/531 (81.36%) | 440/531 (82.86%) | 32/92 (34.78%) | 39/92 (42.39%) |

The targets remain unmet after permitted deterministic repairs. Most residual misses
are page-level absence: the archived `Oak planks` pages are redirects to an unexported
target section, and selected pages omit several basic producer/drop statements. The
required three-world regression `oak_log -> oak_planks` cannot be source-supported in
the matched caches. Following S2(d), no truth-derived or synthetic public text/claim
was added. This is the recorded deterministic-exhaustion outcome, not a relaxed gate.

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 516 | `0252345e67a5f043488b50d0148a95739d758ad7a3503da3a9cfd6e70edc9435` |
| `matched_1.19` | 651 | `6f6b4874030bb2a072f2da768147183c8d1eb840eb8b31631c8b60c19b6b3651` |
| `matched_1.21.11` | 359 | `66f5114314b15898c54623d4002a43daf16ad17242e5aa4c3043ce60dc19400d` |
| `mismatched_old2new` | 516 | `0252345e67a5f043488b50d0148a95739d758ad7a3503da3a9cfd6e70edc9435` |
| `mismatched_new2old` | 359 | `66f5114314b15898c54623d4002a43daf16ad17242e5aa4c3043ce60dc19400d` |
| `mixed` | 2,384 | `509934a2cc3d4be79209c275affab0f070358b39998580b8eae554a09f0b07e4` |
| `polluted` | 2,551 | `9d36b08b3247ee656de38a07b75c15a19b3ff2cc2a8e09e367aeb3266cbd428e` |

`reports/corpus_stats/solvability_formal_p1.csv` has SHA-256
`04b4307121a0e957a229f50abc45b1c902cf976cc1e24a8ef112f300862f1efc`.
The evaluator audit implementation has SHA-256
`188968e68edb1adbbfc995feef0186ed8c4e789e4da53b536a4515bdfd33b650`.
All 86 corpus tests pass. Persistent Stage 2 LLM spend is 18.197700 CNY. At this
freeze there are no agent results using the v4 cache hashes.

## v5 (redirect and source-mechanism repair)

This append-only v5 supersedes v4. At freeze time no intelligent-agent result has used
the v5 `formal_p1` indexes or caches. All redirect targets are locally archived and
resolved at the five registered source cutoffs; the manifest and page-map SHA-256
values are `1b3b9308b3ae2d294fb6b8947fe657ac79927c1cb924aaff12eb7f332d3ced05`
and `7af8e6e4d6f2e1e45987e056bb0ed0daf98a03afff9c137141c0879433d83933`.

The repair is source-only and deterministic: intact Crafting/Smelting templates,
explicit infobox fields, explicit current-tense drop sentences, matching item-family
expansion, old display-name aliases, and translation-invariant shaped-layout matching.
No synthetic or truth-derived claim was inserted into matched or mixed conditions.
Parser/extractor/normalizer versions are `72f8b3e7`/`96b07e80`/`fc317f99`.

| Condition | v4 mechanisms | v5 mechanisms | v4 closed | v5 closed |
|---|---:|---:|---:|---:|
| matched | 370/531 | 527/531 (99.25%) | 15/92 | 88/92 (95.65%) |
| mixed | 440/531 | 531/531 (100%) | 39/92 | 92/92 (100%) |

The four matched misses are duplicate task uses of Sand self-drop in T1/T2 and are
classified `true_silence`; page title, revid, and source excerpt are frozen in the
solvability CSV. This exceeds the preregistered 95% mechanism and 90% closure gates.

| Index | Pages | Chunks | Directory SHA-256 |
|---|---:|---:|---|
| `matched_1.16.5` | 77 | 647 | `38d67a5ce186e172ef91469c19609b43f87642f29e4cef90bde1cafb6edc5588` |
| `matched_1.19` | 83 | 744 | `da2b88ec6ac2db876bcab80f2de267bc2351d179712e5aa3f5d27035cd1312ac` |
| `matched_1.21.11` | 112 | 522 | `bb78c3e6840d01c3cc5a9e838678299d7ba364172f37a64018af9529ab2c6ff5` |
| `mismatched_old2new` | 77 | 647 | `d115d327975182e5fe4ae976597fd22df15652d0a3d7bf00ed2c502d08d41948` |
| `mismatched_new2old` | 112 | 522 | `fe63b70ba0698b4c22e92f93e8477f2ef2c9c2efa094ba3457e76466eca178bd` |
| `mixed` | 468 | 3,229 | `c67c31b3bb15ef4af8fd0d06c3b6897860499eca95f8ebf7134b84c1ded5f519` |
| `polluted` | 599 | 3,360 | `daf9371139bc94f3eb61fdd42714497b800307a2103227ef5956ebbac033311a` |

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 1,960 | `d3ddd8472cc13844c9bac4be88f4636f9d25e1cabe0ed5fe7a0d3f236c26a3f7` |
| `matched_1.19` | 2,317 | `033422dd4ebc49ba8d3aae91ecadfbc19ad402d14a4e6382968f7da14657c49b` |
| `matched_1.21.11` | 2,178 | `6d301d3782262a885c57cc553dfff7df821d3ee6f44f0348701141c19cf221e5` |
| `mismatched_old2new` | 1,960 | `d3ddd8472cc13844c9bac4be88f4636f9d25e1cabe0ed5fe7a0d3f236c26a3f7` |
| `mismatched_new2old` | 2,178 | `6d301d3782262a885c57cc553dfff7df821d3ee6f44f0348701141c19cf221e5` |
| `mixed` | 11,415 | `a75b23e62e5eb9bf8eddaaca593c3295708d96b03e396a099a0ea2dabecbfcdb` |
| `polluted` | 11,606 | `33ad5440f71e0fc9fce2c1a60169c0de301755932ff2bcfd006a3832e8a10747` |

`solvability_formal_p1.csv` SHA-256 is
`f8493544ae69bb6378daa42d8d6a2ef81aef6b9b850edea80cffd5b363c9e2ef`.
The R1-R3 incremental pass made 52 successful `glm-5.2` calls (352,184 prompt,
273,875 completion, 72,576 cached tokens), costing 10.050516 CNY. Nineteen failed
gateway attempts had no response usage. Persistent Stage 2 spend is 28.248216 CNY.
All 115 corpus tests pass, including cache contract, exact-key, source-span, and
200-query leakage checks.

## contract v2

The public claims-cache contract is `v2`: the original nine fields plus
`normalizer_version` and `parent_claim_id`, for exactly eleven returned fields.
`parent_claim_id` identifies the source claim from which a normalized or family-expanded
claim was derived, and `normalizer_version` identifies the deterministic normalizer that
performed that derivation. Both values are `null` for an original, underived claim.

These are provenance identifiers only. They contain no truth vector, synthetic or
provisional marker, corpus condition, index directory, or filesystem path. Frozen cache
files are unchanged: `ClaimsCache` upgrades legacy nine-field source rows in memory and
returns the same eleven-field contract for every claim.

## v6 structured reading mode

This append-only freeze supersedes v5 for public claims caches and solvability
statistics only. All seven `formal_p1` indexes retain their v5 bytes and hashes.
Deterministic `recipe`, `smelting`, and `drop` claims are mapped from `claims_gold`
back to the exact archived raw-template chunk. Public rows omit source metadata and
all truth annotations, follow contract v2, preserve the full template as `span_text`,
and record `payload.extractor=template` plus `payload.deterministic=true`. Normalized
children inherit those markers. LLM and template claims coexist without semantic
deduplication or merging.

The three audit modes are frozen as follows: template-only selects the template
payload marker; LLM-only selects extractor version `96b07e80`; union selects either.
Existing `deterministic_prose` claims remain in the caches but are not part of these
three structured modes. Template/normalizer versions are `9994aec2`/`661c9376`.

| Condition | Template-only | LLM-only | Union |
|---|---:|---:|---:|
| matched mechanisms | 315/531 | 383/531 | 464/531 |
| matched closed plans | 18/92 | 16/92 | 40/92 |
| mixed mechanisms | 315/531 | 460/531 | 483/531 |
| mixed closed plans | 18/92 | 39/92 | 51/92 |

| Cache | Claims | Directory SHA-256 |
|---|---:|---|
| `matched_1.16.5` | 1,721 | `752eb71ea99fee4f9f86e8c33be594b9c9c3f957ea119e39a120c2563d6b9d30` |
| `matched_1.19` | 2,073 | `59453d50caecc2727193b650b1ad8e7892fed8a457f8ee2ddff00b309438ac98` |
| `matched_1.21.11` | 2,156 | `dfafefd26d9bf46abdb94264501f44882a3cde933ac0d997b184ab4c0d957f03` |
| `mismatched_old2new` | 1,721 | `752eb71ea99fee4f9f86e8c33be594b9c9c3f957ea119e39a120c2563d6b9d30` |
| `mismatched_new2old` | 2,156 | `dfafefd26d9bf46abdb94264501f44882a3cde933ac0d997b184ab4c0d957f03` |
| `mixed` | 10,872 | `fceccf25b7e46fdbe78fa2cbc89c272a083a9d9eb65bbb8bd39beaf2a9454cb7` |
| `polluted` | 11,063 | `8bf133096da7bee665735a7cf023cbc5fd4658bfd4e22441a73ddccb905be67f` |

`reports/corpus_stats/solvability_formal_p1.csv` has SHA-256
`f8231012e4b2196a820a0545a7151ffbb0e702ef66f18e28adca4ed012fb8012`.
All 119 corpus tests pass, including public-contract, forbidden-field, source-span,
reading-mode, and 200-query leakage checks. No LLM call was made for v6.

## v6.2 reading modes

This append-only clarification changes no cache or index bytes. The two official
experimental reading modes are:

- `all_public`: every public claim in the selected cache, with no extractor filter.
- `llm_only`: claims whose `extractor_version` equals the frozen LLM extractor
  version `96b07e80`.

The `template union LLM` column in v6 was a diagnostic audit scope only. It is not an
experimental reading mode and must not replace `all_public`; in particular, it omits
the public `deterministic_prose` route. The V3-compatible all-public implementation
reproduces the v3 P1 result of 527/531 matched mechanisms and 88/92 closed plans on
both the v3 and v6 caches.

| Generation/condition | All-public mechanisms | All-public closed | LLM-only mechanisms | LLM-only closed |
|---|---:|---:|---:|---:|
| P0 matched | 527/531 | 88/92 | 381/531 | 16/92 |
| P0 mixed | 531/531 | 92/92 | 460/531 | 39/92 |
| P1 matched | 527/531 | 88/92 | 383/531 | 16/92 |
| P1 mixed | 531/531 | 92/92 | 460/531 | 39/92 |

Effective extractor counting gives formal totals of 12,042 template, 557
deterministic-prose, and 7,927 LLM claims; formal_p1 totals are 18,588, 869, and
12,305 respectively. `cache_extractor_counts.csv` has SHA-256
`91677ceeebf3a548225dac3521364568b9bfd157e525ea35e50c872c74ec8a2c`.

The v6.2 P0/P1 solvability CSV SHA-256 values are
`f36ddf05ee4874ae13b52d36ca211a01ec861e16cbc6f1fbeda5cc6df503bcac` and
`abfc1c76dbd3524b873b206c17e1df5b7a5316c145483d1892bc481b8538e5d5`.
The audit and extractor-count implementations have SHA-256
`c5f08082aa0fca9ccb2d545fffd7d0920e662b811e9236784d69588e39895f81`
and `25f44a27f149f86c2a3a675e7afaf0736e16eb55f334212283af8a504757d6b8`.
All 122 corpus tests pass. No LLM call was made for v6.2.
