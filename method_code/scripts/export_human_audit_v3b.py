"""Enrich existing human-review evidence, without reading scorer keys or running agents."""
from __future__ import annotations

import ast
import argparse
import csv
import hashlib
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.check_human_audit_v2 import decode_actions, digest, read, reassemble

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'reports/main/post_review/human_audit_v3b'
MAIN = Path('/root/autodl-tmp/audit_sheet_v3.csv')
CDS = ROOT / 'reports/main/post_review/human_audit_v3/audit_sheet_cds_v2_fixed.csv'
VERSIONS = ('1.16.5', '1.19', '1.21.11')
MARKER = '【证据未决，待裁决】'
PAIR_FIELDS = ('stale_claim_text', 'valid_claim_text', 'stale_predicted_output', 'discriminating_action')
META = ('agent_family', 'method', 'condition', 'world', 'task_id')


def js(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def load_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def unnamespace(item):
    return item.removeprefix('minecraft:') if isinstance(item, str) else item


def normalized_recipe(recipe: dict) -> dict:
    result = {
        'kind': 'recipe', 'station': recipe['station'], 'ordered': recipe['ordered'],
        'output': unnamespace(recipe['result']['item']), 'count': recipe['result']['count'],
    }
    if recipe['ordered']:
        result['grid'] = [[sorted(unnamespace(x) for x in recipe['key'].get(symbol, []))
                           if symbol != ' ' else [] for symbol in row]
                          for row in recipe['pattern']]
    else:
        result['ingredients'] = [[unnamespace(x) for x in options] for options in recipe['ingredients']]
    return result


def slots(assertion: dict) -> list[list[str]]:
    if assertion.get('ordered'):
        return [cell for row in assertion['grid'] for cell in row if cell]
    return assertion.get('ingredients', [])


def layouts(assertion: dict) -> list[list]:
    """Deterministic finite witnesses: default, single-slot alternatives and slot permutations."""
    if assertion['kind'] == 'concrete_grid':
        return [assertion['layout']]
    if assertion['kind'] != 'recipe' or assertion['station'] != 'crafting_grid':
        return []
    grid = [None] * 9
    indices, options = [], []
    if assertion['ordered']:
        for y, row in enumerate(assertion['grid']):
            for x, cell in enumerate(row):
                if cell:
                    index = y * 3 + x
                    grid[index] = cell[0]
                    indices.append(index)
                    options.append(cell)
    else:
        for index, cell in enumerate(assertion['ingredients']):
            grid[index] = cell[0]
            indices.append(index)
            options.append(cell)
    candidates = [grid]
    for index, choices in zip(indices, options):
        for choice in choices:
            candidate = grid.copy()
            candidate[index] = choice
            candidates.append(candidate)
    if not assertion['ordered']:
        # Different placements matter when the other version still requires a shaped recipe.
        for first in range(9):
            for second in range(first + 1, 9):
                candidate = grid.copy()
                candidate[first], candidate[second] = candidate[second], candidate[first]
                candidates.append(candidate)
    return list({js(candidate): candidate for candidate in candidates}.values())


def trim_grid(grid: list[list]) -> list[list]:
    occupied = [(y, x) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell]
    if not occupied:
        return []
    y0, y1 = min(y for y, _ in occupied), max(y for y, _ in occupied)
    x0, x1 = min(x for _, x in occupied), max(x for _, x in occupied)
    return [row[x0:x1 + 1] for row in grid[y0:y1 + 1]]


def assignment_matches(items: list[str], options: list[list[str]]) -> bool:
    if not items:
        return not options
    for index, allowed in enumerate(options):
        if items[0] in allowed and assignment_matches(items[1:], options[:index] + options[index + 1:]):
            return True
    return False


def grid_matches(layout: list, recipe: dict) -> bool:
    if not recipe['ordered']:
        items = [item for item in layout if item]
        options = recipe['ingredients']
        return len(items) == len(options) and assignment_matches(items, options)
    actual = trim_grid([layout[index:index + 3] for index in (0, 3, 6)])
    expected = trim_grid(recipe['grid'])
    if not actual or len(actual) != len(expected) or len(actual[0]) != len(expected[0]):
        return False
    for pattern in (expected, [list(reversed(row)) for row in expected]):
        if all((item in allowed if allowed else item is None)
               for row, expected_row in zip(actual, pattern) for item, allowed in zip(row, expected_row)):
            return True
    return False


def mechanism_key(assertion: dict) -> tuple:
    kind = assertion['kind']
    if kind in {'recipe', 'concrete_grid'}:
        return ('recipe', assertion.get('station', 'crafting_grid'), assertion['output'])
    return (kind, assertion.get('block', assertion.get('item')))


class FrozenEvidence:
    def __init__(self):
        pin = json.loads((ROOT / 'config/webcraft_pin.json').read_text())
        pinned = {name: sha for record in pin['roots'].values() for name, sha in record.get('files', {}).items()}
        self.hashes = {}
        for name in ['data/conflicts.jsonl', 'data/tasks.jsonl', *(f'truth/{v}.json' for v in VERSIONS)]:
            sha = digest(WEB / name)
            if pinned.get(name) != sha:
                raise RuntimeError(f'Frozen evidence hash mismatch: {name}')
            self.hashes[name] = sha
        self.conflicts = {row['id']: row for row in load_lines(WEB / 'data/conflicts.jsonl')}
        self.tasks = {row['id']: row for row in load_lines(WEB / 'data/tasks.jsonl')}
        self.truth = {v: json.loads((WEB / f'truth/{v}.json').read_text()) for v in VERSIONS}
        self.recipes = {v: [(r['id'], normalized_recipe(r)) for r in self.truth[v]['recipes']
                            if r['station'] in {'crafting_grid', 'furnace'}] for v in VERSIONS}
        self.names = {v: {unnamespace(item) for item in self.truth[v]['names']['items']} for v in VERSIONS}
        # Read the documented deterministic yield table as data; never import/instantiate a world.
        path = WEB / 'oracle/planner.py'
        self.hashes['oracle/planner.py'] = digest(path)
        tree = ast.parse(path.read_text())
        self.sources = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == 'SOURCE_SPECS' for t in node.targets))
        self.groups = defaultdict(list)
        for conflict in self.conflicts.values():
            self.groups[mechanism_key(conflict['assertion'])].append(conflict)

    def outcome(self, version: str, action: dict) -> dict:
        """Static recipe/name/drop lookup only, not an environment action or episode."""
        method, params = action['method'], action['params']
        if method == 'check_name':
            return {'name': params['name'], 'resolvable': params['name'] in self.names[version]}
        if method == 'mine':
            block = params.get('block', params.get('node_type'))
            spec = self.sources[block]
            output = spec['yield'].get(version, spec['yield'].get('*'))
            return {'input_block': block, 'required_tool': spec['tool'],
                    'product': list(output) if output else None}
        if method == 'craft_grid':
            for recipe_id, recipe in self.recipes[version]:
                if recipe['station'] == 'crafting_grid' and grid_matches(params['layout'], recipe):
                    return {'product': recipe['output'], 'count': recipe['count'], 'recipe_source': recipe_id}
            return {'product': None, 'count': 0, 'recipe_source': None}
        if method == 'smelt':
            for recipe_id, recipe in self.recipes[version]:
                if recipe['station'] == 'furnace' and params['item'] in recipe['ingredients'][0]:
                    return {'product': recipe['output'], 'count': recipe['count'], 'recipe_source': recipe_id}
            return {'product': None, 'count': 0, 'recipe_source': None}
        raise ValueError(f'Unsupported static mechanism action: {method}')

    def actions(self, assertions: list[dict], conflicts: list[dict]) -> list[dict]:
        actions = [c['probe'] for c in conflicts if c.get('probe') and c['probe']['method'] in
                   {'craft_grid', 'mine', 'check_name', 'smelt'}]
        items = set()
        for assertion in assertions:
            kind = assertion['kind']
            if kind in {'recipe', 'concrete_grid'}:
                if kind == 'concrete_grid' or assertion['station'] == 'crafting_grid':
                    actions.extend({'method': 'craft_grid', 'params': {'layout': layout}} for layout in layouts(assertion))
                elif assertion['station'] == 'furnace':
                    actions.extend({'method': 'smelt', 'params': {'item': item, 'count': 1}}
                                   for option in assertion['ingredients'] for item in option)
                items.add(assertion['output'])
                items.update(item for option in slots(assertion) for item in option)
            elif kind == 'drop':
                if assertion['block'] not in self.sources:
                    raise RuntimeError('Drop quantity has no documented deterministic source')
                actions.append({'method': 'mine', 'params': {'block': assertion['block'], 'count': 1}})
            elif kind in {'name', 'craftability'}:
                items.add(assertion['item'])
            else:
                raise RuntimeError(f'Unhandled assertion kind {kind}')
        actions.extend({'method': 'check_name', 'params': {'name': item}} for item in sorted(items))
        return list({js(action): action for action in actions}.values())

    def witnesses(self, target: str, other: str, assertions: list[dict], conflicts: list[dict]) -> tuple[str, list[dict]]:
        actions = self.actions(assertions, conflicts)
        differences, identical = [], []
        for action in actions:
            current, previous = self.outcome(target, action), self.outcome(other, action)
            # Recipe identifiers may change without changing observable output.
            observed = lambda result: {k: v for k, v in result.items() if k != 'recipe_source'}
            detail = {'action': action, '当前世界': target, '当前机制反馈': current,
                      '对比版本': other, '对比机制反馈': previous}
            if observed(current) != observed(previous):
                differences.append(detail)
            elif action in [c.get('probe') for c in conflicts]:
                identical.append(detail)
        if not differences:
            # Prove equivalence over the complete finite layout domain, not just sample witnesses.
            domain = {}
            for assertion in assertions:
                if assertion['kind'] != 'recipe' or assertion['ordered'] or assertion['station'] != 'crafting_grid':
                    raise RuntimeError('No witness and no complete finite equivalence proof')
                ingredients = assertion['ingredients']
                if len(ingredients) > 3 or any(len(option) != 1 for option in ingredients):
                    raise RuntimeError('No witness; unrestricted ingredient domains need explicit review')
                for positions in itertools.permutations(range(9), len(ingredients)):
                    layout = [None] * 9
                    for position, option in zip(positions, ingredients):
                        layout[position] = option[0]
                    domain[js(layout)] = layout
            for layout in domain.values():
                action = {'method': 'craft_grid', 'params': {'layout': layout}}
                current, previous = self.outcome(target, action), self.outcome(other, action)
                if (current['product'], current['count']) != (previous['product'], previous['count']):
                    raise RuntimeError('Finite equivalence proof found an unhandled differing layout')
            if not domain:
                raise RuntimeError('Empty equivalence proof')
            sample = next(iter(domain.values()))
            detail = {'action': {'method': 'craft_grid', 'params': {'layout': sample}},
                      '当前世界': target, '当前机制反馈': self.outcome(target, {'method': 'craft_grid', 'params': {'layout': sample}}),
                      '对比版本': other, '对比机制反馈': self.outcome(other, {'method': 'craft_grid', 'params': {'layout': sample}}),
                      '完整摆法域检查数': len(domain)}
            return ('无可区分动作\n就此记录指定的输入与产物而言，完整枚举的 '
                    f'{len(domain)} 种合法摆放在两版本有同一产物/数量。'
                    '冻结文本中肯定/否定的冲突仍原样保留；不能把配方目录对象不相等直接解释为该具体摆法失配。'
                    '\n示例：' + js(detail), [detail])
        selected = differences[0]
        prerequisite = (
            'craft_grid/smelt 对比以输入数量与工具/燃料齐备为前提；不代表本回合当时具备这些前提。'
            'smelt 单次 count=1，需燃料；mine 单次 1 块，所需工具见反馈。'
        )
        absent = []
        action = selected['action']
        if action['method'] == 'craft_grid':
            absent = [item for item in action['params']['layout'] if item and
                      (item not in self.names[target] or item not in self.names[other])]
        elif action['method'] == 'smelt':
            item = action['params']['item']
            absent = [item] if item not in self.names[target] or item not in self.names[other] else []
        extra = ''
        if absent:
            names = [d for d in differences if d['action']['method'] == 'check_name' and
                     d['action']['params']['name'] in absent]
            extra = (' 此输入在一侧没有名称，不能声称能在两世界控制齐输入；可用不需库存的名称查询辅助区分：'
                     + js(names[0]) if names else ' 此输入在一侧不可取得，不能将失败归因于配方本身。')
        text = js(selected) + '\n前提说明：' + prerequisite + extra
        if identical:
            text += '\n重要：冻结默认 probe 中至少一个在两版本给相同反馈，不能据此识别版本：' + js(identical[0])
        return text, differences

    def claim(self, conflict: dict, version: str, positive: bool) -> str:
        assertion = conflict['assertion']
        source = conflict.get('source', {}).get(version)
        if source is None:
            source = f'truth/{version}.json（名称/可制作性或机制缺失记录；无 Wiki 段落原句）'
        polarity = '肯定该断言' if positive else '否定该断言（由该版本冻结机制记录导出；不是新增配方）'
        return (f'版本 {version}；来源 {source}；冲突 {conflict["id"]}；{polarity}。'
                f'冻结断言原文（JSON，不是 Wiki 逐字引文）：{js(assertion)}')

    def main_pairs(self, row: dict) -> list[dict]:
        target = row['world']
        result = []
        for cid in self.tasks[row['task_id']]['conflict_ids']:
            conflict = self.conflicts[cid]
            assertion = conflict['assertion']
            current_value = conflict['truth_vector'][target]
            other = next(v for v in VERSIONS if conflict['truth_vector'][v] != current_value)
            alternatives = self.groups[mechanism_key(assertion)]
            if current_value:
                stale = [c for c in alternatives if not c['truth_vector'][target] and c['truth_vector'][other]]
                stale = sorted(stale, key=lambda c: c['id']) or [conflict]
                valid = [conflict]
            else:
                stale = [conflict]
                valid = sorted([c for c in alternatives if c['truth_vector'][target]], key=lambda c: c['id'])
            stale_positive = not current_value or stale[0] is not conflict
            stale_text = '\n'.join(self.claim(c, other, stale_positive) for c in stale)
            valid_text = '\n'.join(self.claim(c, target, True) for c in valid) if valid else self.claim(conflict, target, False)
            evidence_assertions = [c['assertion'] for c in stale + valid]
            # A renamed output can be found through the same input layout in frozen truth.
            if not valid and assertion['kind'] == 'recipe':
                for layout in layouts(assertion)[:1]:
                    answer = self.outcome(target, {'method': 'craft_grid', 'params': {'layout': layout}})
                    if answer['product']:
                        valid_text += '\n同一输入在目标世界的实际机制结果：' + js(answer)
            witness, differences = self.witnesses(target, other, evidence_assertions, [conflict, *stale, *valid])
            outputs = self.output_description(stale[0]['assertion'], target, other, stale_positive, differences)
            result.append(dict(conflict_id=cid, stale_claim_text=stale_text, valid_claim_text=valid_text,
                               stale_predicted_output=outputs, discriminating_action=witness))
        return result

    def output_description(self, assertion: dict, target: str, other: str, positive: bool,
                           differences: list[dict]) -> str:
        kind = assertion['kind']
        if kind in {'recipe', 'concrete_grid'}:
            if kind == 'concrete_grid':
                inputs = dict(Counter(item for item in assertion['layout'] if item))
                count = '按同一冻结摆法匹配配方的 count（见下方版本反馈）'
            else:
                inputs = {'逐输入槽任选其一，每槽消耗1': slots(assertion), '有序': assertion['ordered']}
                count = assertion['count']
            prediction = {'输入要求': inputs, '正断言预测产物': assertion['output'], '数量': count,
                          '若陈旧断言是否定形式': '预测该输入不产出此物品' if not positive else '不适用'}
        elif kind == 'drop':
            prediction = {'输入': {'block': assertion['block'], 'count': 1, 'silk_touch': False},
                          '断言可能掉落集合': assertion['drops'],
                          '数量依据': '目录是可能物品集合，不承诺数量；以下数量取冻结确定性后端 SOURCE_SPECS',
                          '版本数量与工具': {v: self.outcome(v, {'method': 'mine', 'params': {'block': assertion['block'], 'count': 1}})
                                     for v in (other, target)}}
        elif kind in {'name', 'craftability'}:
            prediction = {'item': assertion['item'], 'inventory_consumption': 0,
                          '断言含义': '标识符存在' if kind == 'name' else '该物品可制作',
                          '预测': assertion.get('exists', True) if positive else not assertion.get('exists', True)}
            if kind == 'craftability':
                prediction['配方与输入数量'] = [a for _, a in self.recipes[other]
                                             if a['output'] == assertion['item']]
        else:
            raise RuntimeError(f'Unrendered output for {kind}')
        return js(prediction) + '\n动作下的数量/产物比较（可能同反馈，详见区分动作列）：' + js(differences[0])

    def cds_pairs(self, row: dict, metadata: dict) -> list[dict]:
        visible_path = WEB / metadata['visible_path']
        evaluator_path = WEB / metadata['evaluator_path']
        for path, expected in ((visible_path, metadata['visible_sha256']), (evaluator_path, metadata['evaluator_sha256'])):
            actual = digest(path)
            if actual != expected:
                raise RuntimeError('CDS material freeze mismatch')
            self.hashes[str(path.relative_to(WEB))] = actual
        visible = json.loads(visible_path.read_text())
        # Read mechanism descriptions only, never boundary truth or episode scoring fields.
        mechanisms = json.loads(evaluator_path.read_text())['conditions'][row['condition']]
        by_text = {hashlib.sha256(fact['mechanism fact'].encode()).hexdigest(): fact
                   for fact in visible['conditions'][row['condition']]}
        grouped = defaultdict(dict)
        for mechanism in mechanisms:
            if mechanism['role'] in {'focal_correct', 'focal_stale'}:
                grouped[mechanism['conflict_id']][mechanism['role']] = mechanism
        result = []
        for cid, pair in sorted(grouped.items()):
            if set(pair) != {'focal_correct', 'focal_stale'}:
                raise RuntimeError('Incomplete CDS conflict pair; stop')
            stale, valid = pair['focal_stale'], pair['focal_correct']
            if stale['text_sha256'] not in by_text or valid['text_sha256'] not in by_text:
                raise RuntimeError('CDS original literal text missing')
            def literal(mechanism, version):
                fact = by_text[mechanism['text_sha256']]
                return (f'版本 {version}；来源 {fact["source"]}；时间戳 {fact["timestamp"]}；'
                        f'冲突 {cid}；冻结材料原文：{fact["mechanism fact"]}\n'
                        f'对应结构化机制：{js(mechanism["assertion"])}；断言极性：{mechanism["value"]}')
            conflict = self.conflicts[cid]
            witness, differences = self.witnesses(row['world'], metadata['comparison_world'],
                                                [stale['assertion'], valid['assertion']], [conflict])
            result.append(dict(conflict_id=cid,
                               stale_claim_text=literal(stale, metadata['comparison_world']),
                               valid_claim_text=literal(valid, row['world']),
                               stale_predicted_output=self.output_description(stale['assertion'], row['world'],
                                                                              metadata['comparison_world'], stale['value'], differences),
                               discriminating_action=witness))
        if not result:
            raise RuntimeError('No CDS focal mechanisms found')
        return result


def cds_metadata(rows: list[dict]) -> dict[str, dict]:
    manifest = json.loads((WEB / 'cds_materials/cds_manifest.json').read_text())['rows']
    answer = {}
    for row in rows:
        events = decode_actions(reassemble(row, 'action_part', 'action_sequence_and_feedback', 'truncated'))
        injected = [event['result']['diagnostic_mechanism_facts'] for event in events
                    if 'diagnostic_mechanism_facts' in (event.get('result') or {})]
        if not injected:
            raise RuntimeError('CDS identity requires its saved initial fact list')
        matches = []
        for entry in manifest:
            if entry['task_id'] != row['task_id'] or entry['target_world'] != row['world']:
                continue
            visible = json.loads((WEB / entry['visible_path']).read_text())
            if visible['conditions'][row['condition']] == injected[0]:
                matches.append(entry)
        if len(matches) != 1:
            raise RuntimeError('CDS material identity is not unique')
        answer[row['audit_id']] = matches[0]
    return answer


def snippet_sharing(rows: list[dict]) -> tuple[dict, dict]:
    groups = defaultdict(list)
    for row in rows:
        text = reassemble(row, 'search_part', 'search_snippets', 'search_split')
        groups[text].append(row)
    result = {}
    for text, members in groups.items():
        for row in members:
            same_scope = [other for other in members if other['task_id'] == row['task_id']
                          and other['condition'] == row['condition']]
            describe = lambda members: [{'audit_id': r['audit_id'], **{k: r[k] for k in META}}
                                       for r in sorted(members, key=lambda r: r['audit_id'])]
            result[row['audit_id']] = {
                'status': '独有' if len(members) == 1 else '共享',
                'sha256': hashlib.sha256(text.encode()).hexdigest(),
                'same_task_condition_methods': sorted({r['method'] for r in same_scope}),
                'same_task_condition_rows': describe(same_scope),
                'all_identical_block_rows': describe(members),
                'basis': '全部拆列按编号无损拼接后的片段块逐字相同；不据此断言直接记录了模型上下文',
            }
    return result, {'rows': len(rows), 'exact_distinct_blocks': len(groups),
                    'exact_shared_blocks': sum(len(v) > 1 for v in groups.values()),
                    'unique_rows': sum(len(v) == 1 for v in groups.values()),
                    'shared_rows': sum(len(v) for v in groups.values() if len(v) > 1)}


def enrich(headers: list[str], rows: list[dict], evidence: FrozenEvidence, sharing: dict,
           cds: dict | None = None) -> tuple[list[str], list[dict], list[dict]]:
    excluded = {'contact_type', 'propagation', 'notes', 'terminal_depends_on_stale', 'prev_terminal',
                'prev_terminal_depends_on_stale'}
    preserved = [field for field in headers if field not in excluded]
    previous = ['prev_contact_type_v3', 'prev_propagation_v3', 'prev_notes_v3'] if cds is None else [
        'prev_contact_type_cds_v2', 'prev_propagation_cds_v2', 'prev_notes_cds_v2']
    fields = preserved + list(PAIR_FIELDS) + ['conflict_pair_count', 'search_ownership',
                                            *previous, 'contact_type', 'propagation', 'notes']
    result, summaries = [], []
    for row in rows:
        pairs = evidence.main_pairs(row) if cds is None else evidence.cds_pairs(row, cds[row['audit_id']])
        new = {field: row[field] for field in preserved}
        for field in PAIR_FIELDS:
            new[field] = '\n\n'.join(f'[{i}] {pair["conflict_id"]}\n{pair[field]}' for i, pair in enumerate(pairs, 1))
        new['conflict_pair_count'] = str(len(pairs))
        new['search_ownership'] = js(sharing[row['audit_id']])
        new.update(dict(zip(previous, [row['contact_type'], row['propagation'], row['notes']])))
        # Do not convert a historical query+execution label or decide an unresolved case.
        new.update(contact_type='', propagation='', notes='')
        if cds is not None and row['contact_type'] in {'无', '查询', '执行', '无法确定'}:
            new.update({field: row[field] for field in ('contact_type', 'propagation', 'notes')})
            if row['contact_type'] in {'无', '无法确定'}:
                new['propagation'] = ''
        result.append(new)
        summaries.append({
            'audit_id': row['audit_id'], **{key: row[key] for key in META},
            'conflict_pair_count': len(pairs), 'conflict_ids': ';'.join(p['conflict_id'] for p in pairs),
            'no_discriminating_action_pairs': sum(p['discriminating_action'].startswith('无可区分动作') for p in pairs),
            'search_status': sharing[row['audit_id']]['status'],
            'search_sha256': sharing[row['audit_id']]['sha256'],
            'same_task_condition_methods': ';'.join(sharing[row['audit_id']]['same_task_condition_methods']),
            'original_evidence_unchanged': all(new[field] == row[field] for field in preserved),
            'review_required': (not bool(new['contact_type']) or new['contact_type'] == '无法确定'
                                or new['propagation'] == '无法确定'),
        })
    # Split new long cells only; never resplit the original action/search evidence.
    for field in PAIR_FIELDS + ('search_ownership',):
        for row in result:
            text = row[field]
            parts, buffer, units = [], [], 0
            for char in text:
                size = len(char.encode('utf-16-le')) // 2
                if units + size > 30000:
                    parts.append(''.join(buffer))
                    buffer, units = [], 0
                buffer.append(char)
                units += size
            parts.append(''.join(buffer))
            if len(parts) > 1:
                row[field] = f'完整内容见 {field}_part1 至 {field}_part{len(parts)}（无损拆分）'
                for index, part in enumerate(parts, 1):
                    name = f'{field}_part{index}'
                    if name not in fields:
                        fields.append(name)
                    row[name] = part
        for row in result:
            for name in fields:
                row.setdefault(name, '')
    return fields, result, summaries


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    OUT = parser.parse_args().output_dir.resolve()
    if OUT.exists():
        raise RuntimeError('Refusing to overwrite an existing review export')
    paths = [MAIN, CDS, ROOT / 'config/webcraft_pin.json', WEB / 'cds_materials/cds_manifest.json',
             WEB / 'config/ext_v2/freeze_manifest.json', Path('/root/autodl-tmp/server.lock')]
    snapshots = {str(path): digest(path) for path in paths}
    key_stats = {str(path): (path.stat().st_size, path.stat().st_mtime_ns)
                 for path in ROOT.glob('reports/**/audit_key*.csv')}
    headers, original = read(MAIN)
    cds_headers, cds_rows = read(CDS)
    marked = {r['audit_id'] for r in original if MARKER in r['notes']}
    unknown = {r['audit_id'] for r in original if r['prev_contact_type'] == '无法确定'}
    if marked != unknown or len(marked) != 37:
        raise RuntimeError(f'Marker crosscheck failed: marker-only={marked - unknown}, previous-only={unknown - marked}')
    if len(original) != 80 or len(cds_rows) != 30:
        raise RuntimeError('Input cohort size changed')
    selected = [row for row in original if row['audit_id'] in marked]
    evidence = FrozenEvidence()
    metadata = cds_metadata(cds_rows)
    sharing, sharing_stats = snippet_sharing(original)
    surface = Counter(row['search_snippets'] for row in original)
    surface_stats = {'distinct_cells': len(surface), 'shared_cell_groups': sum(n > 1 for n in surface.values()),
                     'rows_in_shared_cells': sum(n for n in surface.values() if n > 1),
                     'shared_pointer_groups': sum(n > 1 and text.startswith('完整检索片段按顺序见')
                                                  for text, n in surface.items())}
    cds_sharing, cds_sharing_stats = snippet_sharing(cds_rows)
    fields, enriched, main_summary = enrich(headers, selected, evidence, sharing)
    cds_fields, cds_enriched, cds_summary = enrich(cds_headers, cds_rows, evidence, cds_sharing, metadata)
    for before, after in [(selected, enriched), (cds_rows, cds_enriched)]:
        if len({r['audit_id'] for r in after}) != len(before):
            raise RuntimeError('Row correspondence is not one-to-one')
        for old, new in zip(before, after):
            for prefix, field, flag in [('action_part', 'action_sequence_and_feedback', 'truncated'),
                                        ('search_part', 'search_snippets', 'search_split')]:
                if reassemble(old, prefix, field, flag) != reassemble(new, prefix, field, flag):
                    raise RuntimeError('Original evidence changed')
            events = decode_actions(reassemble(new, 'action_part', 'action_sequence_and_feedback', 'truncated'))
            if len(events) != int(new['n_actions']) or not new['search_snippets'].strip():
                raise RuntimeError('Incomplete original evidence')
    if snapshots != {str(path): digest(path) for path in paths}:
        raise RuntimeError('Input changed during export')
    if any((Path(path).stat().st_size, Path(path).stat().st_mtime_ns) != stat for path, stat in key_stats.items()):
        raise RuntimeError('Audit key metadata changed; no label file was read')
    for name, sha in evidence.hashes.items():
        if digest(WEB / name) != sha:
            raise RuntimeError('Frozen mechanism evidence changed during export')
    OUT.mkdir(parents=True)
    (OUT / 'README.md').write_text((ROOT / 'scripts/templates/human_audit_v3b_README.md').read_text(), encoding='utf-8')
    write_csv(OUT / 'audit_sheet_v3b.csv', fields, enriched)
    write_csv(OUT / 'audit_sheet_cds_v3.csv', cds_fields, cds_enriched)
    for name, rows in [('main_row_evidence.csv', main_summary), ('cds_row_evidence.csv', cds_summary)]:
        write_csv(OUT / name, list(rows[0]), rows)
    report = {
        'llm_calls': 0, 'new_episodes': 0, 'scorer_key_read': False,
        'audit_key_metadata_unchanged': True, 'server_lock_unchanged': True,
        'marker_matches_previous_unknown': True, 'main_ids': sorted(marked),
        'main_rows': len(enriched), 'cds_rows': len(cds_enriched),
        'main_pair_count_distribution': dict(Counter(r['conflict_pair_count'] for r in main_summary)),
        'cds_pair_count_distribution': dict(Counter(r['conflict_pair_count'] for r in cds_summary)),
        'main_rows_no_discriminating_action': sum(r['no_discriminating_action_pairs'] > 0 for r in main_summary),
        'cds_rows_no_discriminating_action': sum(r['no_discriminating_action_pairs'] > 0 for r in cds_summary),
        'main_rows_all_pairs_nondiscriminating': sum(r['no_discriminating_action_pairs'] == r['conflict_pair_count'] for r in main_summary),
        'cds_rows_all_pairs_nondiscriminating': sum(r['no_discriminating_action_pairs'] == r['conflict_pair_count'] for r in cds_summary),
        'main_selected_search_status': dict(Counter(r['search_status'] for r in main_summary)),
        'all_80_unsplit_surface_cells': surface_stats,
        'all_80_search_blocks': sharing_stats, 'cds_search_blocks': cds_sharing_stats,
        'cds_review_ids': [r['audit_id'] for r in cds_summary if r['review_required']],
        'row_correspondence': '37/37 and 30/30; original action/search cells unchanged',
        'input_hashes': snapshots, 'webcraft_read_only_evidence_hashes': evidence.hashes,
    }
    (OUT / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    (OUT / 'REPORT.md').write_text(
        '# 冲突对证据重导出\n\n'
        '零 LLM 调用、零新回合；未读 audit_key 的标签，未修改 WebCraft、原表或冻结文件。\n\n'
        f'标记集合与 prev_contact_type=无法确定 的集合完全相同：{len(marked)} 行。\n'
        '37 行清单、逐行冲突数与对应校验见 main_row_evidence.csv；CDS 同类统计见 cds_row_evidence.csv。\n\n'
        f'主表冲突数分布：{js(report["main_pair_count_distribution"])}；'
        f'CDS：{js(report["cds_pair_count_distribution"])}。\n'
        f'无可区分动作行数：主表 {report["main_rows_no_discriminating_action"]}；CDS {report["cds_rows_no_discriminating_action"]}。\n'
        '这里按一行中至少有一条冲突无区分动作计；整行所有冲突均无区分动作的行数：'
        f'主表 {report["main_rows_all_pairs_nondiscriminating"]}；CDS {report["cds_rows_all_pairs_nondiscriminating"]}。\n'
        f'37 行片段独有/共享：{js(report["main_selected_search_status"])}。\n'
        f'全部 80 行的逐字片段块统计：{js(sharing_stats)}。\n'
        f'CDS 片段块统计：{js(cds_sharing_stats)}。\n\n'
        f'仅看 search_snippets 表面单元格（未拼接）的统计：{js(surface_stats)}。'
        '背景的 41 个不同块、11 个共享块可以复现；其中 10 个共享组实际上是“完整检索片段见 search_part…”'
        '的重复拆列提示，不等于真实片段内容共享。片段共享按完整拆列拼接后逐字相等定义，'
        '不按物品或部分引用合并，未将不相等的查询说明或片段强行合并。每行 search_ownership 显示'
        '同任务同条件方法范围，以及跨任务/条件的相同块（包括无检索说明）。\n\n'
        '对应关系：37 ↔ v3 中原有 37 个 audit_id；30 ↔ CDS 30 个 audit_id；ID 无重复无缺失。'
        '动作、反馈与 search 的全部原始拆列逐字保留。CDS 定位使用同任务、目标世界和初始库存观测中保存的完整注入事实列表，'
        '不重新抽样，不使用评分器标签选行。\n\n'
        f'CDS 待复核 ID：{", ".join(report["cds_review_ids"])}。其余既有可用标签直接保留；'
        '历史“查询+执行”只保留在 prev_*，不自动代填新标签。\n\n'
        '原冲突库只提供结构化断言，不存在的 Wiki 逐字原句没有被伪造。CDS 使用冻结材料的字面文本，'
        '可与结构化断言并看；负断言或原料集合的目录差异不必等于某个具体摆法会失败，'
        '区分动作列额外列出版本机制查表结果与同反馈的默认动作，供人工判定。'
        '这不是对任一回合接触/传播的自动标签。\n', encoding='utf-8')
    print(js({key: value for key, value in report.items() if key not in
              {'input_hashes', 'webcraft_read_only_evidence_hashes', 'main_ids'}}))


if __name__ == '__main__':
    main()
