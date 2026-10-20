#!/usr/bin/env python3
"""Enumerate candidates; do not claim oracle grades before resource approval."""
import csv
import io
import json
import random
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from conflict_core import VERSIONS, compact, item_id, recipe_assertion, assertion_id, involved, accepts_layout

ROOT=Path('/root/autodl-tmp/webcraft')
TRUTH={v:json.loads((ROOT/f'truth/{v}.json').read_text()) for v in VERSIONS}
NAMES={v:set(map(item_id,TRUTH[v]['names']['items'])) for v in VERSIONS}
COMMON=set.intersection(*NAMES.values())
ASSERTIONS={}
RECIPES={v:[] for v in VERSIONS}
SOURCE={}
KNOWN_RENAMES=[('grass_path','dirt_path'),('grass','short_grass'),('scute','turtle_scute'),('chain','iron_chain')]

def add(assertion, version, source=None):
    key=compact(assertion)
    ASSERTIONS.setdefault(key,set()).add(version)
    if source: SOURCE.setdefault(key,{})[version]=source

for version in VERSIONS:
    for name in NAMES[version]: add({'kind':'name','item':name,'exists':True},version)
    for name in TRUTH[version]['craftability']: add({'kind':'craftability','item':item_id(name),'exists':True},version)
    for record in TRUTH[version]['recipes']:
        normalized=recipe_assertion(record)
        if normalized:
            add(normalized,version,record['id'])
            RECIPES[version].append(normalized)

def projection(assertion):
    a=json.loads(compact(assertion))
    if 'grid' in a: a['grid']=[[[x for x in cell if x in COMMON] for cell in row] for row in a['grid']]
    if 'ingredients' in a: a['ingredients']=[[x for x in options if x in COMMON] for options in a['ingredients']]
    return compact(a)

projected={v:{projection(a) for a in RECIPES[v]} for v in VERSIONS}
outputs={v:defaultdict(list) for v in VERSIONS}
for v in VERSIONS:
    for a in RECIPES[v]: outputs[v][a['output']].append(a)

# A full loot interpreter is needed for conditional blocks; retain raw trees as evidence.
drop_review=[]
raw_iron={}
for version in VERSIONS:
    with zipfile.ZipFile(ROOT/f'servers/{version}/server.jar') as outer:
        inner=f'META-INF/versions/{version}/server-{version}.jar'
        archive=zipfile.ZipFile(io.BytesIO(outer.read(inner))) if inner in outer.namelist() else outer
        prefix='data/minecraft/loot_table/blocks/' if version=='1.21.11' else 'data/minecraft/loot_tables/blocks/'
        for block in ['gravel','iron_ore','gold_ore']:
            raw=json.loads(archive.read(prefix+block+'.json'))
            if block=='gravel': drop_review.append({'version':version,'block':block,'source':prefix+block+'.json','raw':raw,'legacy_no_silk':next(d['no_silk_touch_items'] for d in TRUTH[version]['drops'] if d['block']=='minecraft:gravel')})
            else:
                raw_iron[(version,block)]=raw
                # These two checked tables use the sole silk/no-silk alternatives.
                old=block
                normal=block if version=='1.16.5' else 'raw_'+block.removesuffix('_ore')
                for silk,item in [(False,normal),(True,old)]:
                    add({'kind':'drop','block':block,'silk_touch':silk,'drops':[item]},version,prefix+block+'.json')
        if archive is not outer: archive.close()

rename_report=[]
for old,new in KNOWN_RENAMES:
    old_vec=[old in NAMES[v] for v in VERSIONS]
    new_vec=[new in NAMES[v] for v in VERSIONS]
    signatures={}
    for name in [old,new]:
        signatures[name]=set()
        for v in VERSIONS:
            for recipe in RECIPES[v]:
                if name in involved(recipe):
                    signatures[name].add(compact(recipe).replace('"'+name+'"','"$RENAMED"'))
    overlap=bool(signatures[old]&signatures[new])
    rename_report.append({'old':old,'new':new,'old_vector':old_vec,'new_vector':new_vec,'same_recipe_or_usage_signature':overlap,'status':'structurally_confirmed' if overlap else 'known_list_candidate_requires_use_evidence'})

rename_items={item for pair in KNOWN_RENAMES for item in pair}
rows=[]
for key,present in sorted(ASSERTIONS.items()):
    if len(present)==3: continue
    a=json.loads(key)
    vector=[v in present for v in VERSIONS]
    subtype=None
    if a['kind']=='recipe':
        if all(projection(a) in projected[v] for v in VERSIONS): subtype='tag_expansion'
        alternatives=[r for v in VERSIONS for r in outputs[v].get(a['output'],[])]
        without_count=lambda x:compact({k:val for k,val in x.items() if k!='count'})
        quantity=any(without_count(r)==without_count(a) and r['count']!=a['count'] for r in alternatives)
        main='quantity' if quantity else ('replacement' if all(outputs[v].get(a['output']) for v in VERSIONS) else ('removed' if vector[0] and not vector[-1] else 'new_route'))
    elif a['kind']=='name': main='renamed' if a['item'] in rename_items else ('removed' if vector[0] and not vector[-1] else 'new_route')
    elif a['kind']=='craftability': main='feasibility_change'
    else: main='replacement'
    grade='A' if a['kind']=='name' else None
    probe={'method':'check_name','params':{'name':a['item']}} if a['kind']=='name' else None
    rows.append({'id':assertion_id(a),'main_type':main,'subtype':subtype,'assertion':a,'truth_vector':dict(zip(VERSIONS,vector)),'grade':grade,'grade_status':'name_probe_primary_cost_1' if grade else 'PENDING_RESOURCE_MODEL_AND_ORACLE','probe':probe,'acquisition_cost':dict.fromkeys(VERSIONS,0 if grade else None),'involved_items':involved(a),'source':SOURCE.get(key,{}),'task_relevant':False})

separator=json.loads((ROOT/'truth/separator_truth.json').read_text())
layouts={
2:['cobbled_deepslate']*3+[None,'stick',None,None,'stick',None],
5:['string',None,None,'leather',None,None,None,None,None],
6:['chiseled_stone_bricks']*4+['iron_ingot']+['chiseled_stone_bricks']*4,
7:['chiseled_stone_bricks']*4+['netherite_ingot']+['chiseled_stone_bricks']*4,
8:[None,'leather',None,'leather','iron_ingot','leather',None,None,None],
9:['copper_ingot']*3+[None,'stick',None,None,'stick',None],
12:['paper','iron_nugget',None,None,None,None,None,None,None]}
names={3:'scute',4:'turtle_scute',10:'chain',11:'iron_chain'}
sep_rows=[]
for index in range(1,13):
    if index in layouts:
        output={2:'stone_pickaxe',5:'bundle',6:'lodestone',7:'lodestone',8:'saddle',9:'copper_pickaxe',12:'name_tag'}[index]
        assertion={'kind':'concrete_grid','output':output,'layout':layouts[index]}
        actual=[any(r['output']==output and r['station']=='crafting_grid' and accepts_layout(r,layouts[index]) for r in RECIPES[v]) for v in VERSIONS]
        probe={'method':'craft_grid','params':{'layout':layouts[index]}}
    elif index in names:
        assertion={'kind':'name','item':names[index],'exists':True}
        actual=[names[index] in NAMES[v] for v in VERSIONS]
        probe={'method':'check_name','params':{'name':names[index]}}
    else:
        assertion={'kind':'drop','block':'iron_ore','silk_touch':False,'drops':['raw_iron']}
        actual=[v!='1.16.5' for v in VERSIONS]
        probe={'method':'mine','params':{'nodeId':'iron_1'}}
    expected=[separator['versions'][v][index-1]['jar'] for v in VERSIONS]
    assert actual==expected,(index,actual,expected)
    grade='C' if index in [2,7,9] else ('A' if index in names else None)
    record={'separator_id':index,'id':assertion_id(assertion),'assertion':assertion,'truth_vector':dict(zip(VERSIONS,actual)),'is_version_conflict':len(set(actual))>1,'grade':grade,'grade_reason':{2:'No cobbled deepslate or source block in common arena',7:'No netherite route from arena resources',9:'No copper source in common arena'}.get(index,'name probe cost 1' if index in names else 'pending bounded-resource oracle'),'probe':probe}
    sep_rows.append(record)
    if not any(r['id']==record['id'] for r in rows):
        rows.append({**record,'main_type':'invariant_false_control' if index==12 else 'replacement','subtype':None,'involved_items':sorted(set(x for x in layouts.get(index,[]) if x)|{assertion.get('output',assertion.get('block',''))}) if index in layouts else involved(assertion),'acquisition_cost':dict.fromkeys(VERSIONS,None),'task_relevant':False})
    elif grade=='C':
        next(r for r in rows if r['id']==record['id']).update(grade='C',grade_status=record['grade_reason'])

draft=ROOT/'data/drafts'; draft.mkdir(parents=True,exist_ok=True)
with (draft/'conflicts.jsonl').open('w') as handle:
    for row in rows: handle.write(compact(row)+'\n')
report={'status':'DRAFT_NOT_PREREGISTERED','worlds':VERSIONS,'candidate_count':len(rows),'main_types':dict(Counter(r['main_type'] for r in rows)),'tag_expansion_count':sum(r.get('subtype')=='tag_expansion' for r in rows),'grades':dict(Counter(str(r.get('grade')) for r in rows)),'separators':sep_rows,'renames':rename_report,'limitations':['General conditional loot is not normalized as unconditional truth. Only checked iron/gold tables are included.','Recipe set assertions retain expanded alternative ingredients; tag-only changes are excluded from task relevance.','Feasibility grades and minimum costs pending finite resource model.','Invariant-false separator 12 is retained as a control and excluded from version-conflict statistics.']}
(ROOT/'reports/stage1_conflict_inventory.json').write_text(json.dumps(report,indent=2)+'\n')
(ROOT/'reports/stage0_loot_counterexample.json').write_text(json.dumps(drop_review,indent=2)+'\n')
sample=random.Random(20261004).sample(rows,min(30,len(rows)))
with (ROOT/'reports/conflict_sample_for_review.csv').open('w',newline='') as handle:
    writer=csv.DictWriter(handle,fieldnames=['id','wiki_page_candidate','main_type','grade','assertion','truth_vector','review_status'],lineterminator='\n')
    writer.writeheader()
    for row in sample:
        a=row['assertion']; subject=a.get('output',a.get('item',a.get('block','Unknown')))
        writer.writerow({'id':row['id'],'wiki_page_candidate':subject.replace('_',' ').title(),'main_type':row['main_type'],'grade':row.get('grade'),'assertion':compact(a),'truth_vector':compact(row['truth_vector']),'review_status':'UNREVIEWED; title not API-resolved'})
print(json.dumps({k:report[k] for k in ['status','candidate_count','main_types','tag_expansion_count','grades']}))
