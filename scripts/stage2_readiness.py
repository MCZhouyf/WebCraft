#!/usr/bin/env python3
"""Offline prerequisites only. Does not send requests or expose API configuration values."""
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

ROOT=Path('/root/autodl-tmp/webcraft')
VERSIONS=['1.16.5','1.19','1.21.11']
corpus=ROOT/'corpus'; corpus.mkdir(exist_ok=True)
times={}
for v in VERSIONS:
    record=json.loads((ROOT/f'servers/{v}/version.json').read_text())
    release=datetime.fromisoformat(record['releaseTime'].replace('Z','+00:00'))
    times[v]={'releaseTime':record['releaseTime'],'corpus_cutoff':(release+timedelta(days=30)).isoformat(),'source':f'servers/{v}/version.json'}
(corpus/'release_times.json').write_text(json.dumps(times,indent=2)+'\n')
required=['WC_LLM_MODEL','WC_LLM_API_KEY','WC_LLM_BUDGET_USD','WC_RESEARCH_CONTACT']
status={key:bool(os.getenv(key)) for key in required}
record={'stage':'2','status':'BLOCKED_MISSING_PREREQUISITES','environment_present':status,'requests_sent':0,'llm_calls':0,'tokens':0,'cost_usd':0,'robots_checked':False,'policy':'Do not request site pages before contact-bearing User-Agent and robots/API review. Do not make billed requests without budget and model pricing.','missing':[key for key,present in status.items() if not present]}
(ROOT/'reports/stage2_readiness.json').write_text(json.dumps(record,indent=2)+'\n')
inventory=json.loads((ROOT/'reports/stage1_conflict_inventory.json').read_text())
items=set()
for row in inventory['separators']:
    a=row['assertion']
    items.update(a.get('layout',[]))
    items.update(a.get('drops',[]))
    for key in ['output','item','block']:
        if key in a: items.add(a[key])
items.discard(None)
with (ROOT/'reports/unmapped_items.csv').open('w',newline='') as handle:
    writer=csv.writer(handle,lineterminator='\n'); writer.writerow(['item_id','candidate_page_title','status','reason'])
    for item in sorted(items): writer.writerow([item,item.replace('_',' ').title(),'not_attempted','Contact and robots/API prerequisites pending'])
with (ROOT/'reports/extraction_review.csv').open('w',newline='') as handle:
    csv.writer(handle,lineterminator='\n').writerow(['claim_id','source_url','revid','text','automatic_claim','human_label','human_note'])
print(json.dumps(record))
