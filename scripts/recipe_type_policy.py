import json
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
POLICY = json.loads((ROOT / 'config/recipe_type_policy.json').read_text())
EXECUTABLE_TYPES = frozenset(POLICY['executable_recipe_types'])


def normalize_recipe_type(value):
    return (value or '').removeprefix('minecraft:')


def is_whitelisted(value):
    return normalize_recipe_type(value) in EXECUTABLE_TYPES


def rejection_reason(value):
    recipe_type = normalize_recipe_type(value)
    if recipe_type in EXECUTABLE_TYPES:
        return None
    non_grid = recipe_type == 'smithing' or recipe_type == 'crafting_transmute'
    non_grid = non_grid or recipe_type.startswith('crafting_special_')
    source = 'non_grid_recipe_type' if non_grid else 'unsupported_semantic_interface'
    return f'{source}:{recipe_type}'


def execution_route(value):
    recipe_type = normalize_recipe_type(value)
    if recipe_type not in EXECUTABLE_TYPES:
        return None
    if recipe_type in {'crafting_shaped', 'crafting_shapeless'}:
        return 'craft_grid'
    if recipe_type == 'smelting':
        return 'smelt'
    raise AssertionError('executable recipe type has no route')


def dry_run_params(recipe):
    """Translate a raw executable recipe to the frozen semantic API contract."""
    recipe_type = normalize_recipe_type(recipe.get('type'))
    route = execution_route(recipe_type)
    if route is None:
        raise ValueError(rejection_reason(recipe_type))
    if route == 'smelt':
        slots = recipe.get('ingredients', [])
        if len(slots) != 1 or not slots[0]:
            raise ValueError('smelting recipe must have one resolved ingredient slot')
        return {'method': 'smelt', 'params': {'input': slots[0][0].removeprefix('minecraft:'),
                                              'fuel': 'coal', 'n': 1}}
    choices = []
    if recipe.get('ordered'):
        layout = [None] * 9
        pattern = recipe.get('pattern', [])
        if len(pattern) > 3 or max((len(row) for row in pattern), default=0) > 3:
            raise ValueError('shaped recipe exceeds grid')
        for row_index, row in enumerate(pattern):
            for column_index, symbol in enumerate(row):
                if symbol == ' ':
                    continue
                options = recipe.get('key', {}).get(symbol, [])
                if not options:
                    raise ValueError('unresolved shaped ingredient')
                layout[row_index * 3 + column_index] = options[0].removeprefix('minecraft:')
    else:
        slots = recipe.get('ingredients', [])
        if not 0 < len(slots) <= 9 or any(not slot for slot in slots):
            raise ValueError('invalid shapeless ingredients')
        choices = [slot[0].removeprefix('minecraft:') for slot in slots]
        layout = choices + [None] * (9 - len(choices))
    if len(layout) != 9 or any(item is not None and not item.replace('_', '').isalnum()
                               for item in layout):
        raise ValueError('invalid craft_grid payload')
    return {'method': 'craft_grid', 'params': {'layout': layout}}
