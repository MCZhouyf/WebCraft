# Discrimination Audit v2 Rule Amendment

Date: 2026-10-13. This post-review amendment responds to W5 and Q6. It is
written after the v1 discrimination audit but before computing any v2 result.
It changes no frozen task, corpus, truth, method, episode, or metric export and
authorizes only deterministic reanalysis of existing records.

## Scope and paired unit

- The confirmatory scope is the 165 method-pair/read-mode rows formed by 11
  common rows, three reading modes, and 55 unordered method pairs. External
  methods remain outside this 165-row matrix.
- Deterministic duplicate seeds are removed. The inferential unit is the task:
  all registered condition-world cells for the same evaluation task are first
  averaged, then the 34 paired task values are compared.
- The v1 columns are retained and relabelled descriptive: "secondary nonzero
  gap" means only that an aggregate point difference is nonzero.

## v2 discrimination rule

For binary episode metrics (SR, HTR_commit, and HTR_all), the audit reports a
two-sided exact McNemar test over paired task-condition-world cells and a paired
task bootstrap over the 34 within-task mean rates. For probe count and probe
cost, it reports the paired task bootstrap. Bootstrap intervals use 10,000
percentile draws and seed 20261013.

A binary difference is statistically and practically separated only when all
three conditions hold: McNemar p < 0.05, the paired-task 95% interval excludes
zero, and the absolute mean-rate difference is at least 0.05. A count/cost
difference is separated only when its paired-task 95% interval excludes zero
and its absolute mean difference is at least 0.5 per episode. SR is called
statistically distinguishable under the same binary rule; otherwise it is
called indistinguishable. An SR-indistinguishable row is separated by a
secondary metric when at least one of HTR_commit, HTR_all, probe count, or
probe cost passes its complete rule. IC and IAA remain descriptive because the
frozen exports do not provide paired task-level observations for every common
row.

The audit also reports a mechanism-cluster bootstrap: the five frozen clusters
in `mechanism_clusters.csv` are sampled with replacement, all tasks in each
sampled cluster are retained, and the paired task-mean difference is recomputed.
The same practical thresholds apply. This cluster result is a sensitivity
analysis and is not substituted for the task-bootstrap v2 count.

## Mechanism-sensitivity rules

All sensitivity rows use existing evaluation records. Five leave-one-cluster
rows remove MC1 through MC5 in turn. Dominant-mechanism groups are assigned in
this fixed priority: an oracle-path conflict involving the iron-ore drop is
`iron_ore_drop`; otherwise a stone-pickaxe recipe conflict is
`stone_pickaxe_recipe`; otherwise any `renamed` conflict is `rename`; all
remaining and control tasks are `other`.

1. **Same-world mismatch.** Use Naive RAG under all_public. Compare matched
   1.21.11 with old-to-new evidence and matched 1.16.5 with new-to-old evidence.
   `保持` requires both HTR_commit increases to remain at least 0.15 and the SR
   signs to remain old-to-new negative and new-to-old positive. `减弱` requires
   positive HTR effects with no SR-sign reversal but fails at least one 0.15
   threshold. Otherwise the conclusion is `消失`.
2. **Verify-All.** Compare Verify-All with Naive RAG over all all_public cells.
   `保持` requires Verify-All HTR_commit to remain exactly zero, Naive RAG HTR_commit
   to remain positive, and the absolute SR difference to remain below 0.05.
   `减弱` requires a positive HTR reduction and an absolute SR difference below
   0.10. Otherwise the conclusion is `消失`.
3. **External feasible-task SR.** Evaluate all five external methods on their
   frozen 110-instance matrix. `保持` means the maximum feasible-task SR is at
   most 0.10; `减弱` means it is above 0.10 and at most 0.15; otherwise it is
   `消失`. Empty mechanism groups are reported `不可估计`, not imputed.

No threshold or grouping rule may be changed after the v2 script is run.
