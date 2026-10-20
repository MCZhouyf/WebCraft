#!/usr/bin/env python3
import argparse
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path


ROOT = Path('/root/autodl-tmp/webcraft')


def qualify(value, namespace='minecraft'):
    if ':' in value:
        return value
    return f'{namespace}:{value}'


def open_data_jar(version):
    outer_path = ROOT / 'servers' / version / 'server.jar'
    outer = zipfile.ZipFile(outer_path)
    inner_name = f'META-INF/versions/{version}/server-{version}.jar'
    if inner_name in outer.namelist():
        payload = outer.read(inner_name)
        outer.close()
        return zipfile.ZipFile(io.BytesIO(payload)), outer_path, inner_name
    return outer, outer_path, None


def load_json(archive, name):
    with archive.open(name) as handle:
        return json.load(handle)


def collect_tags(archive):
    raw = {}
    pattern = re.compile(r'^data/([^/]+)/tags/(?:items|item)/(.+)\.json$')
    for name in archive.namelist():
        match = pattern.match(name)
        if not match:
            continue
        namespace, path = match.groups()
        raw[f'{namespace}:{path}'] = load_json(archive, name).get('values', [])

    cache = {}

    def expand(tag, stack=()):
        tag = qualify(tag)
        if tag in cache:
            return cache[tag]
        if tag in stack:
            return []
        namespace = tag.split(':', 1)[0]
        values = []
        for value in raw.get(tag, []):
            value = value.get('id') if isinstance(value, dict) else value
            if not isinstance(value, str):
                continue
            if value.startswith('#'):
                values.extend(expand(qualify(value[1:], namespace), stack + (tag,)))
            else:
                values.append(qualify(value, namespace))
        cache[tag] = sorted(set(values))
        return cache[tag]

    return {tag: expand(tag) for tag in raw}


def ingredient_options(spec, tags, namespace='minecraft'):
    if spec is None:
        return []
    if isinstance(spec, list):
        values = []
        for part in spec:
            values.extend(ingredient_options(part, tags, namespace))
        return sorted(set(values))
    if isinstance(spec, str):
        if spec.startswith('#'):
            return tags.get(qualify(spec[1:], namespace), [])
        return [qualify(spec, namespace)]
    if isinstance(spec, dict):
        if 'item' in spec:
            return [qualify(spec['item'], namespace)]
        if 'id' in spec and isinstance(spec['id'], str):
            return ingredient_options(spec['id'], tags, namespace)
        if 'tag' in spec:
            return tags.get(qualify(spec['tag'], namespace), [])
    return []


def normalize_result(result, namespace):
    if isinstance(result, str):
        return {'item': qualify(result, namespace), 'count': 1}
    if not isinstance(result, dict):
        return {'item': None, 'count': 0}
    item = result.get('item', result.get('id'))
    return {'item': qualify(item, namespace) if item else None, 'count': result.get('count', 1)}


def collect_recipes(archive, tags):
    recipes = []
    craftability = defaultdict(list)
    pattern = re.compile(r'^data/([^/]+)/(?:recipes|recipe)/(.+)\.json$')
    for name in archive.namelist():
        match = pattern.match(name)
        if not match:
            continue
        namespace, path = match.groups()
        data = load_json(archive, name)
        recipe_type = data.get('type', '')
        result = normalize_result(data.get('result'), namespace)
        if isinstance(data.get('result'), str):
            result['count'] = data.get('count', 1)
        record = {
            'id': f'{namespace}:{path}',
            'type': recipe_type,
            'station': 'crafting_grid' if 'crafting_' in recipe_type else (
                'furnace' if recipe_type.endswith('smelting') else recipe_type.split(':')[-1]
            ),
            'result': result
        }
        record['raw'] = data
        if 'pattern' in data:
            record['ordered'] = True
            record['pattern'] = data['pattern']
            record['key'] = {
                symbol: ingredient_options(spec, tags, namespace)
                for symbol, spec in data.get('key', {}).items()
            }
        elif 'ingredients' in data:
            record['ordered'] = False
            record['ingredients'] = [
                ingredient_options(spec, tags, namespace) for spec in data['ingredients']
            ]
        elif 'ingredient' in data:
            record['ordered'] = False
            record['ingredients'] = [ingredient_options(data['ingredient'], tags, namespace)]
        else:
            record['ordered'] = None
            record['ingredients'] = []
        recipes.append(record)
        if result['item']:
            craftability[result['item']].append(record['id'])
    recipes.sort(key=lambda item: item['id'])
    return recipes, {key: sorted(value) for key, value in sorted(craftability.items())}


def condition_mentions_silk(condition):
    return 'silk_touch' in json.dumps(condition, sort_keys=True)


def condition_value(condition, silk):
    kind = condition.get('condition', '').split(':')[-1]
    if kind == 'inverted':
        value = condition_value(condition.get('term', {}), silk)
        return None if value is None else not value
    if kind == 'match_tool' and condition_mentions_silk(condition):
        return silk
    if kind == 'survives_explosion':
        return True  # Normal player mining, not an explosion.
    return None  # Random, state-dependent, or unsupported conditions cannot be assumed true.


def possible_loot(entry, silk=False):
    values = [condition_value(c, silk) for c in entry.get('conditions', [])]
    if False in values:
        return [], False
    certain = None not in values
    entry_type = entry.get('type', '')
    if entry_type.endswith(':item'):
        return [entry.get('name')], certain
    children = entry.get('children', [])
    result = []
    if entry_type.endswith(':alternatives'):
        for child in children:
            items, always = possible_loot(child, silk)
            result.extend(items)
            if always:
                return result, certain
        return result, False
    for child in children:
        items, _ = possible_loot(child, silk)
        result.extend(items)
    return [item for item in result if item], False


def flatten_loot_items(entry, inherited=None, branch='root'):
    inherited = list(inherited or []) + entry.get('conditions', [])
    entry_type = entry.get('type', '')
    if entry_type.endswith(':item'):
        return [{
            'item': entry.get('name'),
            'branch': branch,
            'conditions': inherited,
            'functions': entry.get('functions', [])
        }]
    output = []
    for index, child in enumerate(entry.get('children', [])):
        output.extend(flatten_loot_items(child, inherited, f'{branch}/{index}'))
    return output


def collect_drops(archive):
    drops = []
    pattern = re.compile(r'^data/([^/]+)/(?:loot_tables|loot_table)/blocks/(.+)\.json$')
    for name in archive.namelist():
        match = pattern.match(name)
        if not match:
            continue
        namespace, block = match.groups()
        data = load_json(archive, name)
        possibilities = []
        no_silk = []
        silk = []
        for pool_index, pool in enumerate(data.get('pools', [])):
            for entry_index, entry in enumerate(pool.get('entries', [])):
                possibilities.extend(flatten_loot_items(
                    entry,
                    pool.get('conditions', []),
                    f'pool{pool_index}/entry{entry_index}'
                ))
                no_silk.extend(possible_loot(entry, False)[0])
                silk.extend(possible_loot(entry, True)[0])
        drops.append({
            'block': f'{namespace}:{block}',
            'no_silk_touch_items': sorted(set(item for item in no_silk if item)),
            'silk_touch_items': sorted(set(item for item in silk if item)),
            'set_semantics': 'possible_items_not_guaranteed_yields',
            'possibilities': possibilities,
            'raw': data
        })
    return sorted(drops, key=lambda item: item['block'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    args = parser.parse_args()
    version = args.version
    archive, outer_path, inner_name = open_data_jar(version)
    try:
        tags = collect_tags(archive)
        recipes, craftability = collect_recipes(archive, tags)
        drops = collect_drops(archive)
    finally:
        archive.close()

    registry_path = ROOT / 'truth' / 'tmp' / f'reports-{version}' / 'generated' / 'reports' / 'registries.json'
    if registry_path.exists():
        with registry_path.open(encoding='utf-8') as handle:
            registries = json.load(handle)
        items = sorted(registries['minecraft:item']['entries'])
    else:
        # Stage 0 removed temporary reports. Reuse only its already extracted item registry.
        existing = json.loads((ROOT / 'truth' / f'{version}.json').read_text())
        items = existing['names']['items']
    source_sha1 = hashlib.sha1(outer_path.read_bytes()).hexdigest()
    output = {
        'schema_version': 2,
        'version': version,
        'source': {
            'server_jar': str(outer_path),
            'server_jar_sha1': source_sha1,
            'inner_server_jar': inner_name,
            'registry_report': 'generated/reports/registries.json'
        },
        'counts': {
            'items': len(items),
            'item_tags': len(tags),
            'recipes': len(recipes),
            'block_loot_tables': len(drops),
            'craftable_outputs': len(craftability)
        },
        'names': {'items': items},
        'recipes': recipes,
        'drops': drops,
        'craftability': craftability
    }
    output_path = ROOT / 'truth' / f'{version}.json'
    with output_path.open('w', encoding='utf-8') as handle:
        json.dump(output, handle, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        handle.write('\n')
    print(json.dumps({'version': version, 'path': str(output_path), 'counts': output['counts']}))


if __name__ == '__main__':
    main()
