#!/usr/bin/env python3
"""Add deterministic interaction and pickup points to the frozen arena."""
import json
from pathlib import Path


ROOT = Path('/root/autodl-tmp/webcraft')
ARENA_PATH = ROOT / 'harness/config/arena.json'


def fixed_station(target, occupied, bounds):
    candidates = (
        (target['x'], target['y'], target['z'] - 1),
        (target['x'], target['y'], target['z'] + 1),
        (target['x'] - 1, target['y'], target['z']),
        (target['x'] + 1, target['y'], target['z']),
    )
    for x, y, z in candidates:
        inside = (bounds['min']['x'] <= x <= bounds['max']['x']
                  and bounds['min']['z'] <= z <= bounds['max']['z'])
        if inside and (x, y, z) not in occupied:
            return {'x': x + 0.5, 'y': y, 'z': z + 0.5}
    raise ValueError(f'no fixed station for {target}')


def main():
    arena = json.loads(ARENA_PATH.read_text())
    targets = [arena['workbench'], arena['furnace'], *arena['resources']]
    occupied = {(row['x'], row['y'], row['z']) for row in targets}
    for target in targets:
        target['station'] = fixed_station(target, occupied, arena['bounds'])
    for resource in arena['resources']:
        resource['pickup'] = {
            'x': resource['x'] + 0.5,
            'y': resource['y'],
            'z': resource['z'] + 0.5,
        }
    ARENA_PATH.write_text(json.dumps(arena, indent=2) + '\n')


if __name__ == '__main__':
    main()
