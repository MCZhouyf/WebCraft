import assert from 'node:assert/strict'
import fs from 'node:fs'
import { publicValue, primaryCost, itemCounts, itemFlow, safeError, evaluationGate } from './public_contract.mjs'

const tests = []
async function test(name, run) { await run(); tests.push({name, passed:true}) }
await test('coordinate_fields_removed', () => assert.deepEqual(publicValue({x:4,y:-60,z:0,position:{x:1},coordinates:[1,2,3],height:384,world_boundary:30000,dimension_height:384,time_ticks:24000,entity_id:2,nodeId:'iron_1'}), {nodeId:'iron_1'}))
await test('coordinate_strings_redacted', () => assert.equal(publicValue('y=-60 (1, 4, 3) entity_id=123'), '[redacted] [redacted] [redacted]'))
await test('version_and_internal_metadata_redacted', () => assert.equal(publicValue('1.19; protocol 759'), '[redacted]; [redacted]'))
await test('relative_distance_allowed', () => assert.deepEqual(publicValue({net_displacement:3,wall_ms:250,nodeId:'log_1'}), {net_displacement:3,wall_ms:250,nodeId:'log_1'}))
await test('raw_errors_never_returned', () => assert.equal(safeError(new Error('world y=-60 protocol 759 entity 56')), 'action could not be completed'))
await test('stable_cost_all_methods', () => {
  for (const m of ['mine','craft_grid','check_name']) assert.equal(primaryCost(m,{}),1)
  for (const m of ['inventory','report_version','submit','search']) assert.equal(primaryCost(m,{}),0)
  assert.equal(primaryCost('smelt',{n:3}),3)
  assert.equal(primaryCost('smelt',{n:-1}),1)
})
await test('stack_counts_are_summed', () => assert.deepEqual(itemCounts([{name:'oak_log',count:64},{name:'oak_log',count:2}]), {oak_log:66}))
await test('flow_tracks_consumption_and_output', () => assert.deepEqual(itemFlow({oak_log:1}, {oak_planks:4}), {consumed:[{name:'oak_log',count:1}],produced:[{name:'oak_planks',count:4}]}))
await test('IAA_forbidden_during_episode', async () => {
  const g=evaluationGate(async () => [true,false,'unknown'])
  await assert.rejects(g.answer_claims([1,2,3]), /finished episode/)
  g.finish_episode()
  assert.deepEqual(await g.answer_claims([1,2,3]), [true,false,'unknown'])
})
await test('IAA_cannot_be_filled_by_evaluator_truth', async () => {
  const g=evaluationGate(); g.finish_episode()
  await assert.rejects(g.answer_claims([1]), /not configured/)
})
const report={tests,passed:true}
fs.writeFileSync(new URL('../../reports/stage1_contract_tests.json',import.meta.url), JSON.stringify(report,null,2)+'\n')
console.log(JSON.stringify(report))
