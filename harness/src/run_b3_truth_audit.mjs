import fs from 'node:fs'
import { Rcon } from 'rcon-client'
import { createSemanticAgent } from './semantic_agent.mjs'

const root = '/root/autodl-tmp/webcraft'
const version = process.argv[2]
const targets = {
  '1.16.5': { gamePort: 25571, rconPort: 25581, secret: 'RCON_1165' },
  '1.19': { gamePort: 25572, rconPort: 25582, secret: 'RCON_119' },
  '1.21.11': { gamePort: 25573, rconPort: 25583, secret: 'RCON_12111' }
}
if (!targets[version]) throw new Error('invalid version')
const manifest = JSON.parse(fs.readFileSync(`${root}/reports/b3_sample_manifest.json`, 'utf8'))
const samples = manifest.versions[version]
const secrets = Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
  .trim().split('\n').map(line => line.split('=', 2)))
const target = targets[version]
const username = `wc_b3_${version.replaceAll('.', '_')}`
const password = secrets[target.secret]
const logPath = `${root}/logs/b3/${version}.jsonl`
fs.mkdirSync(`${root}/logs/b3`, { recursive: true })
if (fs.existsSync(logPath)) throw new Error(`B3 action log already exists: ${logPath}`)

const rcon = await Rcon.connect({ host: '127.0.0.1', port: target.rconPort, password })
const handle = await createSemanticAgent({
  minecraftVersion: version,
  gamePort: target.gamePort,
  rconPort: target.rconPort,
  rconPassword: password,
  username,
  arenaPath: `${root}/harness/config/arena.json`,
  logPath
})

const strip = name => name.replace(/^minecraft:/, '')
const counts = items => Object.fromEntries(items.map(item => [item.name, item.count]))
const clearAndGive = async items => {
  await rcon.send(`clear ${username}`)
  for (const [name, count] of Object.entries(items)) {
    await rcon.send(`give ${username} minecraft:${strip(name)} ${count}`)
  }
}
const rows = []
try {
  await rcon.send(`tp ${username} 0.5 4 0.5`)
  for (const sample of samples.recipes) {
    const ingredients = {}
    if (sample.method === 'craft_grid') {
      for (const name of sample.params.layout.filter(Boolean)) {
        ingredients[name] = (ingredients[name] ?? 0) + 1
      }
    } else {
      ingredients[sample.params.input] = sample.params.n
      ingredients[sample.params.fuel] = sample.params.n
    }
    await clearAndGive(ingredients)
    const response = sample.method === 'craft_grid'
      ? await handle.api.craft_grid(sample.params.layout)
      : await handle.api.smelt(sample.params.input, sample.params.fuel, sample.params.n)
    const inventory = counts(await handle.api.inventory())
    const observed = inventory[sample.expected.item] ?? 0
    rows.push({
      kind: 'recipe', id: sample.recipe_id, method: sample.method,
      expected: sample.expected, observed_count: observed,
      agreement: response.success === true && observed >= sample.expected.count,
      public_error: response.success === false ? response.error ?? 'action_failed' : null
    })
  }

  const [x, y, z] = [0, 4, 20]
  for (const sample of samples.drops) {
    await rcon.send(`clear ${username}`)
    await rcon.send(`setblock ${x} ${y} ${z} minecraft:${sample.block}`)
    const commandResult = await rcon.send(`loot give ${username} mine ${x} ${y} ${z}`)
    // RCON returns before the bot has necessarily processed its inventory packet.
    await new Promise(resolve => setTimeout(resolve, 500))
    const inventory = counts(await handle.api.inventory())
    const observedItems = Object.keys(inventory).filter(name => inventory[name] > 0).sort()
    const expectedItems = [...sample.expected_items].sort()
    rows.push({
      kind: 'drop', id: `minecraft:${sample.block}`,
      expected_items: expectedItems, observed_items: observedItems,
      agreement: JSON.stringify(observedItems) === JSON.stringify(expectedItems),
      command_result: commandResult
    })
    await rcon.send(`setblock ${x} ${y} ${z} air`)
  }
  console.log(JSON.stringify({
    version, rows,
    recipe_agreement: rows.filter(row => row.kind === 'recipe' && row.agreement).length,
    recipe_total: rows.filter(row => row.kind === 'recipe').length,
    drop_agreement: rows.filter(row => row.kind === 'drop' && row.agreement).length,
    drop_total: rows.filter(row => row.kind === 'drop').length
  }))
} finally {
  await handle.shutdown()
  rcon.end()
}
