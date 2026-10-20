"""Validate v3b/CDS-v3 human review forms without loading any scorer key."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

CONTACT = {'无', '查询', '执行', '无法确定'}
PROPAGATION = {'是', '否', '无法确定'}
EVIDENCE = ('audit_id', 'agent_family', 'method', 'condition', 'world', 'task_id',
            'n_actions', 'action_sequence_and_feedback', 'search_snippets', 'stale_claim_text',
            'valid_claim_text', 'stale_predicted_output', 'discriminating_action', 'search_ownership')


def validate_rows(rows: list[dict], expected_rows: int, draft: bool = False) -> dict:
    issues = []
    if len(rows) != expected_rows:
        issues.append({'audit_id': '', 'field': 'rows', 'message': f'要求 {expected_rows} 行，实际 {len(rows)} 行'})
    duplicates = [identifier for identifier, count in Counter(r.get('audit_id', '') for r in rows).items() if count > 1]
    if duplicates:
        issues.append({'audit_id': ','.join(duplicates), 'field': 'audit_id', 'message': 'ID 重复'})
    for row in rows:
        identifier = row.get('audit_id', '')
        def issue(field, message):
            issues.append({'audit_id': identifier, 'field': field, 'message': message})
        if None in row or any(v is None for v in row.values()):
            issue('CSV', '列数不一致')
        for field in EVIDENCE:
            if not str(row.get(field) or '').strip():
                issue(field, '证据或必要字段不得为空')
        for field in row:
            if 'terminal_depends_on_stale' in field or field == 'trajectory_id':
                issue(field, '不应导出废弃列或原轨迹 ID')
        contact, propagation = row.get('contact_type', ''), row.get('propagation', '')
        if not (draft and contact == '') and contact not in CONTACT:
            issue('contact_type', '只允许 无 / 查询 / 执行 / 无法确定；完成后不得留空')
        if contact in {'查询', '执行'}:
            if propagation not in PROPAGATION:
                issue('propagation', '有接触时须填 是 / 否 / 无法确定')
        elif propagation:
            issue('propagation', 'contact_type 为 无 / 无法确定（或未填写）时必须留空')
        if not draft and not str(row.get('notes') or '').strip():
            issue('notes', '请注明依据；无法确定时注明缺少何种证据')
        if row.get('truncated') not in {'0', '1'}:
            issue('truncated', '应为 0 或 1；1 只表示无损拆列')
        if row.get('truncated') == '1' and not row.get('action_part1'):
            issue('action_part1', '缺少动作拆列')
        if row.get('search_split') == '1' and not row.get('search_part1'):
            issue('search_part1', '缺少片段拆列')
    return {
        'status': ('DRAFT_OK' if draft else 'PASS') if not issues else 'NEEDS_REVIEW',
        'rows': len(rows), 'issues': issues,
        'contact_distribution': dict(Counter(row.get('contact_type', '') for row in rows)),
        'propagation_distribution': dict(Counter(row.get('propagation', '') for row in rows)),
        'unfilled_contact_rows': sum(not row.get('contact_type') for row in rows),
        'scorer_key_read': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='四值人工复核表校验；不读取 audit_key。')
    parser.add_argument('csv_file', type=Path)
    parser.add_argument('--expected-rows', type=int, required=True, choices=(30, 37))
    parser.add_argument('--draft', action='store_true', help='只查导出结构，允许尚未填写的标注')
    args = parser.parse_args()
    csv.field_size_limit(10_000_000)
    with args.csv_file.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    result = validate_rows(rows, args.expected_rows, args.draft)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result['issues'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
