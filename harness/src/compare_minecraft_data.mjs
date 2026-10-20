import fs from 'node:fs'
import minecraftData from 'minecraft-data'

const root = '/root/autodl-tmp/webcraft'
const versions = ['1.16.5', '1.19', '1.21.11']
const comparison = {
  generated_at: new Date().toISOString(),
  authority: 'server JAR extraction',
  note: 'minecraft-data differences are diagnostic only; tag expansion and recipe encoding can differ',
  versions: {}
}

function sortedDifference(left, right) {
  return [...left].filter(value => !right.has(value)).sort()
}

for (const version of versions) {
  const truth = JSON.parse(fs.readFileSync(`${root}/truth/${version}.json`, 'utf8'))
  const data = minecraftData(version)
  if (!data) throw new Error(`minecraft-data does not support ${version}`)

  const jarItems = new Set(truth.names.items.map(name => name.replace(/^minecraft:/, '')))
  const mdItems = new Set(data.itemsArray.map(item => item.name))
  const jarCraftable = new Set(Object.keys(truth.craftability).map(name => name.replace(/^minecraft:/, '')))
  const mdCraftable = new Set()
  const mdRecipeCounts = new Map()
  for (const [numericId, recipes] of Object.entries(data.recipes ?? {})) {
    const item = data.items[Number(numericId)]
    if (!item) continue
    mdCraftable.add(item.name)
    mdRecipeCounts.set(item.name, recipes.length)
  }
  const recipeCountMismatches = []
  for (const output of [...jarCraftable].filter(name => mdCraftable.has(name)).sort()) {
    const jarCount = truth.craftability[`minecraft:${output}`]?.length ?? 0
    const mdCount = mdRecipeCounts.get(output) ?? 0
    if (jarCount !== mdCount) recipeCountMismatches.push({ output, jar_count: jarCount, minecraft_data_count: mdCount })
  }
  comparison.versions[version] = {
    jar_item_count: jarItems.size,
    minecraft_data_item_count: mdItems.size,
    items_jar_only: sortedDifference(jarItems, mdItems),
    items_minecraft_data_only: sortedDifference(mdItems, jarItems),
    jar_craftable_output_count: jarCraftable.size,
    minecraft_data_craftable_output_count: mdCraftable.size,
    craftable_outputs_jar_only: sortedDifference(jarCraftable, mdCraftable),
    craftable_outputs_minecraft_data_only: sortedDifference(mdCraftable, jarCraftable),
    recipe_count_mismatches: recipeCountMismatches
  }
}

fs.writeFileSync(
  `${root}/truth/minecraft_data_diff.json`,
  `${JSON.stringify(comparison, null, 2)}\n`,
  { mode: 0o644 }
)
console.log(JSON.stringify(Object.fromEntries(Object.entries(comparison.versions).map(([version, value]) => [version, {
  item_diff: value.items_jar_only.length + value.items_minecraft_data_only.length,
  craftable_output_diff: value.craftable_outputs_jar_only.length + value.craftable_outputs_minecraft_data_only.length,
  recipe_count_mismatches: value.recipe_count_mismatches.length
}]))))
