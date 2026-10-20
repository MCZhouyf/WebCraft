import fs from 'node:fs'
import crypto from 'node:crypto'
import mineflayer from 'mineflayer'
import { Rcon } from 'rcon-client'
import { Vec3 } from 'vec3'
import { resetUsername } from './arena_identity.mjs'

const root = '/root/autodl-tmp/webcraft'
const config = {
  '1.16.5': { gamePort: 25571, rconPort: 25581, secret: 'RCON_1165' },
  '1.19': { gamePort: 25572, rconPort: 25582, secret: 'RCON_119' },
  '1.21.11': { gamePort: 25573, rconPort: 25583, secret: 'RCON_12111' }
}

function readSecrets() {
  return Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
    .trim().split('\n').map(line => line.split('=', 2)))
}

function connectBot(version, port, username) {
  return new Promise((resolve, reject) => {
    const bot = mineflayer.createBot({ host: '127.0.0.1', port, version, auth: 'offline', username })
    const timer = setTimeout(() => reject(new Error('reset bot spawn timeout')), 30_000)
    bot.once('spawn', () => { clearTimeout(timer); resolve(bot) })
    bot.once('error', reject)
    bot.once('kicked', reason => reject(new Error(String(reason))))
  })
}

async function reset(version, arenaIndex = 0) {
  const target = config[version]
  if (!target) throw new Error(`unsupported target ${version}`)
  const arena = JSON.parse(fs.readFileSync(`${root}/harness/config/arena.json`, 'utf8'))
  const secrets = readSecrets()
  const username = resetUsername(version)
  const bot = await connectBot(version, target.gamePort, username)
  const rcon = await Rcon.connect({ host: '127.0.0.1', port: target.rconPort, password: secrets[target.secret] })
  const dx = arenaIndex * arena.spacing
  const send = command => rcon.send(command)
  try {
    await send(`forceload add ${arena.bounds.min.x + dx} ${arena.bounds.min.z} ${arena.bounds.max.x + dx} ${arena.bounds.max.z}`)
    await send(`fill ${arena.bounds.min.x + dx} ${arena.bounds.min.y} ${arena.bounds.min.z} ${arena.bounds.max.x + dx} ${arena.bounds.max.y} ${arena.bounds.max.z} air`)
    await send(`fill ${arena.bounds.min.x + dx} ${arena.bounds.min.y} ${arena.bounds.min.z} ${arena.bounds.max.x + dx} ${arena.bounds.min.y} ${arena.bounds.max.z} stone`)
    await send(`setblock ${arena.workbench.x + dx} ${arena.workbench.y} ${arena.workbench.z} crafting_table`)
    await send(`setblock ${arena.furnace.x + dx} ${arena.furnace.y} ${arena.furnace.z} furnace`)
    for (const resource of arena.resources) {
      await send(`setblock ${resource.x + dx} ${resource.y} ${resource.z} ${resource.block}`)
    }
    await send(`clear ${username}`)
    for (const item of arena.initialItems) await send(`give ${username} ${item.name} ${item.count}`)
    await send(`tp ${username} ${arena.spawn.x + dx} ${arena.spawn.y} ${arena.spawn.z}`)
    await bot.waitForTicks(10)

    const state = []
    for (let x = arena.bounds.min.x; x <= arena.bounds.max.x; x += 1) {
      for (let z = arena.bounds.min.z; z <= arena.bounds.max.z; z += 1) {
        const floor = bot.blockAt(new Vec3(x + dx, arena.bounds.min.y, z))
        state.push([x + dx, arena.bounds.min.y, z, floor?.name ?? null])
      }
    }
    for (const point of [arena.workbench, arena.furnace, ...arena.resources]) {
      const block = bot.blockAt(new Vec3(point.x + dx, point.y, point.z))
      state.push([point.x + dx, point.y, point.z, block?.name ?? null])
    }
    const inventory = bot.inventory.items().map(item => [item.name, item.count]).sort()
    const hash = crypto.createHash('sha256').update(JSON.stringify({ state, inventory })).digest('hex')
    const record = { timestamp: new Date().toISOString(), version, arenaIndex, hash, inventory }
    fs.appendFileSync(`${root}/logs/reset_hashes.jsonl`, `${JSON.stringify(record)}\n`, { mode: 0o600 })
    console.log(JSON.stringify(record))
  } finally {
    rcon.end()
    bot.quit('arena reset complete')
  }
}

await reset(process.argv[2], Number(process.argv[3] ?? 0))
