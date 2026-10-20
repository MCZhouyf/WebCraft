#!/usr/bin/env python3
"""Recompute B4 public episode results from immutable semantic-action logs."""
import json
from collections import Counter
from pathlib import Path


def load_events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def replay_episode(log_path, task, plan_version, world='1.21.11'):
    events = load_events(log_path)
    if any(event.get('schema_version') != 2 for event in events):
        raise ValueError('B4 replay requires schema-v2 events')
    inventory_events = [event for event in events if event['method'] == 'inventory']
    if len(inventory_events) != 2:
        raise ValueError(f'expected exactly two inventory snapshots, found {len(inventory_events)}')
    final = Counter()
    for item in inventory_events[-1]['result']:
        final[item['name']] += item['count']
    plan = task['oracle_plans'][plan_version]
    reported_feasible = plan is not None
    expected_feasible = task['feasible'][world]
    produced_goal = final[task['target']['item']] >= task['target']['count']
    success = produced_goal if expected_feasible else not reported_feasible
    action_events = [event for event in events
                     if event['method'] not in {'inventory', 'submit'}]
    execution_failure = next(({
        'actionIndex': index, 'method': event['method'],
        'error': event.get('result', {}).get('error', 'action_failed')
    } for index, event in enumerate(action_events) if not event.get('success', False)), None)
    return {
        'reported_feasible': reported_feasible,
        'produced_goal': produced_goal,
        'success': success,
        'execution_failure': execution_failure,
        'completed_action_records': len(action_events),
        'primary_cost': sum(event['cost']['primary'] for event in action_events),
        'action_methods': [event['method'] for event in action_events],
    }


def compare_online(replayed, online):
    fields = ('reported_feasible', 'produced_goal', 'success', 'execution_failure',
              'completed_action_records', 'primary_cost')
    differences = {
        field: {'online': online.get(field), 'replayed': replayed.get(field)}
        for field in fields if online.get(field) != replayed.get(field)
    }
    return {'match': not differences, 'differences': differences}
