#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path('/root/autodl-tmp/webcraft')
backup=ROOT/'truth/stage0_snapshot'
backup.mkdir(exist_ok=True)
changes=[]
for v in ('1.16.5','1.19','1.21.11'):
    source=ROOT/f'truth/{v}.json'
    original=json.loads(source.read_text())
    destination=backup/f'{v}.json'
    if not destination.exists(): destination.write_bytes(source.read_bytes())
    subprocess.run([sys.executable,'-B',str(ROOT/'scripts/extract_truth.py'),v],check=True)
    corrected=json.loads(source.read_text())
    old={r['id']:r for r in original['recipes']}
    count_changes=[{'id':r['id'],'before':old[r['id']]['result']['count'],'after':r['result']['count'],'raw':r['raw']} for r in corrected['recipes'] if old[r['id']]['result']['count']!=r['result']['count']]
    old_drops={d['block']:d for d in original['drops']}
    drop_changes=[{'block':d['block'],'before':old_drops[d['block']]['no_silk_touch_items'],'after':d['no_silk_touch_items'],'semantics':d['set_semantics']} for d in corrected['drops'] if old_drops[d['block']]['no_silk_touch_items']!=d['no_silk_touch_items']]
    changes.append({'version':v,'count_changes':count_changes,'drop_changes':drop_changes,'raw_conditions_retained':True})
(ROOT/'reports/stage1_truth_repairs.json').write_text(json.dumps(changes,indent=2)+'\n')
print(json.dumps([{'version':x['version'],'recipe_count_fixes':len(x['count_changes']),'drop_possible_set_fixes':len(x['drop_changes'])} for x in changes]))
