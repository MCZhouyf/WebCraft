#!/usr/bin/env python3
"""Repeat only dev8, and compare the public compiler against the shared wrapper."""
import json
from collections import defaultdict
import w13_run as run
from w13_runtime import compile_bundle, canonical


def main():
    pin=run.setup('dev_repeat')
    tasks={r['id']:r for r in run.jsonl(run.ROOT/'data/tasks.jsonl') if r['split']=='dev'}
    before=list(run.jsonl(run.OUT/'dev/episodes.jsonl'))
    normalizer,_=run.p6._compiler(run.ROOT)
    worker=run.Worker('dev_repeat',pin)
    cache={}
    rows=[]
    try:
        for n,old in enumerate(before):
            cond=old['condition']
            if cond not in cache:
                cache[cond]=run.p6.load_claims_cache(str(run.ROOT/'corpus/indexes/formal_p1'/cond),
                                                    root=run.ROOT,pin_path=pin,reading_mode='all_public')
            task=tasks[old['task_id']]
            bundle=run.p6.build_evidence(task,cond,index_dir=str(run.ROOT/'corpus/indexes/formal_p1'),
                                        root=run.ROOT,pin_path=pin,claims_cache=cache[cond],fallback=None,
                                        fallback_dir=run.OUT/'forbidden_fallback',top_k=10)
            d1,o1=compile_bundle(run.public_bundle(bundle),normalizer)
            d2,o2=run.p6._claims(bundle,normalizer=normalizer)
            compiler_equal=([o.__dict__ for o in o1]==[o.__dict__ for o in o2] and
                            [(d.mechanism_id,d.value,dict(d.hit)) for d in d1]==
                            [(d.mechanism_id,d.value,dict(d.hit)) for d in d2])
            if not compiler_equal:
                raise RuntimeError('SAFETY_STOP: shared compiler projection is not identical')
            new=worker.run(task,bundle,old['world'],'all_public',cond,'dev_repeat',f'{n:03}')
            rows.append(dict(task_id=old['task_id'],condition=cond,world=old['world'],
                             compiler_identical=int(compiler_equal),
                             action_identical=int(run.actions(old)==run.actions(new)),
                             intent_inclusive_identical=int(run.actions(old,True)==run.actions(new,True)),
                             belief_identical=int(canonical(old['temporal_belief'])==canonical(new['temporal_belief']))))
            print(f'dev repeat {n+1}/{len(before)}',flush=True)
    finally:
        worker.close()
    run.csvout('dev/determinism_compiler_validation.csv',rows)
    freeze=json.loads((run.OUT/'CODE_FREEZE.json').read_text())
    freeze['dev_repeat_episodes']=len(rows)
    freeze['compiler_identical_episodes']=sum(r['compiler_identical'] for r in rows)
    freeze['byte_action_identical_dev_repeats']=sum(r['action_identical'] for r in rows)
    freeze['dev_repeat_validation_source_sha256']=run.sha(run.ROOT/'scripts/w13_dev_validate.py')
    (run.OUT/'CODE_FREEZE.json').write_text(json.dumps(freeze,indent=2)+'\n')


if __name__=='__main__':
    main()
