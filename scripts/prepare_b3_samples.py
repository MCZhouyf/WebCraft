#!/usr/bin/env python3
"""Create deterministic, executable B3 truth-audit samples before live probing."""
import json
import random
from pathlib import Path

from recipe_type_policy import dry_run_params, normalize_recipe_type

ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')
SEED = 20261005


def deterministic_drop(row):
    expected = row.get('no_silk_touch_items', [])
    possibilities = row.get('possibilities', [])
    if len(expected) != 1 or not possibilities:
        return False
    for possibility in possibilities:
        conditions = possibility.get('conditions', [])
        if any(condition.get('condition') != 'minecraft:survives_explosion'
               for condition in conditions):
            return False
        if possibility.get('functions'):
            return False
        if possibility.get('item') not in expected:
            return False
    return True


def recipe_record(recipe):
    try:
        invocation = dry_run_params(recipe)
    except (AssertionError, KeyError, TypeError, ValueError):
        return None
    return {
        'recipe_id': recipe['id'],
        'recipe_type': normalize_recipe_type(recipe['type']),
        'expected': {
            'item': recipe['result']['item'].removeprefix('minecraft:'),
            'count': recipe['result'].get('count', 1),
        },
        **invocation,
    }


def main():
    output = {
        'schema_version': 1,
        'seed': SEED,
        'created_before_live_b3': True,
        'recipe_sampling': '25 craft_grid plus 5 smelt, uniformly without replacement within route',
        'drop_sampling': ('10 uniformly without replacement from deterministic no-silk-touch '
                          'loot tables without tool-dependent conditions or item functions'),
        'versions': {},
    }
    for version_index, version in enumerate(VERSIONS):
        truth = json.loads((ROOT / f'truth/{version}.json').read_text())
        recipes = [record for recipe in truth['recipes']
                   if (record := recipe_record(recipe)) is not None]
        craft = sorted((row for row in recipes if row['method'] == 'craft_grid'),
                       key=lambda row: row['recipe_id'])
        smelt = sorted((row for row in recipes if row['method'] == 'smelt'),
                       key=lambda row: row['recipe_id'])
        drops = sorted((row for row in truth['drops'] if deterministic_drop(row)),
                       key=lambda row: row['block'])
        if len(craft) < 25 or len(smelt) < 5 or len(drops) < 10:
            raise RuntimeError(f'insufficient B3 population for {version}')
        rng = random.Random(SEED + version_index)
        recipe_sample = rng.sample(craft, 25) + rng.sample(smelt, 5)
        rng.shuffle(recipe_sample)
        drop_sample = rng.sample(drops, 10)
        output['versions'][version] = {
            'eligible_recipe_population': {'craft_grid': len(craft), 'smelt': len(smelt)},
            'eligible_drop_population': len(drops),
            'recipes': recipe_sample,
            'drops': [{
                'block': row['block'].removeprefix('minecraft:'),
                'expected_items': [item.removeprefix('minecraft:')
                                   for item in row['no_silk_touch_items']],
            } for row in drop_sample],
        }
    path = ROOT / 'reports/b3_sample_manifest.json'
    if path.exists():
        existing = json.loads(path.read_text())
        if existing != output:
            raise SystemExit(f'refusing to overwrite changed sample manifest: {path}')
    else:
        path.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({
        'path': str(path.relative_to(ROOT)),
        'versions': {version: {
            'recipes': len(rows['recipes']), 'drops': len(rows['drops'])
        } for version, rows in output['versions'].items()},
    }))


if __name__ == '__main__':
    main()
