import fs from 'node:fs'
import assert from 'node:assert/strict'
import { Rcon } from 'rcon-client'
import { createSemanticAgent } from './semantic_agent.mjs'

const root='/root/autodl-tmp/webcraft', version=process.argv[2]
const index=['1.16.5','1.19','1.21.11'].indexOf(version)
if(index<0) throw new Error('unknown target')
const username='wc_cost_probe'
const secrets=Object.fromEntries(fs.readFileSync(root+'/scripts/.rcon_secrets','utf8').trim().split('\n').map(x=>x.split('=')))
const password=secrets[['RCON_1165','RCON_119','RCON_12111'][index]]
const rcon=await Rcon.connect({host:'127.0.0.1',port:25581+index,password})
const handle=await createSemanticAgent({minecraftVersion:version,gamePort:25571+index,rconPort:25581+index,rconPassword:password,username,arenaPath:root+'/harness/config/arena.json',logPath:`${root}/logs/stages/actions-cost-${index}.jsonl`})
try{
  // Evaluator-only fixture. These supplies are not part of any experiment task.
  for(const command of [`tp ${username} 0.5 4 0.5`,`clear ${username}`,`give ${username} iron_ore 2`,`give ${username} coal 1`,`give ${username} paper 1`,`give ${username} iron_nugget 1`]) await rcon.send(command)
  await new Promise(resolve=>setTimeout(resolve,300))
  const smelt=await handle.api.smelt('iron_ore','coal',2)
  assert.equal(smelt.success,true); assert.equal(smelt.cost.primary,2)
  assert.deepEqual(smelt.items,[{name:'iron_ingot',count:2}])
  const bad=await handle.api.craft_grid(['paper','iron_nugget',null,null,null,null,null,null,null])
  assert.equal(bad.success,false); assert.equal(bad.cost.primary,1)
  const inventory=await handle.api.inventory()
  assert.equal(inventory.find(i=>i.name==='paper')?.count,1)
  assert.equal(inventory.find(i=>i.name==='iron_nugget')?.count,1)
  const report={version,passed:true,smelt,invalid_recipe:bad,inventory,fixture:'evaluator-only materials, not experimental task package'}
  fs.writeFileSync(`${root}/reports/stage1_cost_${version}.json`,JSON.stringify(report,null,2)+'\n')
  console.log(JSON.stringify({version,passed:true,smelt_primary:smelt.cost.primary,produced:smelt.items,failed_craft_primary:bad.cost.primary}))
}finally{rcon.end();await handle.shutdown()}
