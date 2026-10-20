#!/usr/bin/env python3
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
VERSIONS = ('1.16.5', '1.19', '1.21.11')


def main():
    policy = json.loads((ROOT / 'config/recipe_type_policy.json').read_text())
    executable = set(policy['executable_recipe_types'])
    rows = [json.loads(line) for line in
            (ROOT / 'data/drafts/conflicts.jsonl').read_text().splitlines() if line]
    details = []
    for row in rows:
        assertion = row.get('assertion', {})
        if assertion.get('kind') != 'recipe':
            continue
        slots = ([cell for grid_row in assertion.get('grid', []) for cell in grid_row if cell]
                 if assertion.get('ordered') else assertion.get('ingredients', []))
        if slots:
            continue
        recipe_type = assertion.get('recipe_type', '')
        outside = recipe_type not in executable
        if recipe_type in {'crafting_transmute', 'smithing'} or recipe_type.startswith('crafting_special_'):
            reason = f'non_grid_recipe_type:{recipe_type}'
        else:
            reason = f'unsupported_semantic_interface:{recipe_type}'
        details.append({
            'id': row['id'],
            'type': recipe_type,
            'output': assertion.get('output'),
            **{f'true_{version}': row['truth_vector'][version] for version in VERSIONS},
            'interface_executable': not outside,
            'proposed_grade': 'C' if outside else 'EXECUTABLE',
            'reason': reason if outside else '',
        })
    if len(details) != 53:
        raise AssertionError(f'expected 53 empty-slot assertions, found {len(details)}')

    output = ROOT / 'reports/non_grid_recipe_53_review.csv'
    with output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(details[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(sorted(details, key=lambda row: (row['type'], row['output'], row['id'])))

    c_rows = [row for row in details if row['proposed_grade'] == 'C']
    summary = {
        'total_empty_slot_assertions': len(details),
        'type_output_detail_file': str(output.relative_to(ROOT)),
        'by_type': dict(Counter(row['type'] for row in details)),
        'outside_executable_set_c_count': len(c_rows),
        'outside_executable_set_c_by_type': dict(Counter(row['type'] for row in c_rows)),
        'outside_executable_set_c_true_by_version': {
            version: sum(bool(row[f'true_{version}']) for row in c_rows) for version in VERSIONS
        },
        'executable_but_empty_count': sum(row['interface_executable'] for row in details),
        'executable_but_empty_by_type': dict(Counter(
            row['type'] for row in details if row['interface_executable'])),
        'status': 'FROZEN_ALL_53_GRADE_C',
    }
    (ROOT / 'reports/non_grid_recipe_53_summary.json').write_text(
        json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
