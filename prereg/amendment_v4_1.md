# Preregistration Amendment V4.1: Post Hoc S1.5 Reconciliation

**Post hoc supplement written after observing S1.5 results on 2026-10-06.** This
document records the author's adjudication of the four non-D1 reconciliation
differences. No LLM-agent result exists. The task corpus, truth files, oracle and
stale expectations, semantic-cost model, resource model, recipe policy, and their
frozen hashes are unchanged.

## Reconciliation Result

Of 97 successful historical live traces, 93 are exactly concordant after D1
normalization. The remaining four differences are retained as evidence and are
explained below: two are live-execution artifacts for which the simulator is
correct, and two are covered by D5, where the simulator is more conservative. This
is not a claim that 97 of 97 traces passed parity.

### Live-Execution Artifacts

1. `con_002 / 1.21.11` mined coal and also collected `blue_carpet`. The preserved
   log is `logs/archive/pre_teleport_amendment_20261006/b2/eval/1.21.11/con_002.attempt1.jsonl`,
   SHA-256 `62231e1fc84d722455312931b92bc21471b256c18f8a18d954a582a84291328d`.
   This is adjudicated as **live-world incidental-item pickup**. `blue_carpet` is
   not the coal goal, is never consumed by a later action, does not participate in
   goal attainment, and the anomalous mine action retains primary cost 1; its only
   following inventory and submit records have primary cost 0.
2. `pla_001 / 1.21.11` crafted lead and also produced `leather_boots`. The preserved
   log is `logs/archive/pre_v3_20261006/b2/dev/1.21.11/pla_001.attempt1.jsonl`,
   SHA-256 `73b19ad1315dd801f2d27a4069616956bd0ec9e11478d871530940d046f5636f`.
   This is adjudicated as a **live harness container-interaction artifact**: a
   residual crafting-grid item was also crafted. `leather_boots` is not the lead
   goal, is never consumed later, does not participate in goal attainment, and the
   anomalous craft action retains primary cost 1; its only following inventory and
   submit records have primary cost 0.

Both artifact logs remain unmodified in the evidence archive. The simulator's
single semantic mining/crafting result is correct for these two cases.

### D5: Furnace Remaining-Burn-Time Difference

D5 is added to the declared difference list D1-D5:

> The simulator does not model carry-over of furnace remaining burn time between
> `smelt` actions in the same episode. Each `smelt` action consumes
> `ceil(n / 8)` coal, matching the frozen oracle fuel model. Live Minecraft may
> consume less coal.

`pla_012` in 1.16.5 and 1.19 each performed a prior same-episode `n=3` smelt, then
ten intervening semantic actions, before the `n=9` gold smelt. The elapsed wall
times from the prior smelt record to the `n=9` record were 120.116 seconds and
120.422 seconds, respectively. The schema-v2 logs contain neither furnace
burn-time nor tick-state fields, so they establish the prior-smelt condition but
cannot prove the residual burn amount. D5 applies regardless. It is conservative:
both oracle plans are feasible under either fuel accounting, and semantic action
cost is unchanged.

## Scope

This adjudication authorizes S2 only. It neither broadens simulator scope nor
alters any frozen input. The live backend remains frozen; no live server, bot, GPU,
or LLM work is performed by S2.
