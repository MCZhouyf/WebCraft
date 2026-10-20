#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path('/root/autodl-tmp/webcraft')
commands=[
    [sys.executable,'-B','-m','unittest','metrics.test_episode','-v'],
    [sys.executable,'-B','scripts/test_truth_repairs.py','-v'],
    [str(ROOT/'env/node/bin/node'),'harness/src/test_public_contract.mjs']
]
checks=[]
for command in commands:
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    checks.append({'command':command,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
patterns={'versions':re.compile(r'\b1\.(?:16\.5|19|21\.11)\b'),'world_keys':re.compile(r'"(?:x|y|z|position|coordinates?|height|world_boundary|dimension_height|time_ticks|entity_id|protocol|registry)"\s*:'),'coordinate_strings':re.compile(r'\b(?:x|y|z|height|entity_id)\s*[:=]\s*-?\d')}
files=[]
for path in sorted(ROOT.glob('logs/**/actions-*.jsonl')):
    text=path.read_text()
    files.append({'path':str(path.relative_to(ROOT)),'records':len(text.splitlines()),'matches':{key:len(p.findall(text)) for key,p in patterns.items()}})
scan={'files':files,'passed':not any(any(r['matches'].values()) for r in files),'old_logs_rewritten':False}
(ROOT/'reports/stage1_log_leak_scan.json').write_text(json.dumps(scan,indent=2)+'\n')
result={'timestamp':datetime.now(timezone.utc).isoformat(),'tests':checks,'action_log_files':len(files),'action_records':sum(r['records'] for r in files),'log_scan_passed':scan['passed'],'passed':all(r['exit_code']==0 for r in checks) and scan['passed'],'scope':'Available implementation tests only; not full Stage 1 acceptance'}
(ROOT/'reports/stage1_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='tests'}))
raise SystemExit(0 if result['passed'] else 1)
