# A21 family-count metadata correction

Rule formed on 2026-10-18 before any formal A21 model episode. The formal
output directory does not exist at the time of this correction. Development
results have been observed; no development outcome determines this change.

The original preregistration calls the eight source tasks eight independent
families. The frozen visible materials instead contain seven distinct
`family_id` values. Both `extv2_012` and `extv2_013` belong to
`rename:chain_iron_chain`; they must remain in the same bootstrap cluster.
The other source tasks have these frozen family IDs:

| Source task | Family |
| --- | --- |
| extv2_001 | feature:blue_wool_chain |
| extv2_003 | feature:raw_ingot_smelting |
| extv2_007 | item:chiseled_bookshelf |
| extv2_009 | item:lodestone |
| extv2_010 | item:oak_chest_boat |
| extv2_011 | item:saddle |
| extv2_012 and extv2_013 | rename:chain_iron_chain |

The existing bootstrap implementation already groups by actual `family_id`.
Only the hardcoded eight-family label in the contrast export is corrected
to the number of actual eligible family IDs. Feasible-only exports count
families represented among feasible instances. Point estimates, sampling
seed, draws, contrasts, tasks, fact materials, and model behavior do not
change. The original preregistration remains intact and is read with this
explicit correction.
