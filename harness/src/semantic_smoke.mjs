import fs from 'node:fs'
import { createSemanticAgent } from './semantic_agent.mjs'

const root = '/root/autodl-tmp/webcraft'
const targets = {
  '1.16.5': { gamePort: 25571, rconPort: 25581, secret: 'RCON_1165', username: 'wc_reset_1165' },
  '1.19': { gamePort: 25572, rconPort: 25582, secret: 'RCON_119', username: 'wc_reset_119' },
  '1.21.11': { gamePort: 25573, rconPort: 25583, secret: 'RCON_12111', username: 'wc_reset_12111' }
}

const secrets = Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
  .trim().split('\n').map(line => line.split('=', 2)))

const version = process.argv[2]
const target = targets[version]
if (!target) throw new Error(`unknown target ${version}`)

const { api, shutdown } = await createSemanticAgent({
  minecraftVersion: version,
  gamePort: target.gamePort,
  rconPort: target.rconPort,
  rconPassword: secrets[target.secret],
  username: target.username,
  arenaPath: `${root}/harness/config/arena.json`,
  logPath: `${root}/logs/actions-${version}.jsonl`
})

try {
  const results = []
  results.push(['inventory', await api.inventory()])
  results.push(['mine_log', await api.mine('log_1')])
  results.push(['craft_planks', await api.craft_grid(['oak_log', null, null, null, null, null, null, null, null])])
  results.push(['craft_table', await api.craft_grid([
    'oak_planks', 'oak_planks', null,
    'oak_planks', 'oak_planks', null,
    null, null, null
  ])])
  results.push(['mine_stone', await api.mine('stone_1')])
  results.push(['craft_stone_pickaxe', await api.craft_grid([
    'cobblestone', 'cobblestone', 'cobblestone',
    null, 'stick', null,
    null, 'stick', null
  ])])
  results.push(['check_name', await api.check_name('scute')])
  results.push(['search_stub', await api.search('crafting change', 3)])
  console.log(JSON.stringify({ version, results }))
} finally {
  await shutdown()
}
