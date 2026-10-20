#!/usr/bin/env python3
"""Portable entry point for the separately frozen CP-RAG diagnostic row."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'method_code/src'),str(ROOT/'scripts'),str(ROOT),str(ROOT/'repro')]
import runtime
import run_cp_rag_20261016 as cp


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    prior,conflicts,nodes=runtime.setup()
    tasks={r['id']:r for p in (ROOT/'data/tasks.jsonl',ROOT/'data/ext_v2/ext_v2_tasks_20261016.jsonl')
           for r in runtime.read_jsonl(p)}
    standard=json.loads((ROOT/'config/resource_model.json').read_text())['standard_inventory']
    evidence={scope:runtime.load_evidence(scope) for scope in ('main','ext_v2','cds')}
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'cp_rag.jsonl').open('w') as log:
        for n,old in enumerate(runtime.read_jsonl(ROOT/'assets/frozen/cp_rag.jsonl.gz')):
            if args.limit is not None and n>=args.limit:break
            scope=('cds' if old['condition'].startswith('cds_') else
                   'ext_v2' if old['task_id'].startswith('extv2_') else 'main')
            bundle=evidence[scope][(old['reading_mode'],old['condition'],old['task_id'])]
            task=runtime.projected_task(tasks[old['task_id']],standard)
            vectors={mid:r['truth_vector'] for mid,r in conflicts.items()}
            if scope=='cds':
                instance=old['condition'].rsplit('__',1)[0]
                private=json.loads((ROOT/f'evaluator/cds/{instance}.json').read_text())
                for candidate in private['boundary_truth']:
                    vectors[candidate['mechanism_id']]={w:bool(candidate['truth']) for w in ('1.16.5','1.19','1.21.11')}
            job=cp.P6Job(old['condition'],old['task_id'],old['world'],'cp_rag',old['seed'])
            new=cp.run_cp(job,task,bundle,pin=ROOT/'MANIFEST.sha256',prior=prior,
                          vectors=vectors,output=args.output/'runtime')
            log.write(json.dumps(new,sort_keys=True)+'\n')
    runtime.check_imports()


if __name__=='__main__':main()
