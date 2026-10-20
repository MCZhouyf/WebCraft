#!/usr/bin/env python3
"""Conservative 2-D arena walkability checks before and after oracle mining."""
import json
from collections import deque
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')


def bfs(arena, removed):
    bounds = arena['bounds']
    blocked = {(row['x'], row['z']) for row in arena['resources']
               if row['nodeId'] not in removed}
    blocked |= {(arena['workbench']['x'], arena['workbench']['z']),
                (arena['furnace']['x'], arena['furnace']['z'])}
    start = (int(arena['spawn']['x']), int(arena['spawn']['z']))
    seen = {start}
    pending = deque([start])
    while pending:
        x, z = pending.popleft()
        for nxt in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if not (bounds['min']['x'] <= nxt[0] <= bounds['max']['x']
                    and bounds['min']['z'] <= nxt[1] <= bounds['max']['z']):
                continue
            if nxt in blocked or nxt in seen:
                continue
            seen.add(nxt)
            pending.append(nxt)
    return seen


def reachable_near(seen, point, radius):
    return any((x - point['x']) ** 2 + (z - point['z']) ** 2 <= radius ** 2
               for x, z in seen)


def node_id(arena, action):
    rows = [row for row in arena['resources'] if row['block'] == action['node_type']]
    return rows[action['node_ordinal'] - 1]['nodeId']


def state_result(arena, removed):
    seen = bfs(arena, removed)
    targets = {
        'workbench': reachable_near(seen, arena['workbench'], 3),
        'furnace': reachable_near(seen, arena['furnace'], 3),
    }
    for row in arena['resources']:
        targets[row['nodeId']] = reachable_near(seen, row, 1)
    return {
        'reachable_standing_cells': len(seen),
        'target_count': len(targets),
        'unreachable_targets': sorted(key for key, value in targets.items() if not value),
        'all_targets_reachable': all(targets.values()),
    }


def main():
    arena = json.loads((ROOT / 'harness/config/arena.json').read_text())
    tasks = [json.loads(line) for line in (ROOT / 'data/tasks.jsonl').read_text().splitlines()]
    output = {'method': ('Conservative four-neighbor BFS on the y=4 floor. Resource, '
                         'workbench, and furnace cells are obstacles; a target is reachable '
                         'when a standing cell satisfies its frozen GoalNear radius.'),
              'versions': {}}
    initial = state_result(arena, set())
    for version in VERSIONS:
        after = []
        for task in tasks:
            plan = task['oracle_plans'][version]
            removed = set()
            if plan:
                removed = {node_id(arena, action) for action in plan['actions']
                           if action['method'] == 'mine'}
            after.append({'task_id': task['id'], 'removed_nodes': sorted(removed),
                          **state_result(arena, removed)})
        output['versions'][version] = {'initial': initial, 'after_each_oracle_plan': after}
    path = ROOT / 'reports/d1_static_reachability.json'
    path.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({version: {
        'initial_unreachable': len(rows['initial']['unreachable_targets']),
        'post_plan_states': len(rows['after_each_oracle_plan']),
        'states_with_unreachable_targets': sum(
            not state['all_targets_reachable'] for state in rows['after_each_oracle_plan'])
    } for version, rows in output['versions'].items()}))


if __name__ == '__main__':
    main()
