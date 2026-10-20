import assert from 'node:assert/strict'
import { parseItemEntityData } from './semantic_agent.mjs'

assert.deepEqual(
  parseItemEntityData('Item entity has the following entity data: {id: "minecraft:iron_ore", Count: 1b}'),
  { id: 'minecraft:iron_ore', count: 1 }
)
assert.deepEqual(
  parseItemEntityData('Item entity has the following entity data: {count: 4, id: "minecraft:redstone"}'),
  { id: 'minecraft:redstone', count: 4 }
)
assert.throws(() => parseItemEntityData('{id: "minecraft:raw_iron"}'), /invalid item entity data/)
assert.throws(() => parseItemEntityData('{count: 1}'), /invalid item entity data/)

console.log('pickup fallback parser tests passed')
