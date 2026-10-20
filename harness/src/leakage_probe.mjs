import fs from 'node:fs'
import { createSemanticAgent } from './semantic_agent.mjs'

const root = '/root/autodl-tmp/webcraft'
const secrets = Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
  .trim().split('\n').map(line => line.split('=', 2)))

const { api, shutdown } = await createSemanticAgent({
  minecraftVersion: '1.19',
  gamePort: 25572,
  rconPort: 25582,
  rconPassword: secrets.RCON_119,
  username: 'wc_leak_probe',
  arenaPath: `${root}/harness/config/arena.json`,
  logPath: `${root}/logs/actions-leakage.jsonl`
})

const forbidden = ['bot', 'version', 'registry', 'recipesFor', 'recipesAll', 'chat', 'command', 'rcon']
const expected = ['inventory', 'mine', 'craft_grid', 'smelt', 'check_name', 'report_version', 'submit', 'search']
const tests = []
try {
  const keys = Object.keys(api).sort()
  tests.push({ name: 'method_allowlist', passed: JSON.stringify(keys) === JSON.stringify([...expected].sort()), observed: keys })
  for (const key of forbidden) {
    tests.push({ name: `forbidden_${key}`, passed: !(key in api) && api[key] === undefined })
  }
  let mutationBlocked = false
  try { api.bot = { version: 'forbidden' } } catch { mutationBlocked = true }
  tests.push({ name: 'frozen_interface', passed: Object.isFrozen(api) && mutationBlocked })
  const source = fs.readFileSync(`${root}/harness/src/semantic_agent.mjs`, 'utf8')
  tests.push({
    name: 'no_recipe_registry_calls',
    passed: !source.includes('recipesFor') && !source.includes('recipesAll')
  })
  const report = {
    timestamp: new Date().toISOString(),
    passed: tests.every(test => test.passed),
    tests
  }
  fs.writeFileSync(`${root}/reports/leakage_probe.json`, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 })
  console.log(JSON.stringify(report))
} finally {
  await shutdown()
}
