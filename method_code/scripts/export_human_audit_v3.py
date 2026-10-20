"""Freeze a deterministic unique-episode sample, then export v3 review files.

Only archived sampling strata are used. This script never opens audit_key.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

from scripts.check_human_audit_v2 import digest, read, reassemble

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/main/post_review/human_audit_v3'
ARCHIVE = OUT / 'archive'
MAIN = Path('/root/autodl-tmp/audit_sheet_v2.csv')
CDS = Path('/root/autodl-tmp/audit_sheet_cds_v2.csv')
META = ('agent_family', 'method', 'condition', 'world', 'task_id')
SAMPLE_SEED = 20261019
SHUFFLE_SEED = 20261020
PRIORITY = ('propagation', 'query', 'execution', 'nonspecific', 'S4_no_contact_abstention')
ORIGINAL = ROOT / 'reports/main/post_review/review_v13_audit'
IDMAP = ORIGINAL / 'reexport_v2_private/audit_idmap.csv'
SAMPLING_HISTORY = ORIGINAL / 'reexport_v2_diagnostics/step0_window_contacts.json'


def write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def fixed_cds() -> None:
    target = OUT / 'audit_sheet_cds_v2_fixed.csv'
    if target.exists():
        raise RuntimeError('refusing to overwrite existing CDS review file')
    before = digest(CDS)
    headers, rows = read(CDS)
    if len(rows) != 30 or len({row['audit_id'] for row in rows}) != 30:
        raise RuntimeError('CDS coverage changed')
    fixed, changes = [], []
    for row in rows:
        new = dict(row)
        if row['contact_type'] == '无':
            new['propagation'] = ''
            changes.append({'audit_id': row['audit_id'], 'field': 'propagation',
                            'old_value': row['propagation'], 'new_value': '',
                            'reason': 'contact_type=无；按作者要求清空'})
        fixed.append(new)
    if len(changes) != 13:
        raise RuntimeError('expected exactly 13 CDS no-contact rows; stop without export')
    for old, new in zip(rows, fixed):
        for field in headers:
            permitted = field == 'propagation' and old['contact_type'] == '无'
            if not permitted and old[field] != new[field]:
                raise RuntimeError('unexpected CDS modification')
    if digest(CDS) != before:
        raise RuntimeError('CDS input changed while exporting')
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(target, headers, fixed)
    write_csv(OUT / 'cds_changes.csv', ['audit_id', 'field', 'old_value', 'new_value', 'reason'], changes)
    (OUT / 'README_CDS_FIXED.md').write_text(
        '# CDS v2 轻修说明\n\n'
        '`terminal_depends_on_stale` 列已废弃：保留原值仅供存档，不再要求填写，不用于后续判定。\n\n'
        '本次不重标。仅将 contact_type=无 的 13 行 propagation 清空；其余单元格逐项保持原值。\n'
        '其中 v2 的其它接触类别也原样保留，不擅自转换；本表不适用针对 80 条 v3 的三值校验器。\n'
        '具体变更见 cds_changes.csv。原上传文件及原始审计表均未修改。\n', encoding='utf-8')
    ARCHIVE.mkdir(exist_ok=True)
    (ARCHIVE / 'cds_fix_checks.json').write_text(json.dumps({
        'input_path': str(CDS), 'input_sha256': before, 'rows': 30,
        'cleared_propagation_rows': len(changes), 'other_cells_unchanged': True,
        'deprecated_terminal_preserved': True, 'llm_calls': 0, 'new_episodes': 0,
        'scorer_key_read': False, 'output_sha256': digest(target),
    }, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'CDS_rows': 30, 'cleared_propagation': len(changes), 'other_cells_unchanged': True}))


def unique_cohort() -> tuple[list[str], list[dict], dict, dict]:
    headers, rows = read(MAIN)
    if len(rows) != 147 or len({row['audit_id'] for row in rows}) != 147:
        raise RuntimeError('main annotation coverage changed')
    _, mapping = read(IDMAP)
    ids = {row['audit_id']: row['trajectory_id'] for row in mapping}
    # Only these two sampling-identity fields are accessed from the old report.
    strata = {row['trajectory_id']: row['stratum']
              for row in json.loads(SAMPLING_HISTORY.read_text())}
    groups = defaultdict(list)
    for row in rows:
        text = reassemble(row, 'action_part', 'action_sequence_and_feedback', 'truncated')
        snippets = reassemble(row, 'search_part', 'search_snippets', 'search_split')
        signature = tuple(row[field] for field in META) + (text, snippets)
        groups[signature].append(row)
    if len(groups) != 119:
        raise RuntimeError('unique cohort is not 119 complete episodes')
    representatives, aliases, assigned = [], {}, {}
    rank = {label: index for index, label in enumerate(PRIORITY)}
    for values in groups.values():
        representative = min(values, key=lambda row: (rank[strata[ids[row['audit_id']]]], row['audit_id']))
        identifier = representative['audit_id']
        representatives.append(representative)
        aliases[identifier] = sorted(row['audit_id'] for row in values)
        assigned[identifier] = strata[ids[identifier]]
    return headers, sorted(representatives, key=lambda row: row['audit_id']), aliases, assigned


def quotas(counts: Counter, total: int = 80) -> dict[str, int]:
    denominator = sum(counts.values())
    exact = {label: Fraction(counts[label] * total, denominator) for label in PRIORITY}
    result = {label: exact[label].numerator // exact[label].denominator for label in PRIORITY}
    remainders = sorted(PRIORITY, key=lambda label: (-(exact[label] - result[label]), PRIORITY.index(label)))
    for label in remainders[:total - sum(result.values())]:
        result[label] += 1
    if sum(result.values()) != total or any(result[label] > counts[label] for label in PRIORITY):
        raise RuntimeError('sampling allocation exceeds unique-cohort capacity')
    return result


def freeze_sample() -> None:
    manifest_path = ARCHIVE / 'sample_manifest.json'
    if manifest_path.exists() or (OUT / 'audit_sheet_v3.csv').exists():
        raise RuntimeError('sample already frozen; refusing to draw again')
    _, representatives, aliases, assigned = unique_cohort()
    counts = Counter(assigned.values())
    allocation = quotas(counts)
    rng = random.Random(SAMPLE_SEED)
    selected = []
    for label in PRIORITY:
        candidates = sorted(row['audit_id'] for row in representatives if assigned[row['audit_id']] == label)
        selected.extend(rng.sample(candidates, allocation[label]))
    selected_set = set(selected)
    unselected = [row['audit_id'] for row in representatives if row['audit_id'] not in selected_set]
    if len(selected_set) != 80 or len(unselected) != 39:
        raise RuntimeError('80/39 split failed')
    hashes = {str(path): digest(path) for path in (MAIN, IDMAP, SAMPLING_HISTORY)}
    manifest = {'basis': 'unique_119_with_original_sampling_priority', 'sample_seed': SAMPLE_SEED,
                'shuffle_seed': SHUFFLE_SEED, 'selected_audit_ids': selected,
                'unselected_audit_ids': unselected, 'source_sha256': hashes,
                'representative_aliases': aliases, 'scorer_key_read': False,
                'llm_calls': 0, 'new_episodes': 0}
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    report = ['# v3 sampling freeze (author archive)', '',
              'Sampling is completed before any v3 table is written. No result-based redrawing is permitted.',
              f'Sampling seed: {SAMPLE_SEED}. Independent row-shuffle seed: {SHUFFLE_SEED}.', '',
              'The basis is the 119 unique full episodes, not the duplicated 147 rows.',
              'A literal 147-row proportional sample would require 15 propagation episodes but only 12 exist.',
              'Cross-stratum duplicates are assigned once in original sampling priority order:',
              'propagation > query > execution > nonspecific > no-contact abstention.',
              'Within that first eligible stratum, the smallest existing neutral v2 ID represents the episode.',
              'Allocation uses exact rational proportions and largest remainders, ties in that priority order.',
              'The source is the archived step-0 sampling history plus neutral ID map; audit_key is not opened.', '',
              '| Original sampling stratum | Duplicated v2 rows | Unique assigned cohort | Selected | Unselected |',
              '| --- | ---: | ---: | ---: | ---: |']
    original_counts = {'propagation': 27, 'query': 30, 'execution': 30, 'nonspecific': 40, 'S4_no_contact_abstention': 20}
    for label in PRIORITY:
        report.append(f'| {label} | {original_counts[label]} | {counts[label]} | {allocation[label]} | {counts[label] - allocation[label]} |')
    report.extend(['| Total | 147 | 119 | 80 | 39 |', '',
                   'Sampling manifest contains neutral IDs and source hashes, not scoring labels.', ''])
    (ARCHIVE / 'SAMPLING_REPORT.md').write_text('\n'.join(report))
    print(json.dumps({'frozen': True, 'unique_cohort': dict(counts), 'selected': allocation,
                      'selected_rows': 80, 'unselected_rows': 39}, sort_keys=True))


def export_main() -> None:
    manifest_path = ARCHIVE / 'sample_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if any(digest(Path(path)) != value for path, value in manifest['source_sha256'].items()):
        raise RuntimeError('frozen sample inputs changed')
    target = OUT / 'audit_sheet_v3.csv'
    if target.exists():
        raise RuntimeError('refusing to overwrite an existing v3 review sheet')
    old_headers, representatives, aliases, assigned = unique_cohort()
    by_id = {row['audit_id']: row for row in representatives}
    selected = list(manifest['selected_audit_ids'])
    unselected_ids = set(manifest['unselected_audit_ids'])
    if (len(set(selected)) != 80 or len(unselected_ids) != 39
            or set(selected) & unselected_ids or set(selected) | unselected_ids != set(by_id)):
        raise RuntimeError('frozen manifest split changed')
    # Sampling is already frozen. This independent RNG only changes row order.
    random.Random(manifest['shuffle_seed']).shuffle(selected)
    evidence_fields = [field for field in old_headers if field not in {
        'prev_contact_type', 'prev_propagation', 'prev_notes', 'prev_terminal',
        'contact_type', 'propagation', 'terminal_depends_on_stale', 'notes'}]
    headers = evidence_fields + ['prev_contact_type', 'prev_propagation', 'prev_notes',
                                 'contact_type', 'propagation', 'notes']
    output, mapping = [], []
    for identifier in selected:
        original_row = by_id[identifier]
        row = {field: original_row[field] for field in evidence_fields}
        row.update({'prev_contact_type': original_row['contact_type'],
                    'prev_propagation': original_row['propagation'], 'prev_notes': original_row['notes'],
                    'contact_type': '', 'propagation': '', 'notes': ''})
        if not row['search_snippets'].strip():
            raise RuntimeError('missing search evidence')
        if any(row[field] != original_row[field] for field in evidence_fields):
            raise RuntimeError('evidence mismatch')
        if any(re.search(r'\bS[1-5]\b|A6-[EP]-(?:S4-)?\d{3}', str(value)) for value in row.values()):
            raise RuntimeError('stratum marker or original trajectory ID leaked into v3')
        output.append(row)
        mapping.append({'audit_id': identifier, 'v2_audit_id': identifier,
                        'all_v2_equivalent_audit_ids': ';'.join(aliases[identifier]),
                        'evidence_unchanged': True, 'previous_annotations_match_v2': True})
    if any('terminal' in field for field in headers):
        raise RuntimeError('deprecated terminal column leaked into v3')
    if any(digest(Path(path)) != value for path, value in manifest['source_sha256'].items()):
        raise RuntimeError('frozen sample inputs changed during export')
    write_csv(target, headers, output)
    write_csv(ARCHIVE / 'v3_v2_mapping.csv', list(mapping[0]), mapping)
    unselected = [{'audit_id': identifier, **{field: by_id[identifier][field] for field in META},
                   'all_v2_equivalent_audit_ids': ';'.join(aliases[identifier])}
                  for identifier in manifest['unselected_audit_ids']]
    write_csv(ARCHIVE / 'unselected_39.csv', list(unselected[0]), unselected)
    checks = {'rows': 80, 'unique_complete_episodes': 80, 'neutral_ids_preserved': True,
              'evidence_and_parts_unchanged': True, 'previous_annotations_match_v2': True,
              'terminal_columns_absent': True, 'new_annotations_blank': True,
              'unselected_unique_episodes': 39, 'sample_manifest_sha256': digest(manifest_path),
              'output_sha256': digest(target), 'scorer_key_read': False,
              'llm_calls': 0, 'new_episodes': 0}
    (ARCHIVE / 'export_checks.json').write_text(json.dumps(checks, indent=2, sort_keys=True) + '\n')
    print(json.dumps(checks, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('fix-cds', 'freeze-sample', 'export-main'))
    args = parser.parse_args()
    {'fix-cds': fixed_cds, 'freeze-sample': freeze_sample, 'export-main': export_main}[args.stage]()


if __name__ == '__main__':
    main()
