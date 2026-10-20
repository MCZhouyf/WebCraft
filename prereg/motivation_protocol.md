# Paper A Motivation Experiment: Version-Conditioned Parametric Prior

Protocol frozen before any conditioned request on 2026-10-08. The experiment uses
the same 51 assertions as `corpus/llm_prior/`, the configured `glm-5.2` endpoint,
and the persistent WC_LLM ledger. No retrieval text is supplied.

## Assertion set

Each assertion hash is SHA-256 of canonical UTF-8 JSON (`sort_keys=True`, compact
separators, Unicode unescaped). The set hash is SHA-256 of 51 sorted lines, each the
canonical JSON object `{"id": ID, "assertion": ASSERTION}` followed by LF:
`c39d56c21dc620f3dee6059f989630a399a5570d7a152ebf6822d2e718cae6b7`.

| Conflict ID | Assertion SHA-256 |
|---|---|
| `cf_0c226e3fce08af40` | `0c226e3fce08af4031454e49661c329e2f47a3a16353d76532677fe0bd66e557` |
| `cf_0c6e974b768575f0` | `0c6e974b768575f06b53d5b1b6a2b8e8ed780c4c8cfc2fa7b038a14a4eb3ff6d` |
| `cf_0cf08e1cf6803d3f` | `0cf08e1cf6803d3f312a2edc3553720b584a937451399b528d4b2cd50c24e948` |
| `cf_0d210ee00d1c6174` | `0d210ee00d1c61748126feeaf4c8c5ac2035bed211c2e7e4d7dacf5b200106d9` |
| `cf_1cb7281e828f31da` | `1cb7281e828f31da83ce376091614c64faafbeb2bd624dd3e499ba34f7c294fc` |
| `cf_2068561aa0ccad4d` | `2068561aa0ccad4d38a7662b3bc8a272e09eca4b4b113ab976f58058773c7eb8` |
| `cf_238bb55648aeec46` | `238bb55648aeec4634cc34907f79d1337e2bd1e089a40fd7a961a9ffdbfb5d7b` |
| `cf_26b91529da7ae18a` | `26b91529da7ae18a8ef05b7f0ddbdba5b6dd334fb9f83332814c5b0cff2dfba6` |
| `cf_2e815e3f72c3c874` | `2e815e3f72c3c874cc801a42a3ed7ce2382b347ff542efc52463605ae2ca1eae` |
| `cf_3b4af3a40cc2a7de` | `3b4af3a40cc2a7de2fc075658f4cf3d5c3f97a3c0fb138897cbc78e0be559bbc` |
| `cf_41c3bbdf62309528` | `41c3bbdf623095283701ddd1a01bcc0d85a5c5e52dfd9e431c55214a325a9ecb` |
| `cf_47d540b09417920d` | `47d540b09417920d594c0fcc455aad4719da7aad2228d2e67c3a788932b74735` |
| `cf_4d11bfa897f56045` | `4d11bfa897f56045b10404764607e8fe82c3b64b8af3add1fcf50f3b1779939e` |
| `cf_4f998190a145c6b3` | `4f998190a145c6b330c312e13e8501f466d9c2145e33e7e590d687bbb5080f2a` |
| `cf_4fa0985ff8ad9153` | `4fa0985ff8ad9153c00aa08d4c02aa83624a02652f4f7240ca9f977ab5e7d39c` |
| `cf_505b3745b9b39bfb` | `505b3745b9b39bfb2358317f65d45101cc5a2765ab368f73c93d166f45fa50ca` |
| `cf_5184496a41467286` | `5184496a41467286e3b600a781461eb4ef9b48d31860c77d9d3ab052b5ef08a4` |
| `cf_531db9be14f175f6` | `531db9be14f175f64c067861c92066f67b67c4a1287b557c9e47204b6876a9c0` |
| `cf_55273e72b5e069cd` | `55273e72b5e069cd5d79e48999c6dfe55a758c41eff91528b063de6af9ddb333` |
| `cf_56840355bf5ce16c` | `56840355bf5ce16ccb34a53511d9c8b00b0c37ee11c2534618c5203381bad509` |
| `cf_570be14d06a1a91b` | `570be14d06a1a91b0ea275603e3f41e19f962fd9bff41f0e55ab9fde7efe1671` |
| `cf_57ebd937febbf42a` | `57ebd937febbf42a82075678cf195f88cd568927312eb7b6f5080fb1cbf242c6` |
| `cf_5cb0b2918f65d91e` | `5cb0b2918f65d91e61748ac32af8b96e438026c9a3e278b9ec2d98aa0979d7a5` |
| `cf_68bd292b30be0ef8` | `68bd292b30be0ef847eebc2805244cde48af4b36f677613a42fd0fa86b53b4d2` |
| `cf_6907e4f26c9f077c` | `6907e4f26c9f077cf8aea28a09fbe55a6de5047e085b48535d7a6e33f6c4dd57` |
| `cf_6b48d32f07313566` | `6b48d32f073135667746ec3a0e55e40e48cf1d48fc5ef774a3b9b12c8b3d58af` |
| `cf_6cdbb1cfcbc9251d` | `6cdbb1cfcbc9251d2b6eb46b1bb516149d48b9dc4922bfa6d46fcc90b19470d9` |
| `cf_76ff0d1ebadce366` | `76ff0d1ebadce366532c1a34ca9231ef716f76c811c0eef12805226fb733f079` |
| `cf_818aca3c1bd43d7a` | `818aca3c1bd43d7a64cf2a79297224590be9945976cbf25e94e859c33b595c59` |
| `cf_89a9359a02752e94` | `89a9359a02752e947f3fc79af44de8e8320491173c761c4784767479cbdb4d1f` |
| `cf_8c1aaac95575e35f` | `8c1aaac95575e35f198dbd7b416d08f67aed937561f3ef85e9954837e38dbce2` |
| `cf_8c8d36c97705d8dd` | `8c8d36c97705d8ddd3143dec89626c9b93e661237daf7c2a00f89943dab57e04` |
| `cf_947018068b4dc8f6` | `947018068b4dc8f65a4aa84ae51cda2bee3c64043c540d089752f144e0a74536` |
| `cf_959aeccc2986d66f` | `959aeccc2986d66fa0575eb1242029713a0a262ccda5307082c7c68d3d8a9908` |
| `cf_9a5be3fa73db9df0` | `9a5be3fa73db9df0465a5a80bc981f90777ab453d796df77897f23c0bae9381f` |
| `cf_a813b2bd71ea96fd` | `a813b2bd71ea96fdfabb5b789115a23d288c6cac00b1e3920a7b83d58928e06c` |
| `cf_a8a492b8974f4699` | `a8a492b8974f46997cee4c70352fb1a652afbfb39b47b7585cec6fec89a1e782` |
| `cf_ad3306e6306b1b5b` | `ad3306e6306b1b5b101ecda6cd9c6dddd37bb9bdccb6452ff80159202bf85325` |
| `cf_b27645a8f5c5fd88` | `b27645a8f5c5fd885fd09fc8fb2f33a86b32fefdeb4d03a948309434c3667f7c` |
| `cf_b36eaa65c8285db8` | `b36eaa65c8285db891a2a8a19e16136c4911076a4725587f64a75c1f822f71e8` |
| `cf_bbd6e1595e421674` | `bbd6e1595e42167432a2d75db1277a26c9b71f50e68cb41daac43f56c843941e` |
| `cf_c40cc17b8917d6bc` | `c40cc17b8917d6bc76dcc9786c9e13515a658493c1f34d843f03bedbd47979ff` |
| `cf_c8b3e0527e7ee09f` | `c8b3e0527e7ee09fc5a629223667a1b7bcee07531feb6796e121f84e18684e6b` |
| `cf_c8f3718a4637369f` | `c8f3718a4637369f370fb4619fe9dfe91653adcf38701cc5524470ac4db32325` |
| `cf_cb2880dc3e221ba5` | `cb2880dc3e221ba5a0ce612a6e746681da91971c6aa730e76ab1c3012b8f9151` |
| `cf_d339b1c87bc5802f` | `d339b1c87bc5802fb1355b46ef3178936839bdd113616b40731b2da89c1a98aa` |
| `cf_e49fc86b111b3bc5` | `e49fc86b111b3bc5e4f59ec24a52a37f43bf033e6599a28d6e79e5d0523bfe4b` |
| `cf_ebafe65e846e097f` | `ebafe65e846e097f80ab5fb64b515d4076ee6e0afc9143a7a4acdf8558741004` |
| `cf_ef57fc62499868e9` | `ef57fc62499868e9deefb228f94c5140cc1629ea2e30619711844e8f1b095879` |
| `cf_f222b76ffee74fa3` | `f222b76ffee74fa33bdeedac12ecc9ba0fc488d9205a52bff547627ecceb8388` |
| `cf_f35b65b2584972c3` | `f35b65b2584972c35ccc7538e58305393a782e1a1a2dd0412238ef825d511d95` |

## Exact prompts and calls

The system message is byte-for-byte the `SYSTEM` constant in
`corpus/sample_llm_prior.py`:

```text
Answer from your internal Minecraft knowledge without search or supplied
documents. Return JSON only as {"answers":[...]}. Return exactly one result per assertion.
Each result has id copied exactly, answer (true, false, or unknown), confidence (number
from 0 to 1), normalized_claim (a concise structured JSON claim), and rationale (at most
one short sentence). Treat each assertion independently. Do not mention editions,
releases, snapshots, dates, or historical variants; answer what you believe is generally
correct now.
```

For assertion `A` and ID `I`, `BASE` is the exact UTF-8 output of
`json.dumps([{"id": I, "assertion": A}], ensure_ascii=False)`. The three user-message
templates are exactly:

```text
In Minecraft Java Edition 1.16.5, {BASE}
In Minecraft Java Edition 1.19, {BASE}
In Minecraft Java Edition 1.21.11, {BASE}
```

Thus only the displayed prefix differs from the one-assertion unconditional prompt.
Every version is a separate request with no shared conversation state. For each of
51 assertions and three prompt versions, make five requests at temperature 0.7 and
one request at temperature 0: 918 ordinary calls total. No seed is supplied. Each
response must report the configured model exactly; persistent accounting and the
fixed canary after every 200th ordinary call use `corpus.llm_client`.
Calls are ordered by stochastic replicate, sorted assertion ID, then chronological
prompt version, followed by the temperature-0 calls in sorted assertion-ID and
chronological-version order. This interleaves prompt versions throughout the run.

## Parsing

Parse the response as one JSON object, then read `answers`, requiring exactly one
object whose `id` equals the requested ID. Case-fold `answer`; exact `true` and
`false` are parsed values. `unknown`, missing/malformed JSON, wrong ID/cardinality,
or any other value is `unparsed`. Preserve every raw response and parse error locally.
Unparsed responses are excluded from accuracy denominators and included in the
reported unparsed rate. No retry is made for a parse failure; transport retries retain
the existing client policy and do not count as additional planned calls.

## Measures

1. **Conditional accuracy matrix.** Rows are unconditional, prompt-1.16.5,
   prompt-1.19, and prompt-1.21.11; columns are truth in those three worlds. A cell is
   parsed stochastic answers equal to that world's Boolean truth divided by parsed
   stochastic answers. The unconditional row uses the frozen five existing samples;
   conditioned rows use their five temperature-0.7 samples. Every cell also reports
   unparsed rate and a 95% cluster-bootstrap interval.
2. **Version sensitivity.** An assertion's conditioned answer for a prompt version is
   the majority of its five parsed stochastic answers; a 2-2 tie or no parsed answer
   is unparsed. It is sensitive if its three conditioned majorities are parsed and
   not all equal. A sensitive assertion is `correct_sensitive` only when all three
   majorities equal their corresponding truth values; every other parsed sensitive
   assertion is `wrong_sensitive`. Rates use all 51 assertions as denominator, with
   unparsed-majority counts shown separately.
3. **Collapse direction.** A parsed non-sensitive assertion is collapsed. Its common
   answer is compared with the three truth entries. Direction is the `+`-joined set
   of matching versions in chronological order, `all_versions` when all match, and
   `no_version` when none match. Report counts and shares without resolving ties.
4. **Change-year buckets.** `change_version` is the first chronological world whose
   truth differs from 1.16.5; its release year is 2022 for 1.19 and 2025 for 1.21.11.
   Assertions already distinguished at/before 1.16.5 use year 2021. Multiple changes
   retain the first. Buckets are `<=2021`, `2022-2024`, and `2025+`; report sensitivity,
   correct sensitivity, wrong sensitivity, collapse directions, and unparsed count.
5. **Bootstrap.** Resample the 51 assertion IDs with replacement as clusters, retaining
   all repetitions/versions for each sampled ID. Use 10,000 replicates, PRNG seed
   20261008, and percentile 2.5/97.5 bounds. Undefined replicates are omitted; if all
   are undefined, emit empty bounds. Do not bootstrap individual calls.

## Interpretation rules

1. Compare each conditioned diagonal cell only with the same truth column in the
   unconditional row. A larger diagonal is evidence of accessible version conditioning;
   equality or decline is not. Off-diagonal cells are descriptive controls.
2. Report `correct_sensitive` and `wrong_sensitive` separately. Sensitivity alone is
   not evidence of correct temporal knowledge, and wrong sensitivity cannot be pooled
   with correct sensitivity as an improvement.
3. Treat collapse direction and change-year/cutoff trends as descriptive. They may
   identify a dominant version prior but do not establish training-data timing or
   causality; no cutoff-before/after claim is made without an official cutoff source.

## Amendment 1: packed format

Frozen before the amended packed run on 2026-10-08. This section supersedes only the
call unit, prompt payload, and call count above. The assertion set, temperatures,
parser labels, measures, bootstrap, and interpretation rules remain unchanged.

The first attempt used one assertion per request. It was stopped after 86 successful
calls and retained under `corpus/llm_prior/pilot_per_assertion_*` with the label
`pilot_per_assertion`; it is excluded from every primary statistic. The correction
reason is: "match the first-row format and eliminate the per-assertion/packed-format
confound; retain the per-assertion format as an optional robustness check, not run in
this round."

`BASE_51` is byte-for-byte the original unconditional user message: the UTF-8 output
of `json.dumps(prompt_payload(conflicts), ensure_ascii=False)`, with conflicts in the
same sorted-ID order used by `corpus/sample_llm_prior.py`. Its SHA-256 is recorded by
the runner before calls. The system message remains byte-for-byte `SYSTEM`. The exact
user-message transformation is:

```diff
-{BASE_51}
+In Minecraft Java Edition {v}, {BASE_51}
```

Equivalently, at byte offset zero insert exactly
`In Minecraft Java Edition {v}, `; no byte in `BASE_51` changes. Substitute `v` with
`1.16.5`, `1.19`, or `1.21.11`. The three versions are separate requests with no
shared context. For each version make five calls at temperature 0.7 and one call at
temperature 0, for 18 ordinary calls total; no seed is supplied.

Reuse the unconditional parser by mapping returned `answers` objects by exact ID.
Case-fold `answer`; exact `true` and `false` are parsed and every other value is
`unparsed`. A malformed package or an ID missing from a package produces `unparsed`
for the affected assertions and is not re-asked. Raw packages and returned model
fields are retained.

The compact decision content is nominally bounded at 51 x 8 = 408 tokens. The frozen
unconditional system also requires IDs, confidence, normalized claims, and rationales;
its five observed completions used 3,684--4,144 tokens. To preserve byte-identical
prompt format without truncating valid JSON, the amended run uses `max_tokens=4608`,
`thinking=disabled`, and the same JSON response mode. This is the smallest rounded
cap above the observed reference maximum; the discrepancy from the nominal 408-token
decision budget is protocol overhead, not additional sampled assertions.
