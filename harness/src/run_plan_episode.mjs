import fs from 'node:fs'
import path from 'node:path'
import { createSemanticAgent } from './semantic_agent.mjs'
import { resetUsername } from './arena_identity.mjs'

const root = '/root/autodl-tmp/webcraft'
const [world, taskId, planVersion, logPath, diagnosticPath] = process.argv.slice(2)
const versions = ['1.16.5', '1.19', '1.21.11']
const index = versions.indexOf(world)
if (index < 0 || !versions.includes(planVersion)) throw new Error('invalid world or plan version')

const tasks = fs.readFileSync(`${root}/data/tasks.jsonl`, 'utf8')
  .trim().split('\n').map(line => JSON.parse(line))
const task = tasks.find(row => row.id === taskId)
if (!task) throw new Error(`unknown task ${taskId}`)
const arena = JSON.parse(fs.readFileSync(`${root}/harness/config/arena.json`, 'utf8'))
const secrets = Object.fromEntries(fs.readFileSync(`${root}/scripts/.rcon_secrets`, 'utf8')
  .trim().split('\n').map(line => line.split('=', 2)))
const password = secrets[['RCON_1165', 'RCON_119', 'RCON_12111'][index]]
// Reuse the reset identity so the episode inherits the reset inventory and spawn.
const username = resetUsername(world)
fs.mkdirSync(path.dirname(logPath), { recursive: true })
if (fs.existsSync(logPath)) throw new Error(`episode log already exists: ${logPath}`)
fs.writeFileSync(logPath, '', { mode: 0o600, flag: 'wx' })

const handle = await createSemanticAgent({
  minecraftVersion: world,
  gamePort: 25571 + index,
  rconPort: 25581 + index,
  rconPassword: password,
  username,
  arenaPath: `${root}/harness/config/arena.json`,
  logPath,
  diagnosticPath
})

function nodeId(action) {
  const nodes = arena.resources.filter(row => row.block === action.node_type)
  const node = nodes[action.node_ordinal - 1]
  if (!node) throw new Error(`missing arena node ${action.node_type}#${action.node_ordinal}`)
  return node.nodeId
}

const started = performance.now()
const actionResults = []
let result
try {
  const initialInventory = await handle.api.inventory()
  const plan = task.oracle_plans[planVersion]
  let executionFailure = null
  if (plan) {
    for (let actionIndex = 0; actionIndex < plan.actions.length; actionIndex += 1) {
      const action = plan.actions[actionIndex]
      let response
      if (action.method === 'mine') response = await handle.api.mine(nodeId(action))
      else if (action.method === 'craft_grid') response = await handle.api.craft_grid(action.layout)
      else if (action.method === 'smelt') response = await handle.api.smelt(action.input, action.fuel, action.n)
      else throw new Error(`unsupported plan method ${action.method}`)
      actionResults.push({ actionIndex, method: action.method, response })
      if (response?.success === false) {
        executionFailure = { actionIndex, method: action.method, error: response.error ?? 'action_failed' }
        break
      }
    }
  }
  const finalInventory = await handle.api.inventory()
  const targetCount = finalInventory
    .filter(item => item.name === task.target.item)
    .reduce((total, item) => total + item.count, 0)
  const producedGoal = targetCount >= task.target.count
  const reportedFeasible = Boolean(plan)
  const expectedFeasible = task.feasible[world]
  const success = expectedFeasible ? producedGoal : !reportedFeasible
  await handle.api.submit({ feasible: reportedFeasible, success })
  const primaryCost = actionResults.reduce((total, row) => total + (row.response?.cost?.primary ?? 0), 0)
  result = {
    task_id: taskId,
    task_type: task.type,
    split: task.split,
    target: task.target,
    world,
    plan_version: planVersion,
    expected_feasible: expectedFeasible,
    reported_feasible: reportedFeasible,
    produced_goal: producedGoal,
    success,
    execution_failure: executionFailure,
    planned_action_records: plan?.actions.length ?? 0,
    completed_action_records: actionResults.length,
    primary_cost: primaryCost,
    oracle_cost_current_world: task.oracle_cost[world],
    elapsed_seconds: Number(((performance.now() - started) / 1000).toFixed(3)),
    initial_inventory: initialInventory,
    final_inventory: finalInventory
  }
  console.log(JSON.stringify(result))
} finally {
  await handle.shutdown()
}
