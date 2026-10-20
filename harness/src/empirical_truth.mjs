import fs from 'node:fs'
import { Rcon } from 'rcon-client'
import { createSemanticAgent } from './semantic_agent.mjs'

const root = '/root/autodl-tmp/webcraft'
const targets = {
  '1.16.5': { gamePort: 25571, rconPort: 25581, secret: 'RCON_1165', username: 'wc_truth_1165' },
  '1.19': { gamePort: 25572, rconPort: 25582, secret: 'RCON_119', username: 'wc_truth_119' },
  '1.21.11': { gamePort: 25573, rconPort: 25583, secret: 'RCON_12111', username: 'wc_truth_12111' }
}
const secrets = Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
  .trim().split('\n').map(line => line.split('=', 2)))

const craftProbes = [
  {
    id: 2,
    name: 'cobbled_deepslate_stone_pickaxe',
    items: { cobbled_deepslate: 3, stick: 2 },
    layout: ['cobbled_deepslate', 'cobbled_deepslate', 'cobbled_deepslate', null, 'stick', null, null, 'stick', null]
  },
  {
    id: 5,
    name: 'string_leather_bundle',
    items: { string: 1, leather: 1 },
    layout: ['string', null, null, 'leather', null, null, null, null, null]
  },
  {
    id: 6,
    name: 'iron_lodestone',
    items: { chiseled_stone_bricks: 8, iron_ingot: 1 },
    layout: ['chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'iron_ingot', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks']
  },
  {
    id: 7,
    name: 'netherite_lodestone',
    items: { chiseled_stone_bricks: 8, netherite_ingot: 1 },
    layout: ['chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'netherite_ingot', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks', 'chiseled_stone_bricks']
  },
  {
    id: 8,
    name: 'leather_iron_saddle',
    items: { leather: 3, iron_ingot: 1 },
    layout: [null, 'leather', null, 'leather', 'iron_ingot', 'leather', null, null, null]
  },
  {
    id: 9,
    name: 'copper_pickaxe',
    items: { copper_ingot: 3, stick: 2 },
    layout: ['copper_ingot', 'copper_ingot', 'copper_ingot', null, 'stick', null, null, 'stick', null]
  },
  {
    id: 12,
    name: 'paper_iron_nugget_name_tag',
    items: { paper: 1, iron_nugget: 1 },
    layout: ['paper', 'iron_nugget', null, null, null, null, null, null, null]
  }
]

async function runVersion(version) {
  const target = targets[version]
  const password = secrets[target.secret]
  const rcon = await Rcon.connect({ host: '127.0.0.1', port: target.rconPort, password })
  const { api, shutdown } = await createSemanticAgent({
    minecraftVersion: version,
    gamePort: target.gamePort,
    rconPort: target.rconPort,
    rconPassword: password,
    username: target.username,
    arenaPath: `${root}/harness/config/arena.json`,
    logPath: `${root}/logs/actions-truth-${version}.jsonl`
  })
  const rows = []
  const give = async (items) => {
    await rcon.send(`clear ${target.username}`)
    for (const [name, count] of Object.entries(items)) {
      await rcon.send(`give ${target.username} ${name} ${count}`)
    }
  }
  try {
    await rcon.send(`tp ${target.username} 0.5 4 0.5`)
    await give({ iron_pickaxe: 1 })
    await rcon.send('setblock 16 4 0 iron_ore')
    const mined = await api.mine('iron_1')
    rows.push({
      id: 1,
      probe: 'iron_ore_drops_raw_iron_without_silk',
      empirical: Boolean(mined.items?.some(item => item.name === 'raw_iron' && item.count > 0)),
      detail: mined
    })

    const names = [
      [3, 'name_scute_exists', 'scute'],
      [4, 'name_turtle_scute_exists', 'turtle_scute'],
      [10, 'name_chain_exists', 'chain'],
      [11, 'name_iron_chain_exists', 'iron_chain']
    ]
    for (const [id, probe, name] of names) {
      const result = await api.check_name(name)
      rows.push({ id, probe, empirical: result.resolvable === true, detail: result })
    }

    for (const probe of craftProbes) {
      await give(probe.items)
      const result = await api.craft_grid(probe.layout)
      rows.push({ id: probe.id, probe: probe.name, empirical: result.success === true, detail: result })
    }

    await rcon.send('setblock 6 4 0 furnace')
    await give({ iron_ore: 1, coal: 1 })
    const smelt = await api.smelt('iron_ore', 'coal', 1)
    return { rows: rows.sort((a, b) => a.id - b.id), smelt }
  } finally {
    await shutdown()
    rcon.end()
  }
}

const output = { generated_at: new Date().toISOString(), versions: {} }
for (const version of Object.keys(targets)) {
  output.versions[version] = await runVersion(version)
  console.log(JSON.stringify({ version, passed_probes: output.versions[version].rows.filter(row => row.empirical).map(row => row.id), smelt: output.versions[version].smelt }))
}
fs.writeFileSync(`${root}/truth/separator_empirical.json`, `${JSON.stringify(output, null, 2)}\n`)
