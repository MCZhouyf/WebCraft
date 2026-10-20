# Expansion Feasibility Probe Preregistration

Registered at 2026-10-10T13:12:39Z, before running the expansion planner or
inspecting any evaluation-method result. This probe is deterministic, does not call an
LLM, does not select tasks, and does not modify frozen truth, task, conflict, corpus,
configuration, or simulator files. It may read the frozen task definitions to identify
the Core42 targets and already covered mechanisms, but it must not read method-result or
evaluation-result artifacts.

## Inputs and target universe

The target universe is the sorted union of output identifiers accepted by the existing
`OraclePlanner` in Minecraft Java 1.16.5, 1.19, and 1.21.11. It contains 934 distinct
identifiers (604, 713, and 933 per version before union). Planning uses the frozen arena
resource capacities, public initial item bundle, executable recipe policy, source yields,
action costs, and action limit already implemented by `oracle/planner.py`. Every target
is planned independently in all three versions. An infeasible plan has cost infinity.

The following SHA-256 values freeze the inputs and implementation used by this probe:

| Input | SHA-256 |
|---|---|
| `config/resource_model.json` | `c8b533f8922d42a38194922f68b164e453a627407840ff5351cf8edd1092b312` |
| `config/cost_model.json` | `10b481041b5489a11eb580a968ff8d15fb62aeb25179cbe43ee02ce910a4e80b` |
| `config/recipe_type_policy.json` | `b36749ef2a4a63ad40ddd9e17ff6e02cad027dd388a850c2cb46de0ff59cf732` |
| `data/conflicts.jsonl` | `e655dc89e7a51c9c2973649b0c4cd605f56efabf989499e90bfece7411db7f44` |
| `data/tasks.jsonl` | `1b9309be014062a5e91162a382f09893fe252ce8a081f51be4b9d706bc54fd35` |
| `reports/a6_mechanism_frequency.csv` | `e5096807ac19caac4e8ef18853c720a3167eb6280c32ee08a63a4614d0eeec71` |
| `corpus/manifests/page_map_p1.json` | `7af8e6e4d6f2e1e45987e056bb0ed0daf98a03afff9c137141c0879433d83933` |
| `truth/1.16.5.json` | `023b5883470d4b80b675fc358c8687acd7ac93f1f42e0e45612123e07b012701` |
| `truth/1.19.json` | `6b2dc367995558db82d88238755a0ad8ff22499f9624e0e144ca76c5f16db645` |
| `truth/1.21.11.json` | `dbbc3fec3d60eefd75911e34b85430d3a7efa3b9ee51243399c55382bddd91d2` |
| `oracle/planner.py` | `673eb6595a1e06b93c1208871004c43850124a1c290b36fdca583de29c9d2268` |
| `scripts/build_stage1_data.py` | `2f61744a48b341b990205bc86d02c93eb158dcca4d124661ec50c3756f133d47` |
| `corpus/normalize_claims_cache.py` | `04adaa1adf0d1ee9a722a99150f6e0e664c35eff792d84c0f7e8023ddc96b29f` |

Repository state at registration: `8e9ca4249c86c4e967aa510eceb5024606869e0c`.

### Pre-acceptance execution-environment note

Added 2026-10-10 after the first implementation dry run and before accepting or
reporting probe results. Two planner batches inside one Python process were identical,
but a second process selected a different equal-cost plan because the frozen planner
iterates internal sets whose order follows Python's randomized hash seed. No count or
candidate conclusion is accepted from those dry runs. The frozen planner is not changed;
all accepted runs set `PYTHONHASHSEED=0`, and the complete batch must reproduce across
two independent processes as well as twice within each process. This note is post hoc to
the determinism dry run and pre-result for the accepted probe.

## S1-S2: three-version reachability

For every target `g`, run `OraclePlanner(version, capacities=resource_block_counts).plan(g)`
and validate each feasible result with `validate_plan`. Record
`F(g)=(F_1.16.5,F_1.19,F_1.21.11)` and the corresponding minimum primary semantic-action
cost vector `C*(g)`, using the literal string `inf` for infeasible components. Preserve
the full action sequence, recipe IDs, resource demand, and every path item needed for
later audits. Run the complete batch twice and require byte-identical canonical output.

## S3: automatic conflict typing

Map every craft, smelt, and mine action to `data/conflicts.jsonl` with the same
normalization functions used for Core42. A mechanism key ignores assertion polarity and
recipe encoding variants: recipe mechanisms are `recipe:<output>` and drop mechanisms
are `drop:<block>`. Thus the two temporal sides of one change form one mechanism. The
path mechanism set is the union across feasible versions.

Each target receives four Boolean labels and one exclusive primary type:

1. `feasibility_change`: at least one feasibility-vector component differs.
2. `route_change`: at least two versions are feasible and their exact conflict-assertion
   ID sets differ. Exact IDs are required here so that old and new recipe assertions for
   the same output remain distinguishable; the folded mechanism keys are used only for
   coverage, deduplication, families, and clusters.
3. `efficiency_change`: at least two versions are feasible and the maximum finite cost
   divided by the minimum positive finite cost is at least 1.5. A zero-cost target is not
   an efficiency candidate unless another finite cost is positive, in which case the
   ratio is recorded as infinity.
4. `no_conflict`: none of the preceding labels is true.

The exclusive primary type uses the fixed priority feasibility, route, efficiency, then
no conflict. Overlapping labels remain in the detailed output.

For each feasible path, the dominant mechanisms are the conflict mechanisms on the last
(closest-to-goal) action containing any conflict; the candidate dominant set is their
union across versions. If no action is conflict-bearing, the dominant set is empty.
Mechanism families use the frozen normalizer suffix table
`_chest_boat, _planks, _log, _wood, _stem, _hyphae, _slab, _boat, _wool, _carpet,
_harness`; a mechanism subject ending in one of these receives that suffix family, and
all other subjects retain their exact identifier. Family keys retain the action kind,
for example `recipe:wool` or `drop:iron_ore`.

## S4: deduplication, clusters, and source cohorts

The Core42 target reference is the 42 frozen targets in `data/tasks.jsonl`. The
instruction's 22-mechanism reference is frozen by the 22 rows of the pre-experiment A6
construction diagnostic `reports/a6_mechanism_frequency.csv`; its SHA-256 is registered
above. A wider union over every auxiliary conflict ID attached to Core42 would contain
30 folded mechanisms, and the closest-to-goal path rule would contain 26, so neither is
silently substituted for the stated 22-mechanism baseline. Apply these filters in order:

1. 934 union recipe outputs.
2. Reachable in at least one version.
3. At least one of feasibility, route, or efficiency change.
4. Remove targets already used by Core42.
5. Remove candidates whose dominant mechanism set intersects the Core42 mechanism set.
6. Retain candidates whose complete oracle paths are corpus-closed under S5.

Report survivor counts at every layer. New mechanism assertions are normalized mechanism
keys outside the Core42 set. New mechanism families are family keys outside the Core42
family set. A target cluster is its sorted dominant-mechanism tuple; targets with no
dominant mechanism use their sorted full path-mechanism tuple. Cluster identity does not
include shared base materials.

Audit two descriptive source cohorts through all six layers without changing the main
funnel: (i) newly craftable outputs are outputs accepted in 1.21.11 but absent from the
1.16.5 accepted-output set; (ii) new-route outputs occur in both endpoint output sets and
have at least one normalized 1.21.11 recipe assertion absent from 1.16.5. Report the exact
source count and each layer's survivor count for both, thereby resolving the informal
"about 300" and "about 40" statements.

## S5: corpus closure

For every layer-5 survivor, collect the target plus every item identifier appearing in
its feasible oracle paths: craft inputs and output, smelt input/fuel/output, and mine
resource block/tool/output. An identifier is covered only if `page_map_p1.json` contains
its `item_id`, `verified_in_dump` is true, and at least one v6.2 source has a nonempty
resolved title. A candidate is corpus-closed only when every collected identifier is
covered. Emit separate closed and missing-page lists, with missing identifiers shown per
candidate.

## Independent representation-drift labels

Label the 34 frozen evaluation tasks without reading results. The mutually exclusive
priority is: `direct_identifier` when the target or an oracle action uses an identifier
that is absent or renamed in another frozen world; `mechanism_dependent_rename` when the
task target remains stable but a dependency is covered by a rename conflict;
`long_range_effect` when the nearest conflict-bearing action is not the terminal goal
action; otherwise `none`. Record the oracle-path and conflict IDs supporting each label.

## Outputs

All outputs go under `reports/expansion_probe/`, except
`reports/paperA/task_drift_labels.csv`. Required artifacts are the full 934-target table,
the six-layer funnel, type counts, mechanism and family inventories, cluster inventory,
source-cohort audit, corpus-closed list, missing-page list, deterministic-run hashes, and
a concise Markdown report with `N_task`, `N_mech`, and `N_cluster`. No task is selected and
no method is run.

## Mechanism authenticity audit

Registered at 2026-10-10T14:16:47Z, before executing the authenticity audit. This audit
does not alter the expansion funnel, frozen inputs, or task files, and does not run a
model or inspect method results. Its primary scope is every row of
`reports/expansion_probe/mechanism_families.csv` with `new_after_dedup=1` (32 mechanism
families, each containing one folded mechanism in this freeze). `drop:gold_ore` is added
only as a required diagnostic control and is excluded from the new-mechanism totals.

Input hashes specific to this audit are:

| Input | SHA-256 |
|---|---|
| `reports/expansion_probe/mechanism_families.csv` | `0eb04f143911e8330c0af8e6ed716499a00b8f75ed07fc44c96572b186b40dc1` |
| `reports/expansion_probe/corpus_closed_candidates.csv` | `4545438b0a73614c6c80d6c9104037814797b4dd949cd5115dcaa9b9aeddb0e4` |
| `reports/expansion_probe/targets_934.csv` | `6ae2cef5f5a751adacca9458d321927a7961fb4cb91491550f3c82c6ff7e6e88` |
| `reports/expansion_probe/plans_934.jsonl` | `f7bbcad4c04bfbd9d4cfc82aa8250585ff99846343a027970171b65321571671` |
| `sim/semantic_agent.py` | `f9c870e59d52dd5e7af190d476375699afcca41499b4a80f9687ea631588f3cc` |
| `harness/config/arena.json` | `c53cd4325cc4b6f5d4e352303ff266564e39168645d0a58af27c9001dc38eb87` |

### Decision rule

A mechanism is `behaviorally_real` if and only if at least one semantic action sequence
is executable from the public initial bundle and frozen arena in at least one evaluated
version, and replaying that identical sequence in another evaluated version changes an
observable result: produced item identifier, produced quantity, feasibility, or the
frozen public error category. Otherwise it is `representational`.

For a recipe mechanism, enumerate every concrete grid or furnace terminal action induced
by the mechanism's executable frozen recipes after restricting each ingredient slot to
items reachable under the frozen arena in at least one version. For each concrete action,
use the existing oracle to compute the shortest acquisition plan in every version where
its concrete requirements are feasible, append the terminal action once, and replay that
exact sequence in all three semantic simulators. For a drop mechanism, acquire the
registered tool, append one mine action for the registered resource block, and replay in
the same way. Stop a replay at its first public failure. Compare the ordered observable
trace, represented only by success/failure, public error category, and produced item/count
pairs. Coordinates, private truth fields, recipe IDs, and internal state are not evidence.

Mechanism attribution is terminal-local. For recipe mechanism `recipe:x`, prerequisite
reachability is proved separately in each world by the oracle, and the identical concrete
terminal `craft_grid` or `smelt` action is compared across worlds. If both worlds can
acquire its concrete prerequisites and the terminal public result is identical, divergence
inside one particular acquisition prefix is attributed to that upstream mechanism, not
to `recipe:x`. If the concrete prerequisites are reachable in one world but not another,
that feasibility difference is admissible evidence for `recipe:x`. This guard prevents an
unrelated ore-drop change from making every downstream recipe appear behaviorally changed.
The recorded witness contains each world's shortest prerequisite proof plus the shared
terminal action; the source-world concatenation supplies the requested shortest executable
action sequence.

Among distinguishing witnesses choose the minimum primary semantic-action cost, then the
fewest actions, source version, and canonical action JSON lexicographically. Record the
full shortest action sequence and every version's observable trace. A mechanism with no
witness after exhaustive concrete-action enumeration is representational and receives
one deterministic source code, in priority order:

1. `tag_members_outside_arena`: changed ingredient/tag members are unreachable under all
   three frozen arena planners;
2. `equivalent_alternative_ingredient`: reachable concrete ingredient alternatives differ
   but every replay has the same observable result;
3. `recipe_json_format_change`: normalized recipe assertions differ while the arena-
   reachable concrete action set and observable outcomes are identical;
4. `other_representational`: none of the preceding explanations applies.

Candidate tasks for a mechanism are all expansion rows whose path-mechanism field contains
that exact folded mechanism. The audit emits one CSV row per scoped mechanism, including
the diagnostic control, with the decision, source code, shortest witness, per-version
outcomes, and candidate targets.

### Summary quantities

`N_behavioral_cluster` is the number of new 32 mechanism families classified
`behaviorally_real`; the diagnostic control is excluded. The corpus-closed task upper
bound is computed without selecting tasks: retain only corpus-closed candidates whose
dominant-mechanism cluster contains at least one behaviorally real new mechanism, then sum
`min(4, candidate_count)` over distinct dominant-mechanism clusters. Shared base materials
do not merge clusters. Report this numerical upper bound and the per-cluster arithmetic;
do not output a selected task subset.

The decision-rule hash is SHA-256 over the exact Markdown bytes from the heading
`## Mechanism authenticity audit` through the end of this file and is written beside the
CSV result.
