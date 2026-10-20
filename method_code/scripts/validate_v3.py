"""Validate completed v3 human annotations using only the supplied CSV."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

CONTACT = {'无', '查询', '执行'}
PROPAGATION = {'是', '否', '无法确定'}
ANNOTATION_FIELDS = ('contact_type', 'propagation', 'notes')


def validate_rows(rows: list[dict], expected_rows: int = 80) -> dict:
    issues = []
    if len(rows) != expected_rows:
        issues.append({'audit_id': '', 'field': 'rows', 'message': f'要求 {expected_rows} 行，实际 {len(rows)} 行'})
    ids = [row.get('audit_id', '') for row in rows]
    duplicate_ids = [identifier for identifier, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        issues.append({'audit_id': ','.join(duplicate_ids), 'field': 'audit_id', 'message': 'ID 重复'})
    for index, row in enumerate(rows, 1):
        identifier = row.get('audit_id', '')
        if not identifier.strip():
            issues.append({'audit_id': '', 'field': 'audit_id', 'message': f'第 {index} 行缺少 ID'})
        if None in row or any(value is None for value in row.values()):
            issues.append({'audit_id': identifier, 'field': 'CSV', 'message': 'CSV 列数不一致'})
        contact, propagation = row.get('contact_type', ''), row.get('propagation', '')
        if contact not in CONTACT:
            issues.append({'audit_id': identifier, 'field': 'contact_type', 'message': '只允许 无 / 查询 / 执行；不得留空'})
        if contact == '无':
            if propagation != '':
                issues.append({'audit_id': identifier, 'field': 'propagation', 'message': 'contact_type=无 时必须留空'})
        elif contact in CONTACT and propagation not in PROPAGATION:
            issues.append({'audit_id': identifier, 'field': 'propagation', 'message': '有接触时须填 是 / 否 / 无法确定'})
        if not str(row.get('notes') or '').strip():
            issues.append({'audit_id': identifier, 'field': 'notes', 'message': '备注不得留空；请写明依据或证据不足之处'})
        if not str(row.get('search_snippets') or '').strip():
            issues.append({'audit_id': identifier, 'field': 'search_snippets', 'message': '检索说明不得留空'})
        if not str(row.get('action_sequence_and_feedback') or '').strip():
            issues.append({'audit_id': identifier, 'field': 'action_sequence_and_feedback', 'message': '动作证据或拆列说明不得留空'})
        for field in ('agent_family', 'method', 'condition', 'world', 'task_id', 'n_actions'):
            if not str(row.get(field) or '').strip():
                issues.append({'audit_id': identifier, 'field': field, 'message': '原始必要字段缺失'})
        if row.get('truncated') not in {'0', '1'}:
            issues.append({'audit_id': identifier, 'field': 'truncated', 'message': '拆列标记应为 0 或 1'})
        if row.get('truncated') == '1' and not str(row.get('action_part1') or '').strip():
            issues.append({'audit_id': identifier, 'field': 'action_part1', 'message': '拆分动作缺少第一部分'})
        if row.get('search_split') == '1' and not str(row.get('search_part1') or '').strip():
            issues.append({'audit_id': identifier, 'field': 'search_part1', 'message': '拆分检索缺少第一部分'})
    contact_counts = Counter(row.get('contact_type', '') for row in rows)
    eligible = [row for row in rows if row.get('contact_type') in {'查询', '执行'}]
    return {
        'status': 'PASS' if not issues else 'NEEDS_REVIEW', 'rows': len(rows),
        'issues': issues, 'issue_count': len(issues),
        'contact_distribution': dict(contact_counts),
        'propagation_distribution_all': dict(Counter(row.get('propagation', '') for row in rows)),
        'propagation_eligible_rows': len(eligible),
        'propagation_distribution_among_contacts': dict(Counter(row.get('propagation', '') for row in eligible)),
        'blank_notes': sum(not str(row.get('notes') or '').strip() for row in rows),
        'scorer_key_read': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='校验填写完成的 80 条 v3 表；不读取 audit_key。')
    parser.add_argument('csv_file', type=Path)
    args = parser.parse_args()
    with args.csv_file.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = reader.fieldnames or []
    result = validate_rows(rows)
    if any(field in headers for field in ('terminal_depends_on_stale', 'prev_terminal', 'prev_terminal_depends_on_stale')):
        result['issues'].append({'audit_id': '', 'field': 'headers', 'message': 'v3 不应包含已废弃的终局标注列'})
        result['issue_count'] = len(result['issues'])
        result['status'] = 'NEEDS_REVIEW'
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
