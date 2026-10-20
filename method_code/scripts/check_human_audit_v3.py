"""Check and archive completed v3 human labels without scorer-key access."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from scripts.check_human_audit_v2 import decode_actions, digest, read, reassemble
from scripts.validate_v3 import ANNOTATION_FIELDS, validate_rows

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/main/post_review/human_audit_v3'
UPLOAD = Path('/root/autodl-tmp/audit_sheet_v3.csv')
OUT = BASE / 'completed_review'


def main() -> int:
    template = BASE / 'audit_sheet_v3.csv'
    manifest_path = BASE / 'archive/sample_manifest.json'
    original_export = json.loads((BASE / 'archive/export_checks.json').read_text())
    before = {str(p): digest(p) for p in (UPLOAD, template, manifest_path)}
    if digest(template) != original_export['output_sha256']:
        raise RuntimeError('frozen v3 template has changed')
    if digest(manifest_path) != original_export['sample_manifest_sha256']:
        raise RuntimeError('frozen sample manifest has changed')
    headers, rows = read(UPLOAD)
    old_headers, old_rows = read(template)
    old = {row['audit_id']: row for row in old_rows}
    selected = set(json.loads(manifest_path.read_text())['selected_audit_ids'])
    validation = validate_rows(rows)
    changes, issues = [], []
    if headers != old_headers:
        issues.append({'audit_id': '', 'issue': 'header_changed'})
    if {row['audit_id'] for row in rows} != set(old) or set(old) != selected:
        issues.append({'audit_id': '', 'issue': 'frozen_sample_ID_mismatch'})
    for row in rows:
        identifier = row['audit_id']
        if identifier not in old:
            continue
        for field in old_headers:
            if field not in ANNOTATION_FIELDS and row.get(field) != old[identifier].get(field):
                changes.append({'audit_id': identifier, 'field': field})
        events = decode_actions(reassemble(row, 'action_part', 'action_sequence_and_feedback', 'truncated'))
        methods = [event['method'] for event in events]
        if len(events) != int(row['n_actions']) or methods[-1] != 'submit' or methods.count('submit') != 1:
            issues.append({'audit_id': identifier, 'issue': 'action_count_or_submit_mismatch'})
        if row['contact_type'] == '查询' and 'check_name' not in methods:
            issues.append({'audit_id': identifier, 'issue': 'query_without_check_name'})
        if row['contact_type'] == '执行' and not set(methods) & {'mine', 'craft_grid', 'smelt'}:
            issues.append({'audit_id': identifier, 'issue': 'execution_without_semantic_action'})
        steps = [int(value) for match in re.finditer(r'第([\d、，,与和及到至\-–—~～ ]+)步', row['notes'])
                 for value in re.findall(r'\d+', match.group(1))]
        if any(step < 1 or step > len(events) for step in steps):
            issues.append({'audit_id': identifier, 'issue': 'note_step_out_of_range'})
    passed = validation['status'] == 'PASS' and not changes and not issues
    checks = {'status': 'PASS' if passed else 'NEEDS_REVIEW', 'validation': validation,
              'frozen_sample_IDs_match': set(old) == selected == {row['audit_id'] for row in rows},
              'immutable_cell_changes': changes, 'additional_issues': issues,
              'source_sha256': before, 'llm_calls': 0, 'new_episodes': 0, 'scorer_key_read': False}
    if {str(p): digest(p) for p in (UPLOAD, template, manifest_path)} != before:
        raise RuntimeError('an input changed while checking annotations')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'checks.json').write_text(json.dumps(checks, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    if not passed:
        print(json.dumps(checks, ensure_ascii=False, sort_keys=True))
        return 2
    with (OUT / 'human_annotations_v3.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=['audit_id', *ANNOTATION_FIELDS], lineterminator='\n')
        writer.writeheader()
        writer.writerows({field: row[field] for field in ['audit_id', *ANNOTATION_FIELDS]} for row in rows)
    report = [
        '# Completed v3 human annotation check', '',
        'Status: PASS. All 80 neutral IDs match the frozen sample, with no missing or duplicated IDs.',
        'The frozen template and sample manifest hashes match the original export records.',
        'All evidence, split parts, metadata and prev_* cells are unchanged.',
        'Full action sequences parse; action counts, submit boundaries, note-step references and',
        'basic label/action compatibility have no detected issues.', '',
        '## Annotation distribution', '',
        '| Field | Value | Count |', '| --- | --- | ---: |',
    ]
    for label, count in validation['contact_distribution'].items():
        report.append(f'| contact_type | {label} | {count} |')
    for label, count in validation['propagation_distribution_all'].items():
        report.append(f"| propagation | {label or '(blank: no contact)'} | {count} |")
    report.extend([
        '', 'All 76 no-contact rows leave propagation empty. All four contact rows have an allowed',
        'propagation value. All 80 notes are nonempty. Deprecated terminal columns are absent.', '',
        'The supplied human judgments are archived verbatim in human_annotations_v3.csv.',
        'The uploaded table and original files are unchanged. This verifies file integrity and',
        'internal consistency, not agreement with a scorer or independent gold correctness.',
        'No scorer key or scorer labels were read. Zero LLM calls and zero new episodes.', '',
    ])
    (OUT / 'REPORT.md').write_text('\n'.join(report))
    print(json.dumps({'status': 'PASS', 'rows': len(rows), 'immutable_cell_changes': 0,
                      'contact_distribution': validation['contact_distribution'],
                      'propagation_distribution': validation['propagation_distribution_all']},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
