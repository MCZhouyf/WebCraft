"""Pure normalization and truth evaluation. No network or model calls."""
import hashlib
import json
from collections import Counter

VERSIONS = ('1.16.5','1.19','1.21.11')

def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',',':'))

def item_id(value):
    return value.removeprefix('minecraft:') if isinstance(value,str) else value

def recipe_assertion(recipe):
    result=recipe['result']
    if not result.get('item'): return None
    base={'kind':'recipe','output':item_id(result['item']), 'count':result['count'], 'station':recipe['station'], 'recipe_type':item_id(recipe['type'])}
    if recipe.get('ordered'):
        grid=[[tuple(sorted(item_id(x) for x in recipe['key'].get(char,[]))) if char!=' ' else () for char in row] for row in recipe['pattern']]
        width=max(map(len,grid))
        grid=[row+[()]*(width-len(row)) for row in grid]
        while grid and not any(grid[0]): grid.pop(0)
        while grid and not any(grid[-1]): grid.pop()
        if not grid: return None
        while not any(row[0] for row in grid): grid=[row[1:] for row in grid]
        while not any(row[-1] for row in grid): grid=[row[:-1] for row in grid]
        mirror=[list(reversed(row)) for row in grid]
        base.update(ordered=True, grid=min([grid,mirror],key=compact))
    else:
        base.update(ordered=False, ingredients=sorted([sorted(item_id(x) for x in options) for options in recipe.get('ingredients',[])],key=compact))
    return json.loads(compact(base))

def accepts_layout(assertion, layout):
    grid=[[item_id(layout[r*3+c]) if layout[r*3+c] else None for c in range(3)] for r in range(3)]
    if assertion.get('ordered'):
        while grid and not any(grid[0]): grid.pop(0)
        while grid and not any(grid[-1]): grid.pop()
        if not grid: return False
        while not any(row[0] for row in grid): grid=[row[1:] for row in grid]
        while not any(row[-1] for row in grid): grid=[row[:-1] for row in grid]
        for candidate in [grid,[list(reversed(row)) for row in grid]]:
            wanted=assertion['grid']
            if len(wanted)!=len(candidate) or len(wanted[0])!=len(candidate[0]): continue
            if all((actual in allowed if actual else not allowed) for row,pattern in zip(candidate,wanted) for actual,allowed in zip(row,pattern)): return True
        return False
    options=assertion['ingredients']
    items=Counter(item_id(x) for x in layout if x)
    if sum(items.values())!=len(options): return False
    def assign(index):
        if index==len(options): return True
        for item in options[index]:
            if items[item]:
                items[item]-=1
                accepted=assign(index+1)
                items[item]+=1
                if accepted: return True
        return False
    return assign(0)

def assertion_id(assertion):
    return 'cf_'+hashlib.sha256(compact(assertion).encode()).hexdigest()[:16]

def involved(assertion):
    values=set()
    for key in ['item','output','block']:
        if assertion.get(key): values.add(assertion[key])
    for row in assertion.get('grid',[]):
        for cell in row: values.update(cell)
    for options in assertion.get('ingredients',[]): values.update(options)
    values.update(assertion.get('drops',[]))
    return sorted(values)
