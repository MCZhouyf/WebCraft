#!/usr/bin/env python3
"""Apply the deterministic amendment-v2 feasibility-task replacement."""
import hashlib
import json
import random
import shutil
from collections import Counter
from pathlib import Path

from a6_revision import A6Revision, VERSIONS, compact

ROOT = Path('/root/autodl-tmp/webcraft')
SEED = 20261005
ARCHIVE = ROOT / 'data/archive/amendment_v2_pre_replace_20261005'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path, rows):
    path.write_text(''.join(compact(row) + '\n' for row in rows))


def mechanism_families(task, rows_by_id):
    families = set()
    for conflict_id in task['conflict_ids']:
        row = rows_by_id[conflict_id]
        assertion = row['assertion']
        if assertion['kind'] == 'drop':
            families.add((row['main_type'], 'drop', assertion['block']))
        elif assertion['kind'] in {'recipe', 'concrete_grid'}:
            families.add((row['main_type'], 'recipe', assertion.get('output')))
    return families


def main():
    tasks_path = ROOT / 'data/tasks.jsonl'
    expectations_path = ROOT / 'data/stale_agent_expectations.jsonl'
    tasks = [json.loads(line) for line in tasks_path.read_text().splitlines()]
    expectations = [json.loads(line) for line in expectations_path.read_text().splitlines()]
    old = next(task for task in tasks if task['id'] == 'fea_009')
    if old['target']['item'] != 'iron_ore':
        raise SystemExit('amendment already applied or unexpected fea_009')
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for path in (tasks_path, expectations_path):
        destination = ARCHIVE / path.name
        if destination.exists() and destination.read_bytes() != path.read_bytes():
            raise SystemExit(f'archive mismatch: {destination}')
        if not destination.exists():
            shutil.copy2(path, destination)

    revision = A6Revision()
    existing_targets = {task['target']['item'] for task in tasks}
    old_families = mechanism_families(old, revision.rows_by_id)
    candidates = []
    for candidate in revision.feasibility_candidates():
        if candidate['target'] in existing_targets:
            continue
        task = revision.finalize_task('feasibility', candidate)
        families = mechanism_families(task, revision.rows_by_id)
        overlap = sorted(old_families & families)
        if overlap:
            candidates.append((task, overlap))
    candidates.sort(key=lambda row: row[0]['target']['item'])
    if not candidates:
        raise RuntimeError('no same-mechanism feasibility replacement candidate')
    replacement, overlap = random.Random(SEED).choice(candidates)
    replacement['id'] = old['id']
    replacement['split'] = old['split']
    expectation = revision.stale_expectation(replacement)

    amended_tasks = [replacement if task['id'] == old['id'] else task for task in tasks]
    amended_expectations = [expectation if row['task_id'] == old['id'] else row
                            for row in expectations]
    write_jsonl(tasks_path, amended_tasks)
    write_jsonl(expectations_path, amended_expectations)

    feasibility = [task for task in amended_tasks if task['type'] == 'feasibility']
    balance = {version: {
        'feasible': sum(task['feasible'][version] for task in feasibility),
        'infeasible': sum(not task['feasible'][version] for task in feasibility),
    } for version in VERSIONS}
    report = {
        'seed': SEED,
        'rule': ('Uniform choice from sorted unselected feasibility candidates sharing at '
                 'least one main-type/assertion-subject mechanism family with fea_009; '
                 'fallback to all unselected feasibility candidates was not needed.'),
        'candidate_targets': [task['target']['item'] for task, _ in candidates],
        'selected_target': replacement['target']['item'],
        'shared_mechanism_families': overlap,
        'replaced': {'id': old['id'], 'target': old['target']['item'],
                     'feasibility_vector': old['feasibility_vector']},
        'replacement': replacement,
        'post_amendment_feasibility_balance': balance,
        'balance_note': ('No 100-vector candidate remained. The deterministic same-mechanism '
                         'replacement changes the 1.16.5 and 1.21.11 feasibility split from '
                         '3/7 and 7/3 to 2/8 and 8/2; this is reported, not compensated.'),
        'pre_tasks_sha256': sha256(ARCHIVE / 'tasks.jsonl'),
        'pre_expectations_sha256': sha256(ARCHIVE / 'stale_agent_expectations.jsonl'),
        'post_tasks_sha256': sha256(tasks_path),
        'post_expectations_sha256': sha256(expectations_path),
        'task_type_counts': dict(Counter(task['type'] for task in amended_tasks)),
        'split_counts': dict(Counter(task['split'] for task in amended_tasks)),
    }
    (ROOT / 'reports/amendment_v2_task_replacement.json').write_text(
        json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in (
        'candidate_targets', 'selected_target', 'post_tasks_sha256',
        'post_expectations_sha256', 'post_amendment_feasibility_balance')}))


if __name__ == '__main__':
    main()
