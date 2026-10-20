import fs from 'node:fs'
import { Rcon } from 'rcon-client'
import { createSemanticAgent } from './semantic_agent.mjs'

const root = '/root/autodl-tmp/webcraft'
const targets = {
  '1.16.5': { gamePort: 25571, rconPort: 25581, secret: 'RCON_1165', username: 'wc_rollback_1165' },
  '1.19': { gamePort: 25572, rconPort: 25582, secret: 'RCON_119', username: 'wc_rollback_119' },
  '1.21.11': { gamePort: 25573, rconPort: 25583, secret: 'RCON_12111', username: 'wc_rollback_121' }
}
const secrets = Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
  .trim().split('\n').map(line => line.split('=', 2)))
const output = { generated_at: new Date().toISOString(), versions: {} }

for (const [version, target] of Object.entries(targets)) {
  const password = secrets[target.secret]
  const rcon = await Rcon.connect({ host: '127.0.0.1', port: target.rconPort, password })
  const { api, shutdown } = await createSemanticAgent({
    minecraftVersion: version,
    gamePort: target.gamePort,
    rconPort: target.rconPort,
    rconPassword: password,
    username: target.username,
    arenaPath: `${root}/harness/config/arena.json`,
    logPath: `${root}/logs/actions-rollback-${version}.jsonl`
  })
  try {
    await rcon.send(`clear ${target.username}`)
    await rcon.send(`give ${target.username} paper 1`)
    await rcon.send(`give ${target.username} iron_nugget 1`)
    await rcon.send(`tp ${target.username} 0.5 4 0.5`)
    const craft = await api.craft_grid(['paper', 'iron_nugget', null, null, null, null, null, null, null])
    const inventory = await api.inventory()
    const counts = Object.fromEntries(inventory.map(item => [item.name, item.count]))
    output.versions[version] = {
      craft_failed: craft.success === false,
      ingredients_returned: counts.paper === 1 && counts.iron_nugget === 1,
      craft,
      inventory
    }
  } finally {
    await shutdown()
    rcon.end()
  }
}

output.passed = Object.values(output.versions).every(row => row.craft_failed && row.ingredients_returned)
fs.writeFileSync(`${root}/reports/failure_rollback.json`, `${JSON.stringify(output, null, 2)}\n`)
console.log(JSON.stringify(output))
