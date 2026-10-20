# A21 offline feasibility validation before formal calls

Before any formal MDS agent episode, recheck the frozen 32 material labels
with the existing MIP oracle at action_limit=90, using each material's exact
initial inventory and target. Compare feasible status and optimal cost to the
existing action_limit=10 material-building result. No MDS instance is replaced
or omitted based on a model result. If the labels disagree or a newly feasible
instance has cost outside 4--10, record it as a material-construction issue;
the present formal material is not eligible for agent execution until that
issue is explicitly addressed in a separate registered material version.

This check reads WebCraft only, uses one CPU solver thread, and makes zero
model calls. It does not alter existing material or oracle tables.
