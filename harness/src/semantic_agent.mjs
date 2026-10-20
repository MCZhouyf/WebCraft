import fs from 'node:fs'
import crypto from 'node:crypto'
import mineflayer from 'mineflayer'
import pathfinderPackage from 'mineflayer-pathfinder'
import { Rcon } from 'rcon-client'
import { Vec3 } from 'vec3'
import { publicValue, primaryCost, itemCounts, itemFlow, safeError, evaluationGate } from './public_contract.mjs'

const { pathfinder } = pathfinderPackage

const sanitize = publicValue

function inventorySnapshot(bot) {
  return itemCounts(bot.inventory.items())
}

function inventoryDelta(before, after) {
  const names = new Set([...Object.keys(before), ...Object.keys(after)])
  return [...names]
    .map(name => ({ name, count: (after[name] ?? 0) - (before[name] ?? 0) }))
    .filter(item => item.count > 0)
}

function isItemEntity(entity) {
  const name = String(entity?.name ?? '').toLowerCase()
  return name === 'item' || name === 'item_stack'
}

export function parseItemEntityData(response) {
  const text = String(response)
  const id = text.match(/\bid\s*:\s*"(minecraft:[a-z0-9_./-]+)"/i)?.[1]
  const countText = text.match(/\b(?:Count|count)\s*:\s*(\d+)[bBsSlL]?\b/)?.[1]
  const count = Number(countText)
  if (!id || !Number.isInteger(count) || count < 1 || count > 999) {
    throw new Error('invalid item entity data')
  }
  return { id: id.toLowerCase(), count }
}

class SemanticAgentCore {
  #bot
  #rcon
  #arena
  #logPath
  #diagnosticPath
  #finished = false
  #sequence = 0
  #privateEvents = []

  #diagnostic(event, details = {}) {
    if (!this.#diagnosticPath) return
    const position = this.#bot.entity.position
    fs.appendFileSync(this.#diagnosticPath, `${JSON.stringify({
      timestamp: new Date().toISOString(), event,
      position: { x: position.x, y: position.y, z: position.z },
      ...details
    })}\n`, { encoding: 'utf8', mode: 0o600 })
  }

  #surroundings() {
    const center = this.#bot.entity.position.floored()
    const blocks = []
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dz = -1; dz <= 1; dz += 1) {
          const position = center.offset(dx, dy, dz)
          blocks.push({ dx, dy, dz, name: this.#bot.blockAt(position)?.name ?? null })
        }
      }
    }
    return blocks
  }

  async #teleport(point, purpose) {
    if (!point || !['x', 'y', 'z'].every(axis => Number.isFinite(point[axis]))) {
      throw new Error(`missing fixed ${purpose} point`)
    }
    const target = new Vec3(point.x, point.y, point.z)
    this.#diagnostic('teleport_start', { purpose, target: point })
    await this.#rcon.send(`tp ${this.#bot.username} ${point.x} ${point.y} ${point.z}`)
    for (let tick = 0; tick < 40; tick += 1) {
      if (this.#bot.entity.position.distanceTo(target) <= 0.25) {
        this.#diagnostic('teleport_reached', { purpose, target: point, ticks: tick })
        return
      }
      await this.#bot.waitForTicks(1)
    }
    this.#diagnostic('teleport_timeout', {
      purpose, target: point, surroundings_3x3x3: this.#surroundings()
    })
    throw new Error('teleport synchronization timeout')
  }

  #inventoryIncrease(before) {
    return inventoryDelta(before, inventorySnapshot(this.#bot))
  }

  async #pollInventoryIncrease(before, timeoutMs) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const items = this.#inventoryIncrease(before)
      if (items.length) return items
      await this.#bot.waitForTicks(1)
    }
    return this.#inventoryIncrease(before)
  }

  async #tagDropEntity(entity, index) {
    const tag = `wc_pickup_${process.pid}_${this.#sequence + 1}_${index}`
    const { x, y, z } = entity.position
    const selector = '@e[type=minecraft:item,sort=nearest,limit=1,distance=..1]'
    const response = await this.#rcon.send(
      `execute positioned ${x} ${y} ${z} run tag ${selector} add ${tag}`
    )
    if (/no entity|found no/i.test(String(response))) throw new Error('drop entity tagging failed')
    return tag
  }

  async #fallbackTaggedDrop(tag) {
    const selector = `@e[type=minecraft:item,tag=${tag},limit=1]`
    const response = await this.#rcon.send(`data get entity ${selector} Item`)
    if (/no entity|found no/i.test(String(response))) return null
    const stack = parseItemEntityData(response)
    const killed = await this.#rcon.send(`kill ${selector}`)
    if (/no entity|found no/i.test(String(killed))) throw new Error('drop entity kill failed')
    await this.#rcon.send(`give ${this.#bot.username} ${stack.id} ${stack.count}`)
    this.#privateEvents.push({
      event: 'pickup_entity_fallback', source: 'actual_entity_nbt',
      item_id: stack.id, count: stack.count
    })
    return stack
  }

  async #collectMinedDrops(node, before, knownEntityIds, observedDrops) {
    await this.#bot.waitForTicks(15)
    let items = this.#inventoryIncrease(before)
    if (items.length) return items

    const origin = new Vec3(node.x + 0.5, node.y + 0.5, node.z + 0.5)
    const candidates = [...observedDrops, ...Object.values(this.#bot.entities)]
      .filter(entity => isItemEntity(entity)
        && !knownEntityIds.has(entity.id)
        && entity.position.distanceTo(origin) <= 3)
    const drops = [...new Map(candidates.map(entity => [entity.id, entity])).values()]
      .map(entity => this.#bot.entities[entity.id] ?? entity)
    if (!drops.length) throw new Error('no post-dig item entity observed')

    const tags = []
    for (let index = 0; index < drops.length; index += 1) {
      tags.push(await this.#tagDropEntity(drops[index], index))
    }
    await this.#teleport(drops[0].position, 'drop entity pickup')
    items = await this.#pollInventoryIncrease(before, 3_000)
    if (items.length) return items

    let fallbackCount = 0
    for (const tag of tags) {
      if (await this.#fallbackTaggedDrop(tag)) fallbackCount += 1
    }
    if (!fallbackCount) throw new Error('observed drop entity disappeared before fallback')
    items = await this.#pollInventoryIncrease(before, 3_000)
    if (!items.length) throw new Error('entity-grounded pickup fallback failed')
    return items
  }

  constructor(bot, rcon, arena, logPath, diagnosticPath = null) {
    this.#bot = bot
    this.#rcon = rcon
    this.#arena = arena
    this.#logPath = logPath
    this.#diagnosticPath = diagnosticPath
  }

  async #invoke(method, params, operation) {
    if (this.#finished) throw new Error('episode closed')
    const started = performance.now()
    const before = inventorySnapshot(this.#bot)
    const privateStart = this.#privateEvents.length
    let result
    try {
      result = sanitize(await operation())
    } catch (error) {
      result = { success: false, error: safeError(error) }
    }
    const flow = itemFlow(before, inventorySnapshot(this.#bot))
    const cost = {
      primary: primaryCost(method, params),
      wall_ms: Math.round(performance.now() - started),
      net_displacement: 'not_applicable',
      consumed_items: flow.consumed,
      model_calls: 0
    }
    if (result && typeof result === 'object' && !Array.isArray(result)) result.cost = cost
    const privateEvents = this.#privateEvents.splice(privateStart)
    this.#writeLog(method, sanitize(params), result, result?.success !== false, cost, flow,
      privateEvents)
    return result
  }

  #writeLog(method, params, result, success, cost, flow, privateEvents) {
    const record = {
      timestamp: new Date().toISOString(),
      schema_version: 2,
      sequence: ++this.#sequence,
      method,
      params,
      result,
      cost,
      item_flow: flow,
      success
    }
    if (privateEvents.length) record.private = { pickup_fallbacks: privateEvents }
    fs.appendFileSync(this.#logPath, `${JSON.stringify(record)}\n`, { encoding: 'utf8', mode: 0o600 })
  }

  inventory() {
    return this.#invoke('inventory', {}, async () => this.#bot.inventory.items()
      .map(item => ({ name: item.name, count: item.count })))
  }

  mine(nodeId) {
    return this.#invoke('mine', { nodeId }, async () => {
      const node = this.#arena.resources.find(entry => entry.nodeId === nodeId)
      if (!node) throw new Error('unknown resource node')
      const before = inventorySnapshot(this.#bot)
      const knownEntityIds = new Set(Object.values(this.#bot.entities)
        .filter(isItemEntity).map(entity => entity.id))
      const observedDrops = []
      const onItemDrop = entity => observedDrops.push(entity)
      await this.#teleport(node.station, 'resource interaction')
      const block = this.#bot.blockAt(new Vec3(node.x, node.y, node.z))
      if (!block || block.name === 'air') throw new Error('resource node is empty')
      const tool = this.#bot.pathfinder.bestHarvestTool(block)
      if (tool) await this.#bot.equip(tool, 'hand')
      this.#bot.on('itemDrop', onItemDrop)
      let items
      try {
        await this.#bot.dig(block, true)
        items = await this.#collectMinedDrops(node, before, knownEntityIds, observedDrops)
      } finally {
        this.#bot.removeListener('itemDrop', onItemDrop)
      }
      return {
        success: true,
        items,
        cost: {
          environment_steps: 1,
          travel_distance: 'not_applicable'
        }
      }
    })
  }

  craft_grid(layout) {
    return this.#invoke('craft_grid', { layout }, async () => {
      if (!Array.isArray(layout) || layout.length !== 9) throw new Error('layout must contain exactly 9 cells')
      const table = this.#bot.blockAt(new Vec3(
        this.#arena.workbench.x,
        this.#arena.workbench.y,
        this.#arena.workbench.z
      ))
      if (!table || table.name !== 'crafting_table') throw new Error('crafting table unavailable')
      await this.#teleport(this.#arena.workbench.station, 'workbench interaction')
      const window = await this.#bot.openBlock(table)
      let clicks = 0
      try {
        for (let index = 0; index < layout.length; index += 1) {
          const cell = layout[index]
          if (!cell) continue
          const name = typeof cell === 'string' ? cell : cell.name
          const count = typeof cell === 'string' ? 1 : (cell.count ?? 1)
          const sourceSlot = window.slots.findIndex((item, slot) =>
            slot >= window.inventoryStart && slot < window.inventoryEnd && item?.name === name && item.count >= count)
          if (sourceSlot < 0) throw new Error(`missing ingredient: ${name}`)
          await this.#bot.clickWindow(sourceSlot, 0, 0)
          clicks += 1
          for (let amount = 0; amount < count; amount += 1) {
            await this.#bot.clickWindow(index + 1, 1, 0)
            clicks += 1
          }
          if (this.#bot.currentWindow?.selectedItem) {
            await this.#bot.clickWindow(sourceSlot, 0, 0)
            clicks += 1
          }
        }
        await this.#bot.waitForTicks(5)
        const output = window.slots[0]
        if (!output) {
          for (let slot = 1; slot <= 9; slot += 1) {
            if (window.slots[slot]) {
              await this.#bot.clickWindow(slot, 0, 1)
              clicks += 1
            }
          }
          return { success: false, product: null, cost: { environment_steps: clicks } }
        }
        const product = { name: output.name, count: output.count }
        await this.#bot.clickWindow(0, 0, 0)
        clicks += 1
        const empty = window.slots.findIndex((item, slot) =>
          slot >= window.inventoryStart && slot < window.inventoryEnd && !item)
        if (empty < 0) throw new Error('no inventory slot available for crafting result')
        await this.#bot.clickWindow(empty, 0, 0)
        clicks += 1
        return { success: true, product, cost: { environment_steps: clicks } }
      } finally {
        window.close()
      }
    })
  }

  smelt(input, fuel, n) {
    return this.#invoke('smelt', { input, fuel, n }, async () => {
      if (!Number.isInteger(n) || n < 1) throw new Error('n must be a positive integer')
      const furnaceBlock = this.#bot.blockAt(new Vec3(
        this.#arena.furnace.x,
        this.#arena.furnace.y,
        this.#arena.furnace.z
      ))
      if (!furnaceBlock || furnaceBlock.name !== 'furnace') throw new Error('furnace unavailable')
      await this.#teleport(this.#arena.furnace.station, 'furnace interaction')
      const inputItem = this.#bot.inventory.items().find(item => item.name === input && item.count >= n)
      const fuelItem = this.#bot.inventory.items().find(item => item.name === fuel)
      if (!inputItem || !fuelItem) throw new Error('missing smelting input or fuel')
      const furnace = await this.#bot.openFurnace(furnaceBlock)
      try {
        if (furnace.inputItem() || furnace.outputItem()) throw new Error('furnace must be empty before smelting')
        await furnace.putInput(inputItem.type, null, n)
        await furnace.putFuel(fuelItem.type, null, Math.min(fuelItem.count, n))
        const deadline = Date.now() + Math.max(30_000, n * 15_000)
        while ((furnace.outputItem()?.count ?? 0) < n && Date.now() < deadline) await this.#bot.waitForTicks(10)
        if ((furnace.outputItem()?.count ?? 0) < n) throw new Error('smelting timeout')
        const output = await furnace.takeOutput()
        return {
          success: true,
          items: [{ name: output.name, count: output.count }],
          cost: { environment_steps: n }
        }
      } finally {
        if (furnace.inputItem()) await furnace.takeInput()
        if (furnace.fuelItem()) await furnace.takeFuel()
        furnace.close()
      }
    })
  }

  check_name(name) {
    return this.#invoke('check_name', { name }, async () => {
      if (typeof name !== 'string' || !/^(minecraft:)?[a-z_]+$/.test(name)) throw new Error('invalid item identifier')
      return { resolvable: Boolean(this.#bot.registry.itemsByName[name.replace(/^minecraft:/, '')]) }
    })
  }

  report_version(guess) {
    return this.#invoke('report_version', {
      guess_sha256: crypto.createHash('sha256').update(String(guess)).digest('hex')
    }, async () => ({ confirmed: true }))
  }

  submit(result) {
    return this.#invoke('submit', { result: sanitize(result) }, async () => ({ confirmed: true }))
  }

  search(q, k) {
    return this.#invoke('search', { q, k }, async () => [])
  }

  async shutdown() {
    this.#finished = true
    this.#rcon.end()
    this.#bot.quit('semantic harness shutdown')
  }

  finishEpisode() { this.#finished = true }
}

export async function createSemanticAgent(options) {
  const arena = JSON.parse(fs.readFileSync(options.arenaPath, 'utf8'))
  const bot = mineflayer.createBot({
    host: '127.0.0.1',
    port: options.gamePort,
    version: options.minecraftVersion,
    auth: 'offline',
    username: options.username,
    hideErrors: false
  })
  bot.loadPlugin(pathfinder)
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('bot spawn timeout')), 30_000)
    bot.once('spawn', () => { clearTimeout(timer); resolve() })
    bot.once('error', reject)
    bot.once('kicked', () => reject(new Error('connection rejected')))
  })
  const rcon = await Rcon.connect({
    host: '127.0.0.1',
    port: options.rconPort,
    password: options.rconPassword
  })
  const core = new SemanticAgentCore(bot, rcon, arena, options.logPath,
    options.diagnosticPath ?? null)
  // Evaluator control is returned separately and must not be given to the agent.
  const gate = evaluationGate(options.answerClaims)
  const evaluator = Object.freeze({
    finish_episode() { core.finishEpisode(); gate.finish_episode() },
    answer_claims: gate.answer_claims
  })
  const api = Object.freeze({
    inventory: core.inventory.bind(core),
    mine: core.mine.bind(core),
    craft_grid: core.craft_grid.bind(core),
    smelt: core.smelt.bind(core),
    check_name: core.check_name.bind(core),
    report_version: core.report_version.bind(core),
    submit: core.submit.bind(core),
    search: core.search.bind(core)
  })
  return { api, evaluator, shutdown: core.shutdown.bind(core) }
}
