#!/usr/bin/env python3
"""Apply the frozen resource model to the shared arena configuration."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
MODEL = json.loads((ROOT / 'config/resource_model.json').read_text())
ARENA_PATH = ROOT / 'harness/config/arena.json'
ARENA = json.loads(ARENA_PATH.read_text())

counts = MODEL['resource_block_counts']
positions = [
    (x, 4, z)
    for z in range(-8, 9, 2)
    for x in range(10, 31, 2)
]
needed = sum(counts.values())
if needed > len(positions):
    raise SystemExit(f'resource grid has {len(positions)} positions for {needed} nodes')

resources = []
position_index = 0
for block, count in counts.items():
    prefix = {
        'oak_log': 'log', 'cobblestone': 'cobble', 'stone': 'stone',
        'iron_ore': 'iron', 'gold_ore': 'gold', 'coal_ore': 'coal',
        'diamond_ore': 'diamond', 'redstone_ore': 'redstone',
        'lapis_ore': 'lapis', 'sand': 'sand', 'gravel': 'gravel',
    }[block]
    for ordinal in range(1, count + 1):
        x, y, z = positions[position_index]
        position_index += 1
        resources.append({
            'nodeId': f'{prefix}_{ordinal}', 'block': block,
            'x': x, 'y': y, 'z': z,
        })

ARENA['resources'] = resources
ARENA['initialItems'] = MODEL['standard_inventory']

actual = Counter(resource['block'] for resource in resources)
if actual != Counter(counts):
    raise AssertionError((actual, counts))
if len({(row['x'], row['y'], row['z']) for row in resources}) != len(resources):
    raise AssertionError('resource coordinates are not unique')

ARENA_PATH.write_text(json.dumps(ARENA, indent=2) + '\n')
print(json.dumps({
    'resource_nodes': len(resources),
    'resource_counts': dict(actual),
    'initial_items': ARENA['initialItems'],
}))
