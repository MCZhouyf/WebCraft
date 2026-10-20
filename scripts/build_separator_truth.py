#!/usr/bin/env python3
import json
from collections import Counter
from pathlib import Path


ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')


def ingredient_cells(recipe):
    if 'pattern' in recipe:
        cells = []
        for row in recipe['pattern']:
            for symbol in row:
                if symbol != ' ':
                    cells.append(set(recipe['key'].get(symbol, [])))
        return cells
    return [set(options) for options in recipe.get('ingredients', [])]


def recipe_matches(recipe, supplied):
    cells = ingredient_cells(recipe)
    expanded = []
    for item, count in supplied.items():
        expanded.extend([f'minecraft:{item}'] * count)
    if len(cells) != len(expanded):
        return False

    def assign(index, remaining):
        if index == len(cells):
            return not remaining
        for candidate in sorted(remaining):
            if candidate not in cells[index]:
                continue
            next_remaining = remaining.copy()
            next_remaining.remove(candidate)
            if assign(index + 1, next_remaining):
                return True
        return False

    return assign(0, expanded)


def craftable(truth, output, supplied):
    recipe_ids = set(truth['craftability'].get(f'minecraft:{output}', []))
    return any(recipe['id'] in recipe_ids and recipe_matches(recipe, Counter(supplied))
               for recipe in truth['recipes'])


PROBES = [
    ('iron_ore_drops_raw_iron_without_silk', lambda d: 'minecraft:raw_iron' in next(
        row for row in d['drops'] if row['block'] == 'minecraft:iron_ore')['no_silk_touch_items']),
    ('cobbled_deepslate_stone_pickaxe', lambda d: craftable(d, 'stone_pickaxe', {'cobbled_deepslate': 3, 'stick': 2})),
    ('name_scute_exists', lambda d: 'minecraft:scute' in d['names']['items']),
    ('name_turtle_scute_exists', lambda d: 'minecraft:turtle_scute' in d['names']['items']),
    ('string_leather_bundle', lambda d: craftable(d, 'bundle', {'string': 1, 'leather': 1})),
    ('iron_lodestone', lambda d: craftable(d, 'lodestone', {'chiseled_stone_bricks': 8, 'iron_ingot': 1})),
    ('netherite_lodestone', lambda d: craftable(d, 'lodestone', {'chiseled_stone_bricks': 8, 'netherite_ingot': 1})),
    ('leather_iron_saddle', lambda d: craftable(d, 'saddle', {'leather': 3, 'iron_ingot': 1})),
    ('copper_pickaxe', lambda d: craftable(d, 'copper_pickaxe', {'copper_ingot': 3, 'stick': 2})),
    ('name_chain_exists', lambda d: 'minecraft:chain' in d['names']['items']),
    ('name_iron_chain_exists', lambda d: 'minecraft:iron_chain' in d['names']['items']),
    ('paper_iron_nugget_name_tag', lambda d: craftable(d, 'name_tag', {'paper': 1, 'iron_nugget': 1}))
]

EXPECTED = {
    '1.16.5': [False, False, True, False, False, False, True, False, False, True, False, False],
    '1.19': [True, True, True, False, False, False, True, False, False, True, False, False],
    '1.21.11': [True, True, False, True, True, True, False, True, True, False, True, False]
}


output = {'source': 'normalized server JAR truth', 'versions': {}}
for version in VERSIONS:
    with (ROOT / 'truth' / f'{version}.json').open(encoding='utf-8') as handle:
        truth = json.load(handle)
    rows = []
    for index, (name, evaluator) in enumerate(PROBES):
        jar_value = bool(evaluator(truth))
        expected = EXPECTED[version][index]
        rows.append({
            'id': index + 1,
            'probe': name,
            'jar': jar_value,
            'expected': expected,
            'jar_matches_expected': jar_value == expected
        })
    output['versions'][version] = rows

path = ROOT / 'truth' / 'separator_truth.json'
with path.open('w', encoding='utf-8') as handle:
    json.dump(output, handle, indent=2, ensure_ascii=True)
    handle.write('\n')
print(json.dumps({version: sum(row['jar_matches_expected'] for row in rows)
                  for version, rows in output['versions'].items()}))
