import fs from 'node:fs'
import assert from 'node:assert/strict'
import { Rcon } from 'rcon-client'
import { createSemanticAgent } from './semantic_agent.mjs'
import { publicValue, safeError } from './public_contract.mjs'

const root='/root/autodl-tmp/webcraft'
const version=process.argv[2]
const versions=['1.16.5','1.19','1.21.11']
const index=versions.indexOf(version)
if(index<0) throw new Error('unknown target')
const name='wc_reset_'+version.replaceAll('.','')
const secrets=Object.fromEntries(fs.readFileSync(root+'/scripts/.rcon_secrets','utf8').trim().split('\n').map(x=>x.split('=')))
const password=secrets[['RCON_1165','RCON_119','RCON_12111'][index]]
const logPath=`${root}/logs/stages/actions-leakage-${index}.jsonl`
const handle=await createSemanticAgent({minecraftVersion:version,gamePort:25571+index,rconPort:25581+index,rconPassword:password,username:name,arenaPath:root+'/harness/config/arena.json',logPath,answerClaims:async()=>[true,false,'unknown']})
const rcon=await Rcon.connect({host:'127.0.0.1',port:25581+index,password})
const tests=[]
async function test(name,fn){await fn();tests.push({name,passed:true})}
try{
  const api=handle.api
  await test('exact_8_method_allowlist',()=>assert.deepEqual(Object.keys(api).sort(),['inventory','mine','craft_grid','smelt','check_name','report_version','submit','search'].sort()))
  for(const key of ['bot','version','registry','recipesFor','recipesAll','chat','command','rcon','answer_claims']) await test('blocked_'+key,()=>assert.equal(api[key],undefined))
  await test('frozen_api',()=>assert.ok(Object.isFrozen(api)))
  await test('no_recipe_registry_calls',()=>assert.doesNotMatch(fs.readFileSync(root+'/harness/src/semantic_agent.mjs','utf8'),/recipesFor|recipesAll/))
  for(const key of ['x','y','z','position','coordinates','height','world_boundary','dimension_height','time_ticks','entity_id']) await test('redacts_'+key,()=>assert.deepEqual(publicValue({[key]:-60}),{}))
  await test('errors_are_allowlisted',()=>assert.equal(safeError(new Error('position (1, -60, 4) entity_id=7')), 'action could not be completed'))
  const position=await rcon.send(`data get entity ${name} Pos`)
  await test('identical_absolute_spawn',()=>assert.match(position,/\[0\.5d, 4\.0d, 0\.5d\]/))
  await test('evaluation_rejected_mid_episode',()=>assert.rejects(handle.evaluator.answer_claims([1,2,3]),/finished episode/))
  const result=await api.check_name('height=-60 1.19')
  await test('invalid_input_safe_error',()=>assert.equal(result.error,'invalid item identifier'))
  await test('failed_name_cost_one',()=>assert.equal(result.cost.primary,1))
  await api.report_version('1.19')
  await api.inventory()
  handle.evaluator.finish_episode()
  await test('evaluation_returns_agent_answers',async()=>assert.deepEqual(await handle.evaluator.answer_claims([1,2,3]),[true,false,'unknown']))
  await test('actions_rejected_after_end',()=>assert.rejects(api.inventory(),/episode closed/))
  const logs=fs.readFileSync(logPath,'utf8')
  await test('live_log_no_world_leaks',()=>assert.doesNotMatch(logs,/\b1\.(?:16\.5|19|21\.11)\b|"(?:x|y|z|position|entity_id|time_ticks)"\s*:/))
  fs.writeFileSync(`${root}/reports/stage1_leakage_${version}.json`,JSON.stringify({version,tests,passed:true,checks:tests.length},null,2)+'\n')
  console.log(JSON.stringify({version,checks:tests.length,passed:true}))
}finally{rcon.end();await handle.shutdown()}
