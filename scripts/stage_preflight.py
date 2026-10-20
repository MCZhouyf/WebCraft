#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')

def snapshot(planned_gib=4):
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) * 1024
    cgroup = Path('/sys/fs/cgroup')
    limit = (cgroup / 'memory.max').read_text().strip()
    if limit != 'max':
        available = min(available, int(limit) - int((cgroup / 'memory.current').read_text()))
    disk = shutil.disk_usage(ROOT).free
    p = subprocess.run(['ps','-eo','pid,ppid,pcpu,rss,args'], text=True, capture_output=True, check=True)
    processes = [line for line in p.stdout.splitlines() if any(s in line for s in ('run_p4g_training.py','p4g_supervisor.py','p4g_guardian.py'))]
    gpu = subprocess.run(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'], text=True, capture_output=True, check=True).stdout.strip()
    record = {'timestamp': datetime.now(timezone.utc).isoformat(), 'available_gib': round(available/2**30,3), 'free_disk_gib': round(disk/2**30,3), 'planned_gib': planned_gib, 'capcon_processes': processes, 'gpu_processes': gpu, 'cuda_disabled': os.environ.get('CUDA_VISIBLE_DEVICES') == ''}
    record['resource_pass'] = available >= (planned_gib+8)*2**30 and disk >= 3*2**30
    safe_processes = subprocess.run(
        ['ps', '-eo', 'pid,ppid,ni,pcpu,rss,comm'],
        text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    record['all_process_snapshot'] = safe_processes
    record['protection_policy'] = 'Every process not launched by this task is protected; no working directories inspected.'
    return record

if __name__ == '__main__':
    result = snapshot()
    (ROOT/'logs/stages').mkdir(exist_ok=True)
    with (ROOT/'logs/stages/preflight.jsonl').open('a') as handle:
        handle.write(json.dumps(result)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'all_process_snapshot'}))
    if not result['resource_pass'] or not result['cuda_disabled']:
        raise SystemExit('STOP: resource or CUDA constraint')
