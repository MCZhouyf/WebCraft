# Paper A Motivation Study: Multi-model Extension

Frozen on 2026-10-11 before any study call. This study makes LLM API calls only and
does not access Minecraft Wiki, Fandom, retrieval indexes, or supplied Web text.

## Models and call design

| Key | Provider | Exact requested model ID |
|---|---|---|
| `glm52` | SenseNova OpenAI-compatible gateway | `glm-5.2` |
| `qwen35` | SiliconFlow | `Qwen/Qwen3.5-35B-A3B` |
| `qwen38` | SiliconFlow | `Qwen/Qwen3.8-27B` |

All calls use temperature 0 and disabled thinking. Each response's server-side `model`
field must equal the exact requested ID. A missing or different ID stops that model;
results from aliases are not pooled. Each assertion is sent in a fresh request with no
conversation history. For every model, each of 51 assertions receives one unversioned
question and three separate version-conditioned questions, for 204 ordinary calls per
model. A fixed temperature-zero integrity canary follows ordinary call 200 and is not
an observation. Transport retries occur only for 429 or transient 5xx responses.

## Assertion set

The set is identical to the first-round unconditional prior: 51 assertions in sorted
conflict-ID order. An assertion hash is SHA-256 of its canonical compact JSON. The set
hash is SHA-256 of sorted canonical `{"id": ID, "assertion": ASSERTION}` lines with LF:
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

## Exact prompts

The system message is:

```text
Answer one factual Minecraft Java Edition question from your internal knowledge only. Do not search or use external documents. Return exactly one JSON object with keys "answer" and "rationale". "answer" must be exactly "true", "false", or "unknown". "rationale" must be one short sentence. Treat this request as an independent question with no prior conversation.
```

The deterministic natural-language statement `S` is generated from the assertion by
`corpus/motivation_multimodel/common.py`. Item IDs are rendered by replacing underscores
with spaces; quantities, alternatives, order, grid cells, Silk Touch status, and name
existence are retained. The exact user templates are:

```text
A: In Minecraft Java Edition, is this statement true? {S}
B: In Minecraft Java Edition {v}, is this statement true? {S}
```

For B, `{v}` is independently substituted with `1.16.5`, `1.19`, and `1.21.11`.
Neither prompt contains truth labels, conflict IDs, task metadata, retrieval text, or
world information beyond B's explicit version.

## Parsing and five-way scoring

Exact JSON answers `true`, `false`, and `unknown` are parsed automatically. Canonical
item IDs and integer quantities in the question are deterministic renderings of the
frozen assertion and may be matched automatically. Malformed, qualified, contradictory,
or otherwise nonstandard answers require item-level manual adjudication; the exported
table retains `adjudication_mode`, `adjudication_basis`, raw rationale, and any manual
basis. Missing or unknown answers are never re-asked.

For A: unknown/unparseable is **avoid_or_unknown**; a manually verified unsolicited,
fully correct distinction among versions is **version_aware**; a Boolean answer that
differs from 1.21.11 but matches an earlier truth is **stale**; an answer matching
1.21.11 is **current**; any remaining answer is **error**.

For B, the three version calls form one assertion-level observation. Any unknown or
unparseable member is **avoid_or_unknown**. All three correct is **version_aware** when
the truth vector varies, or **current** when it is constant. A uniform collapse to the
1.21.11 truth is **current**; a uniform collapse away from 1.21.11 to an earlier truth
is **stale**; every other pattern is **error**. Correct conditionalization means all
three answers match their respective truths. The key reported rates are A's stale
count / 51 and B's correctly conditioned count / 51. The stale-default pattern is
declared replicated for a model only when A's stale rate is greater than 0.5.

Implementation hashes before calls:

- scoring-rule canonical hash: `c0af5749ddd9cb3c5a183d40d3dbbee1b7a6cb9893222afcac34a94748244716`
- `score.py`: `2c535c8a9d0e4cb9bd494ba577a2d2e2d360e575baf7ffc8e4fa565cd7673cd7`
- `run.py`: `01cf1e896ff140013aa16a87a9b67fff13690213ca9c66fe1ce00d353146c837`
- `common.py`: `084af5f7d98129d49853284c18fdfb2b925ca053623283effdf921f80136b349`

## Amendment 1: unavailable preregistered endpoints

Frozen and committed before any replacement-model study call. Both SiliconFlow
credentials returned HTTP 402 (`insufficient balance`) on their first attempted
ordinary request, before a model response or observation was produced. The SenseNova
credential returned HTTP 429 (`token plan entitlement exhausted`) before producing an
observation. No result was silently discarded and all three original output files are
empty. Unlimited study budget cannot override provider-side account entitlement.

The two unavailable Qwen slots are superseded prospectively. The final planned set has
four exact model IDs:

| Key | Model supplier | API gateway | Exact requested model ID |
|---|---|---|---|
| `glm52` | SenseNova | SenseNova | `glm-5.2` |
| `claude_sonnet45` | Anthropic | Anthropic Messages-compatible configured gateway | `claude-sonnet-4-5-20250929` |
| `claude_haiku45` | Anthropic | Anthropic Messages-compatible configured gateway | `claude-haiku-4-5-20251001` |
| `gpt52` | OpenAI | Anthropic Messages-compatible configured gateway | `gpt-5.2` |

This retains the first-round model and includes three model suppliers. The configured
Messages model-list endpoint advertised all three replacement IDs before calls. Exact
server `model` equality, 204 independent calls per model, temperature, prompts, canary,
parsing, and scoring rules are unchanged. The replacement transport implementation is
`corpus/motivation_multimodel/run_anthropic.py`, SHA-256
`310667bc1f9aeb3deefe121f7307fc7b30367a1d4c21afb5c56655dd7f5cad2c`.

If `glm-5.2` remains entitlement-blocked after the other models complete, its new run
is reported as a protocol deviation rather than replaced by an alias; the existing
first-round 51-assertion result remains a clearly labeled historical reference only.

## Amendment 2: SenseNova key-pool transport

Frozen and committed before any `glm-5.2` study observation. The author supplied six
SenseNova credentials after Amendment 1. Connectivity probes used a fixed non-study
JSON request: one credential returned the same entitlement-exhausted 429 and five
returned an exact server model ID of `glm-5.2`. Probe responses are excluded from the
study and all reported call counts and costs.

The five working credentials are held only in the Git-ignored, mode-0600 local file
`env/motivation_sensenova_pool.json`; no key or key fingerprint appears in source,
reports, commits, or study ledgers. The transport rotates to the next key only on an
explicit 402 or a 429 containing quota, entitlement, or balance status. Other 429 and
transient 5xx responses receive bounded exponential backoff. Every successful study
response still requires exact `model == "glm-5.2"`; key rotation changes neither
prompt nor scoring.

The transport implementation is
`corpus/motivation_multimodel/run_sensenova_pool.py`, SHA-256
`1510be9a4d802d6b9714d428fd276cafd3a870bde658e33c2a01378cff85e313`.
