"""Symmetric boundary readout from public observations, without truth lookup."""
from __future__ import annotations
from arbiter.evaluation.external_scoring import ACTION_METHODS
from arbiter.evaluation.measurement_v3 import result_mismatch


def observation_updates(record, assertions, *, probe_only=False):
    semantic=[e for e in record['events'] if e.get('method') in ACTION_METHODS]
    if len(semantic)!=len(record['used_mechanism_ids']):
        raise ValueError('mechanism/action alignment changed')
    updates={}
    for mechanism,event in zip(record['used_mechanism_ids'],semantic):
        if probe_only and event.get('intent')!='probe':
            continue
        if mechanism not in assertions:
            continue
        result=event.get('result',{})
        error=result.get('error') if isinstance(result,dict) else None
        if error in {'missing ingredient','missing smelting input or fuel',
                     'unknown resource node','resource node is empty',
                     'crafting table unavailable','furnace unavailable'}:
            continue
        if error and event.get('method')!='check_name':
            # Generic execution errors do not establish that a claim is false.
            continue
        if event.get('method')=='check_name' and not isinstance(result.get('resolvable'),bool):
            continue
        updates[mechanism]=not result_mismatch(event,[assertions[mechanism]])
    return updates


def boundary_readout(record, assertions, initial_predictions, *, probe_only=False):
    """Return predictions; callers keep evaluator truth in a separate namespace."""
    result=dict(initial_predictions)
    result.update(observation_updates(record,assertions,probe_only=probe_only))
    return result
