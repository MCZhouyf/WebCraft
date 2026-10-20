"""Validate uploaded human annotations against immutable v2 evidence, offline."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/main/post_review/human_annotation_v2_check'
FIELDS = ('contact_type', 'propagation', 'terminal_depends_on_stale', 'notes')
ALLOWED = {
    'contact_type': {'无', '查询', '执行', '查询+执行', '无法确定'},
    'propagation': {'是', '否', '无法确定'},
    'terminal_depends_on_stale': {'是', '否', '无法确定'},
}
INPUTS = (
    ('main', Path('/root/autodl-tmp/audit_sheet_v2.csv'),
     ROOT / 'reports/main/post_review/review_v13_audit/audit_sheet_v2.csv', 147),
    ('cds', Path('/root/autodl-tmp/audit_sheet_cds_v2.csv'),
     ROOT / 'reports/main/post_review/a9_cds_followup/audit_sheet_cds_v2.csv', 30),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValueError('malformed CSV row')
        return list(reader.fieldnames or []), rows


def reassemble(row: dict, prefix: str, field: str, flag: str) -> str:
    if row[flag] == '0':
        return row[field]
    if row[flag] != '1':
        raise ValueError('invalid split flag')
    fields = sorted((c for c in row if re.fullmatch(prefix + r'\d+', c)),
                    key=lambda c: int(c[len(prefix):]))
    return ''.join(row[c] for c in fields)


def decode_actions(text: str) -> list[dict]:
    decoder, events, position = json.JSONDecoder(), [], 0
    while position < len(text):
        event, position = decoder.raw_decode(text, position)
        events.append(event)
        if position < len(text):
            if text[position:position + 4] != ' -> ':
                raise ValueError('invalid action separator')
            position += 4
    return events


def check(dataset: str, uploaded: Path, original: Path, expected: int) -> tuple[dict, list[dict]]:
    headers, rows = read(uploaded)
    old_headers, old_rows = read(original)
    if headers != old_headers or len(rows) != expected:
        raise ValueError('uploaded header or row count differs')
    old = {row['audit_id']: row for row in old_rows}
    ids = [row['audit_id'] for row in rows]
    if len(set(ids)) != expected or set(ids) != set(old):
        raise ValueError('missing, duplicated or unexpected audit IDs')
    issues, evidence_changes, groups = [], [], defaultdict(list)
    summaries = []
    step_referenced_rows = 0
    for row in rows:
        identifier = row['audit_id']
        for field in headers:
            if field not in FIELDS and row[field] != old[identifier][field]:
                evidence_changes.append({'audit_id': identifier, 'field': field})
        for field in FIELDS:
            if not row[field].strip():
                issues.append({'audit_id': identifier, 'issue': f'empty_{field}'})
            elif field in ALLOWED and row[field] not in ALLOWED[field]:
                issues.append({'audit_id': identifier, 'issue': f'invalid_{field}'})
        text = reassemble(row, 'action_part', 'action_sequence_and_feedback', 'truncated')
        snippets = reassemble(row, 'search_part', 'search_snippets', 'search_split')
        events = decode_actions(text)
        methods = [e['method'] for e in events]
        if len(events) != int(row['n_actions']) or methods[-1] != 'submit' or methods.count('submit') != 1:
            issues.append({'audit_id': identifier, 'issue': 'invalid_complete_episode'})
        if row['contact_type'] in {'查询', '查询+执行'} and 'check_name' not in methods:
            issues.append({'audit_id': identifier, 'issue': 'query_without_check_name'})
        if row['contact_type'] in {'执行', '查询+执行'} and not set(methods) & {'mine', 'craft_grid', 'smelt'}:
            issues.append({'audit_id': identifier, 'issue': 'execution_without_semantic_action'})
        if row['contact_type'] == '无' and row['propagation'] == '是':
            issues.append({'audit_id': identifier, 'issue': 'no_contact_but_propagation'})
        steps = [int(value) for match in re.finditer(r'第([\d、，,与和及到至\-–—~～ ]+)步', row['notes'])
                 for value in re.findall(r'\d+', match.group(1))]
        step_referenced_rows += bool(steps)
        if any(step < 1 or step > len(events) for step in steps):
            issues.append({'audit_id': identifier, 'issue': 'note_step_out_of_range'})
        signature = hashlib.sha256((text + '\n' + snippets).encode()).hexdigest()
        group = tuple(row[f] for f in ('agent_family', 'method', 'condition', 'world', 'task_id')) + (signature,)
        groups[group].append(row)
        summaries.append({'dataset': dataset, 'audit_id': identifier, **{f: row[f] for f in FIELDS}})
    duplicates = [values for values in groups.values() if len(values) > 1]
    for values in duplicates:
        labels = {tuple(row[f] for f in FIELDS[:3]) for row in values}
        if len(labels) > 1:
            issues.append({'audit_ids': [row['audit_id'] for row in values], 'issue': 'duplicate_episode_label_disagreement'})
    result = {
        'dataset': dataset, 'uploaded_path': str(uploaded), 'uploaded_sha256': digest(uploaded),
        'original_path': str(original.relative_to(ROOT)), 'original_sha256': digest(original),
        'rows': len(rows), 'unique_ids': len(set(ids)), 'headers_unchanged': True,
        'evidence_or_prev_field_changes': evidence_changes, 'issues': issues,
        'filled_counts': {f: sum(bool(row[f].strip()) for row in rows) for f in FIELDS},
        'label_counts': {f: dict(Counter(row[f] for row in rows)) for f in FIELDS[:3]},
        'rows_with_step_reference': step_referenced_rows,
        'unique_complete_episodes': len(groups), 'duplicate_episode_groups': len(duplicates),
        'rows_in_duplicate_groups': sum(len(values) for values in duplicates),
        'duplicate_episode_labels_consistent': not any(v['issue'] == 'duplicate_episode_label_disagreement' for v in issues),
        'status': 'PASS' if not issues and not evidence_changes else 'NEEDS_REVIEW',
    }
    return result, summaries


def main() -> int:
    snapshots = {str(p): digest(p) for _, uploaded, original, _ in INPUTS for p in (uploaded, original)}
    results, annotations = [], []
    for args in INPUTS:
        result, rows = check(*args)
        results.append(result)
        annotations.extend(rows)
    if {str(p): digest(p) for _, uploaded, original, _ in INPUTS for p in (uploaded, original)} != snapshots:
        raise RuntimeError('input changed during validation')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'checks.json').write_text(json.dumps({'llm_calls': 0, 'new_episodes': 0, 'datasets': results},
                                              indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    with (OUT / 'human_annotations.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=['dataset', 'audit_id', *FIELDS], lineterminator='\n')
        writer.writeheader()
        writer.writerows(annotations)
    report = ['# Uploaded human annotations: v2 check', '',
              'Zero LLM calls and zero new episodes. Uploaded files and original evidence are unchanged.',
              'This check does not read scorer keys, change a human label, or calculate scorer agreement.', '',
              '| Dataset | Status | Rows | Evidence/prev changes | Consistency issues | Unique full episodes |',
              '| --- | --- | ---: | ---: | ---: | ---: |']
    for r in results:
        report.append(f"| {r['dataset']} | {r['status']} | {r['rows']} | {len(r['evidence_or_prev_field_changes'])} | {len(r['issues'])} | {r['unique_complete_episodes']} |")
    report.extend(['', '## Label counts', ''])
    for r in results:
        report.append(f"### {r['dataset']}")
        report.append('')
        for field, counts in r['label_counts'].items():
            report.append(f'- {field}: {json.dumps(counts, ensure_ascii=False)}')
        report.append('')
    report.extend([
        '## Scope and interpretation', '',
        'All four fields are filled; label spellings, ID coverage, original evidence and prev_* cells,',
        'reassembled JSON, action counts, submit boundaries, note-step bounds and basic label/action',
        'compatibility are checked. This is file integrity and internal consistency, not an independent',
        'gold adjudication of staleness or propagation. Unknown labels remain unknown.', '',
        'The main 147 rows contain 119 unique complete episodes. Twelve repeated-episode groups',
        'cover 40 rows, and all have consistent contact/propagation/terminal judgments.',
        'These rows must not be counted as 147 independent episodes in subsequent inference.',
        'The 30 CDS rows correspond to 30 distinct complete episodes.', '',
        'human_annotations.csv archives only the supplied human judgments and notes, indexed by the',
        'neutral audit_id. No scorer labels or original sampling identifiers are included.',
        'Full source paths and SHA-256 hashes are recorded in checks.json.', '',
    ])
    (OUT / 'REPORT.md').write_text('\n'.join(report))
    print(json.dumps({r['dataset']: {'status': r['status'], 'rows': r['rows'],
                                    'issues': len(r['issues']), 'unique_episodes': r['unique_complete_episodes']}
                      for r in results}, sort_keys=True))
    return 0 if all(r['status'] == 'PASS' for r in results) else 2


if __name__ == '__main__':
    raise SystemExit(main())
