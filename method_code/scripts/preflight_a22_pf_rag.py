"""Zero-call A22 development-input QA. Never reads held-out inputs or labels."""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import re
import resource
from datetime import datetime, timezone
from pathlib import Path

from arbiter.checkpoint import effective_available_bytes


ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
DEV_IDS = frozenset({
    'eff_007', 'fea_002', 'fea_009', 'pla_001', 'pla_003',
    'pla_008', 'pla_009', 'pla_011',
})
CONDITIONS = frozenset({'mixed', 'mismatched_old2new', 'mismatched_new2old'})
VERSION = re.compile(r'(?<![\d.])1\.\d{1,2}(?:\.\d{1,2})?(?!\d|\.\d)')
PUBLIC_FIELDS = frozenset({
    'text', 'source_site', 'page_title', 'revid', 'timestamp', 'chunk_id',
})


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_bundle(bundle: dict, filename: str) -> tuple[dict, list[dict], list[str]]:
    task = bundle.get('task_id')
    condition = bundle.get('condition')
    if task not in DEV_IDS or condition not in CONDITIONS:
        raise ValueError('non-development evidence is forbidden in A22 preflight')
    problems = []
    if bundle.get('reading_mode') != 'all_public':
        problems.append('reading_mode_mismatch')
    if bundle.get('claim_source') != 'precomputed_cache':
        problems.append('not_precomputed_cache')
    reader = bundle.get('reader', {})
    searches = reader.get('search_events', [])
    if reader.get('search_count') != len(searches) or len(searches) > 60:
        problems.append('search_count_mismatch_or_over_budget')
    hits = bundle.get('search_rows', [])
    if not hits:
        problems.append('no_search_rows')
    if any(set(hit) != PUBLIC_FIELDS for hit in hits):
        problems.append('search_field_contract')
    matches = []
    for index, hit in enumerate(hits):
        for field in sorted(PUBLIC_FIELDS):
            for match in VERSION.finditer(str(hit.get(field, ''))):
                matches.append({
                    'evidence_file': filename, 'task_id': task,
                    'condition': condition, 'hit_index': index,
                    'chunk_id': hit.get('chunk_id'), 'field': field,
                    'version_text': match.group(), 'start': match.start(),
                    'origin': 'preexisting_public_search_result',
                })
    return ({
        'evidence_file': filename, 'task_id': task, 'condition': condition,
        'search_count': reader.get('search_count'), 'search_rows': len(hits),
        'version_occurrences': len(matches),
        'version_hit_count': len({r['hit_index'] for r in matches}),
        'input_issues': ';'.join(problems),
    }, matches, problems)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def run(dev_dir: Path, output: Path, web: Path = WEB, *, allow_public_versions: bool = False) -> dict:
    # Inspect filenames before opening contents; do not traverse main artifacts.
    inputs = sorted(dev_dir.glob('*.json'))
    for path in inputs:
        pieces = path.stem.split('__')
        if len(pieces) != 3 or pieces[0] not in CONDITIONS or pieces[1] not in DEV_IDS:
            raise ValueError('unexpected filename in development evidence directory')
    expected = {(t, c) for t in DEV_IDS for c in CONDITIONS}
    observed = set()
    rows, matches, hashes, issues = [], [], [], []
    for path in inputs:
        bundle = json.loads(path.read_text())
        row, found, problems = audit_bundle(bundle, path.name)
        key = (row['task_id'], row['condition'])
        if key in observed:
            issues.append('duplicate_development_bundle')
        observed.add(key)
        rows.append(row)
        matches.extend(found)
        hashes.append({'path': str(path.relative_to(ROOT)), 'sha256': digest(path)})
        issues.extend(problems)
    if observed != expected:
        issues.append('development_evidence_coverage_mismatch')

    config = json.loads((ROOT / 'config/arbiter_hparams.json').read_text())
    compiler = config['compiler']
    dependencies = [compiler['schema_source'], *compiler['normalizer_sources'],
                    config['reader']['family_table_source']]
    dependency_rows = []
    for spec in dependencies:
        path = web / spec['path']
        actual = digest(path) if path.is_file() else None
        ok = actual == spec['sha256']
        dependency_rows.append({
            'path': spec['path'], 'expected_sha256': spec['sha256'],
            'actual_sha256': actual, 'matches': ok,
        })
        if not ok:
            issues.append('frozen_shared_compiler_or_reader_dependency_mismatch')

    if matches and not allow_public_versions:
        issues.append('literal_zero_version_string_QA_failed_on_public_snippets')
    report = {
        'status': 'BLOCKED_QA' if issues else 'INPUT_PREFLIGHT_PASS',
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'preregistration': 'prereg/a22_pf_rag_20261020.md',
        'preregistration_sha256': digest(ROOT / 'prereg/a22_pf_rag_20261020.md'),
        'development_bundles': len(rows), 'expected_bundles': 24,
        'development_tasks': len({r['task_id'] for r in rows}),
        'bundles_containing_version_strings': sum(r['version_occurrences'] > 0 for r in rows),
        'public_search_rows': sum(r['search_rows'] for r in rows),
        'public_search_rows_with_versions': sum(r['version_hit_count'] for r in rows),
        'version_occurrences': len(matches),
        'unique_chunks_with_versions': len({r['chunk_id'] for r in matches}),
        'version_fields': sorted({r['field'] for r in matches}),
        'issues': sorted(set(issues)), 'shared_dependencies': dependency_rows,
        'llm_calls': 0, 'new_episodes': 0, 'heldout_inputs_read': 0,
        'B_selected': None, 'fallback_selected': None,
        'public_text_clarification_applied': allow_public_versions,
        'limitations': [
            'Public text mentions are not evidence of policy use of a version mapping.',
            'This audits saved development-reader output, not new PF action logs.',
            'Input preflight alone does not establish runtime fairness or stability.',
        ],
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'input_hashes': hashes,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'preflight.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    write_csv(output / 'development_evidence_audit.csv', rows, [
        'evidence_file', 'task_id', 'condition', 'search_count', 'search_rows',
        'version_occurrences', 'version_hit_count', 'input_issues',
    ])
    write_csv(output / 'public_version_occurrences.csv', matches, [
        'evidence_file', 'task_id', 'condition', 'hit_index', 'chunk_id',
        'field', 'version_text', 'start', 'origin',
    ])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev-dir', type=Path,
                        default=ROOT / 'artifacts/p6_final_all_public/evidence')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'reports/main/post_review/a22_pf_rag')
    parser.add_argument('--allow-public-versions', action='store_true')
    args = parser.parse_args()
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise SystemExit('WAITING_RESOURCES: no inputs audited; retry after 600 seconds')
    with Path('/root/autodl-tmp/server.lock').open() as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if args.allow_public_versions and args.output.resolve() == (
                ROOT / 'reports/main/post_review/a22_pf_rag').resolve():
            raise SystemExit('clarified preflight must not overwrite the original report')
        report = run(args.dev_dir, args.output, allow_public_versions=args.allow_public_versions)
    print(json.dumps({k: report[k] for k in [
        'status', 'development_bundles', 'bundles_containing_version_strings',
        'version_occurrences', 'issues', 'llm_calls', 'new_episodes',
    ]}, sort_keys=True))


if __name__ == '__main__':
    main()
