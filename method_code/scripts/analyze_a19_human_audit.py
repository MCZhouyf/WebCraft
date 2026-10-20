"""Offline A19 episode-level agreement, under the committed pre-unseal rules."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.validate_v3b import validate_rows

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/main/post_review/a19_human_audit_20261017'
RULES = ROOT / 'prereg/a19_human_audit_20261017.md'
LOCK = ROOT / 'prereg/a19_human_audit_inputs_20261017.json'
KEY = ROOT / 'reports/main/post_review/review_v13_audit/audit_key.csv'
CONTACTS = ('无', '查询', '执行', '无法确定')
PROPS = ('否', '是', '无法确定')
PRIORITY = ('propagation', 'query', 'execution', 'nonspecific', 'S4_no_contact_abstention')
QUOTAS = (8, 19, 17, 23, 13)
CONTACT_MAP = {'none': '无', 'query': '查询', 'execution': '执行',
               'undetermined': '无法确定', **{s: s for s in CONTACTS}}
PROP_MAP = {'yes': '是', 'no': '否', 'undetermined': '无法确定',
            **{s: s for s in PROPS}}
METADATA = ('agent_family', 'method', 'condition', 'world', 'task_id')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    csv.field_size_limit(10_000_000)
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def indexed(rows, field='audit_id'):
    result = {row[field]: row for row in rows}
    if len(result) != len(rows) or '' in result:
        raise RuntimeError('duplicate_or_empty_identity')
    return result


def aggregate_contact(labels):
    if not labels or any(value not in CONTACTS for value in labels):
        raise ValueError('invalid_contact_labels')
    for value in ('执行', '查询', '无法确定'):
        if value in labels:
            return value
    return '无'


def aggregate_propagation(labels):
    if not labels or any(value not in PROPS for value in labels):
        raise ValueError('invalid_propagation_labels')
    if '是' in labels:
        return '是'
    return '无法确定' if '无法确定' in labels else '否'


def human_propagation(row):
    if row['contact_type'] == '无法确定':
        return '无法确定'
    if row['contact_type'] == '无':
        if row['propagation']:
            raise ValueError('no_contact_must_have_blank_propagation')
        return '否'
    if row['contact_type'] not in CONTACTS or row['propagation'] not in PROPS:
        raise ValueError('invalid_human_labels')
    return row['propagation']


def immutable_check(actual, template):
    left, right = indexed(actual), indexed(template)
    if left.keys() != right.keys():
        raise RuntimeError('annotation_IDs_changed')
    if any(set(left[k]) != set(right[k]) for k in left):
        raise RuntimeError('annotation_columns_changed')
    changes = [(k, f) for k in left for f in left[k]
               if f not in {'contact_type', 'propagation', 'notes'} and left[k][f] != right[k][f]]
    if changes:
        raise RuntimeError('immutable_annotation_evidence_changed')


def binary_counts(rows, metric):
    human, scorer = f'human_{metric}', f'scorer_{metric}'
    positives = {'查询', '执行'} if metric == 'contact' else {'是'}
    selected = [r for r in rows if r[human] != '无法确定' and r[scorer] != '无法确定']
    counts = Counter()
    for row in selected:
        h, s = row[human] in positives, row[scorer] in positives
        counts['TP' if h and s else 'FP' if s else 'FN' if h else 'TN'] += 1
    tp, fp, fn, tn = (counts[k] for k in ('TP', 'FP', 'FN', 'TN'))
    return {'metric': metric, 'n_total': len(rows), 'n_eligible': len(selected),
            'human_undetermined': sum(r[human] == '无法确定' for r in rows),
            'scorer_undetermined': sum(r[scorer] == '无法确定' for r in rows),
            'excluded_any_undetermined': len(rows) - len(selected),
            'TP': tp, 'FP': fp, 'FN': fn, 'TN': tn,
            'precision_numerator': tp, 'precision_denominator': tp + fp,
            'precision': tp / (tp + fp) if tp + fp else '',
            'recall_numerator': tp, 'recall_denominator': tp + fn,
            'recall': tp / (tp + fn) if tp + fn else ''}


def confusion(rows, metric):
    values = CONTACTS if metric == 'contact' else PROPS
    counts = Counter((r[f'human_{metric}'], r[f'scorer_{metric}']) for r in rows)
    return [{'human': h, **{s: counts[(h, s)] for s in values},
             'row_total': sum(counts[(h, s)] for s in values)} for h in values]


def key_access(prereg_commit):
    if git('status', '--porcelain', '--', str(RULES.relative_to(ROOT)), str(LOCK.relative_to(ROOT))):
        raise RuntimeError('preregistration_or_input_lock_not_clean')
    previous = [json.loads(line) for line in (OUT / 'key_access.jsonl').read_text().splitlines()]
    opened = [r for r in previous if r['event'] == 'key_open_completed']
    if not opened or opened[0]['prereg_commit'] != prereg_commit:
        raise RuntimeError('missing_registered_initial_unseal')
    now = datetime.now(timezone.utc)
    entry = {'event': 'key_open_intent', 'purpose': 'registered_episode_aggregation',
             'timestamp_utc': now.isoformat(),
             'timestamp_client': now.astimezone(ZoneInfo('America/Tijuana')).isoformat(),
             'prereg_commit': prereg_commit, 'code_commit': git('rev-parse', 'HEAD'),
             'key_path': str(KEY)}
    with (OUT / 'key_access.jsonl').open('a') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')
    data = KEY.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != opened[0]['key_sha256']:
        raise RuntimeError('key_changed_since_initial_unseal')
    csv.field_size_limit(10_000_000)
    records = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    entry.update(event='key_open_completed', key_sha256=checksum, rows=len(records))
    with (OUT / 'key_access.jsonl').open('a') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')
    return records, checksum, opened[0]


def prepare_human(inputs):
    paths = {name: Path(record['path']) for name, record in inputs.items()}
    revisions = read_csv(paths['main_revision'])
    cds = read_csv(paths['cds_revision'])
    for values, n in ((revisions, 37), (cds, 30)):
        if validate_rows(values, n)['status'] != 'PASS':
            raise RuntimeError('human_annotation_validation_failed')
    immutable_check(revisions, read_csv(paths['revision_template']))
    immutable_check(cds, read_csv(paths['cds_template']))
    base = read_csv(paths['main_v3'])
    immutable_check(base, read_csv(paths['main_template']))
    selected = json.loads(paths['sample_manifest'].read_text())
    original = indexed(base)
    revised = indexed(revisions)
    if len(original) != 80 or set(original) != set(selected['selected_audit_ids']):
        raise RuntimeError('main_sample_scope_mismatch')
    if not set(revised) <= set(original):
        raise RuntimeError('revision_outside_frozen_sample')
    remaining = [r for k, r in original.items() if k not in revised]
    if len(remaining) != 43 or Counter(r['contact_type'] for r in remaining) != Counter({'无': 39, '查询': 1, '执行': 3}):
        raise RuntimeError('unchanged_43_judgments_mismatch')
    merged = {k: revised.get(k, r) for k, r in original.items()}
    for row in merged.values():
        human_propagation(row)
    return merged, selected, paths


def case_labels(row, metric):
    human, scorer = row[f'human_{metric}'], row[f'scorer_{metric}']
    if '无法确定' in (human, scorer):
        return 'undetermined'
    positive = {'查询', '执行'} if metric == 'contact' else {'是'}
    if scorer in positive and human not in positive:
        return 'false_positive'
    if human in positive and scorer not in positive:
        return 'false_negative'
    if metric == 'contact' and scorer != human:
        return 'subtype_disagreement'
    return ''


def main():
    if (OUT / 'metrics.csv').exists():
        raise RuntimeError('a19_results_already_exist_no_overwrite')
    lock = json.loads(LOCK.read_text())
    inputs = lock['inputs']
    if any(digest(Path(r['path'])) != r['sha256'] for r in inputs.values()):
        raise RuntimeError('registered_inputs_changed')
    merged, sample, paths = prepare_human(inputs)
    revision_ids = {row['audit_id'] for row in read_csv(paths['main_revision'])}
    for path, expected in sample['source_sha256'].items():
        if digest(Path(path)) != expected:
            raise RuntimeError('original_sampling_source_changed')
    prereg_commit = git('log', '-1', '--format=%H', '--', str(RULES.relative_to(ROOT)))
    key_rows, key_sha, unseal = key_access(prereg_commit)
    key = indexed(key_rows, 'trajectory_id')
    idmap = indexed(read_csv(paths['idmap']))
    if len(key) != 147 or set(key) != {r['trajectory_id'] for r in idmap.values()}:
        raise RuntimeError('original_147_event_key_alignment_failed')
    history = {r['trajectory_id']: r['stratum']
               for r in json.loads(paths['sampling_history'].read_text())}
    aliases = sample['representative_aliases']
    all_aliases = [a for values in aliases.values() for a in values]
    if len(aliases) != 119 or len(all_aliases) != len(set(all_aliases)) or set(all_aliases) != set(idmap):
        raise RuntimeError('119_unique_episodes_do_not_partition_147_events')
    lineages, episodes = [], []
    for identifier, human in sorted(merged.items()):
        ids = aliases[identifier]
        trajectory_ids = [idmap[a]['trajectory_id'] for a in ids]
        events = []
        for alias, trajectory in zip(ids, trajectory_ids):
            event = key[trajectory]
            if event['contact_type'] not in CONTACT_MAP or event['propagation'] not in PROP_MAP:
                raise RuntimeError('unrecognized_scorer_label_do_not_guess')
            normalized = {'contact': CONTACT_MAP[event['contact_type']],
                          'propagation': PROP_MAP[event['propagation']]}
            events.append(normalized)
            lineages.append({'audit_id': identifier, 'event_audit_id': alias,
                             'trajectory_id': trajectory, 'original_event_stratum': history[trajectory],
                             'scorer_contact': normalized['contact'],
                             'scorer_propagation': normalized['propagation']})
        stratum = history[idmap[identifier]['trajectory_id']]
        episodes.append({'audit_id': identifier, **{f: human[f] for f in METADATA},
                         'stratum': stratum,
                         'annotation_source': 'v3b_revision' if identifier in revision_ids else 'v3_unchanged',
                         'event_count': len(events), 'event_aliases': ';'.join(ids),
                         'original_event_ids': ';'.join(trajectory_ids),
                         'human_contact': human['contact_type'],
                         'scorer_contact': aggregate_contact([e['contact'] for e in events]),
                         'human_propagation': human_propagation(human),
                         'scorer_propagation': aggregate_propagation([e['propagation'] for e in events]),
                         'human_notes': human['notes']})
    strata_counts = Counter(r['stratum'] for r in episodes)
    if strata_counts != Counter(dict(zip(PRIORITY, QUOTAS))):
        raise RuntimeError('registered_strata_counts_changed')
    metrics, stratum_metrics = [], []
    cases = []
    for scope in ('all', *PRIORITY):
        subset = episodes if scope == 'all' else [r for r in episodes if r['stratum'] == scope]
        for metric in ('contact', 'propagation'):
            summary = {'scope': scope, **binary_counts(subset, metric)}
            (metrics if scope == 'all' else stratum_metrics).append(summary)
            matrix = confusion(subset, metric)
            write_csv(OUT / f'{metric}_confusion_{scope}.csv', list(matrix[0]), matrix)
    for row in episodes:
        for metric in ('contact', 'propagation'):
            kind = case_labels(row, metric)
            if kind:
                cases.append({'metric': metric, 'case_type': kind, **row})
    unresolved = {'episodes': 80,
                  'contact_undetermined': sum(r['human_contact'] == '无法确定' for r in episodes),
                  'propagation_undetermined': sum(r['human_propagation'] == '无法确定' for r in episodes),
                  'either_undetermined': sum('无法确定' in (r['human_contact'], r['human_propagation']) for r in episodes)}
    unresolved.update({k + '_rate': unresolved[k] / 80 for k in
                       ('contact_undetermined', 'propagation_undetermined', 'either_undetermined')})
    write_csv(OUT / 'episode_alignment_80.csv', list(episodes[0]), episodes)
    write_csv(OUT / 'scorer_event_lineage.csv', list(lineages[0]), lineages)
    write_csv(OUT / 'metrics.csv', list(metrics[0]), metrics)
    write_csv(OUT / 'stratum_metrics.csv', list(stratum_metrics[0]), stratum_metrics)
    case_fields = ['metric', 'case_type', *list(episodes[0])]
    write_csv(OUT / 'disagreement_and_unresolved_cases.csv', case_fields, cases)
    write_csv(OUT / 'false_positive_cases.csv', case_fields, [r for r in cases if r['case_type'] == 'false_positive'])
    write_csv(OUT / 'false_negative_cases.csv', case_fields, [r for r in cases if r['case_type'] == 'false_negative'])
    write_json(OUT / 'unresolved_rates.json', unresolved)
    integrity = {'status': 'PASS', 'episodes': len(episodes),
                 'original_event_rows': 147, 'original_unique_episodes': 119,
                 'selected_event_rows': len(lineages), 'selected_unique_episodes': len(episodes),
                 'strata': dict(strata_counts), 'prereg_commit': prereg_commit,
                 'initial_key_unseal_client': unseal['timestamp_client'],
                 'initial_key_unseal_utc': unseal['timestamp_utc'],
                 'original_key_sha256': key_sha,
                 'original_key_unchanged': digest(KEY) == key_sha,
                 'inputs_unchanged': all(digest(Path(r['path'])) == r['sha256'] for r in inputs.values()),
                 'scorer_unit': 'any_event_positive_aggregated_to_full_episode',
                 'cds_validation_only_not_pooled': True,
                 'second_annotator': 'not_arranged_author_confirmed', 'cohens_kappa': None,
                 'llm_calls': 0, 'new_episodes': 0, 'webcraft_files_written': 0}
    if not integrity['original_key_unchanged'] or not integrity['inputs_unchanged']:
        raise RuntimeError('read_only_input_integrity_failed')
    write_json(OUT / 'integrity.json', integrity)
    render_report(metrics, stratum_metrics, unresolved, integrity, episodes, cases)
    print(json.dumps({'metrics': metrics, 'unresolved': unresolved,
                      'selected_events': len(lineages), 'integrity': 'PASS'}, ensure_ascii=False))


def percent(value):
    return 'NA' if value == '' else f'{100 * value:.2f}%'


def render_report(metrics, strata, unresolved, integrity, episodes, cases):
    report = ['# A19 human audit: episode-level agreement', '',
              f'Pre-unseal rule/input commit: `{integrity["prereg_commit"]}`.',
              f'First key unseal: {integrity["initial_key_unseal_client"]} (America/Tijuana); '
              f'{integrity["initial_key_unseal_utc"]} (UTC).', '',
              'Both uploaded tables passed validate_v3b.py before key access. All evidence cells and IDs match their frozen templates.',
              'The main cohort is 80 unique full episodes, a stratified subset of 119 episodes underlying 147 original event rows.',
              f'These selected episodes map to {integrity["selected_event_rows"]} original sampled event rows; every alias was included.',
              'The 37 revisions supersede their old placeholders; the other 43 judgments are retained (39 no-contact, four contact).',
              'CDS 30 is validation-only and not pooled. The CDS scorer key was not opened.', '',
              '## Main metrics', '',
              '| Metric | Eligible / 80 | TP / FP / FN / TN | Precision | Recall |',
              '|---|---:|---|---:|---:|']
    for row in metrics:
        report.append(f'| {row["metric"]} | {row["n_eligible"]} / 80 | '
                      f'{row["TP"]} / {row["FP"]} / {row["FN"]} / {row["TN"]} | '
                      f'{row["precision_numerator"]}/{row["precision_denominator"]} = {percent(row["precision"])} | '
                      f'{row["recall_numerator"]}/{row["recall_denominator"]} = {percent(row["recall"])} |')
    report += ['', 'Contact-positive means query or execution. Human unresolved cases are excluded per metric, not negative.',
               'Subtype disagreement is listed separately and is not a binary contact FP/FN.',
               'Human no-contact entails no propagation; unresolved contact entails unresolved propagation.', '',
               '## Contact confusion: human rows, scorer columns', '',
               '| Human \\ Scorer | 无 | 查询 | 执行 | 无法确定 | Total |',
               '|---|---:|---:|---:|---:|---:|']
    for row in confusion(episodes, 'contact'):
        report.append('| ' + ' | '.join(str(row[k]) for k in ('human', *CONTACTS, 'row_total')) + ' |')
    report += ['', '## Propagation confusion: human rows, scorer columns', '',
               '| Human \\ Scorer | 否 | 是 | 无法确定 | Total |', '|---|---:|---:|---:|---:|']
    for row in confusion(episodes, 'propagation'):
        report.append('| ' + ' | '.join(str(row[k]) for k in ('human', *PROPS, 'row_total')) + ' |')
    report += ['', '## Unresolved judgments', '',
               f'Contact: {unresolved["contact_undetermined"]}/80 = {percent(unresolved["contact_undetermined_rate"])}.',
               f'Propagation: {unresolved["propagation_undetermined"]}/80 = {percent(unresolved["propagation_undetermined_rate"])}.',
               f'Either: {unresolved["either_undetermined"]}/80 = {percent(unresolved["either_undetermined_rate"])}.', '',
               '## Original sampling strata', '',
               '| Stratum | Episodes | Contact unresolved | Contact P / R | Propagation unresolved | Propagation P |',
               '|---|---:|---:|---|---:|---:|']
    for label in PRIORITY:
        contact = next(r for r in strata if r['scope'] == label and r['metric'] == 'contact')
        prop = next(r for r in strata if r['scope'] == label and r['metric'] == 'propagation')
        report.append(f'| {label} | {contact["n_total"]} | {contact["human_undetermined"]} | '
                      f'{percent(contact["precision"])} / {percent(contact["recall"])} | '
                      f'{prop["human_undetermined"]} | {percent(prop["precision"])} |')
    report += ['', 'All per-stratum TP/FP/FN/TN, denominators, and zero-filled matrices are exported.',
               'NA means a zero denominator, not perfect agreement or zero performance.', '',
               '## Scope and limitations', '',
               'The scorer reference is the original saved audit_key, not a newly rerun or repaired classifier.',
               'The deprecated terminal_depends_on_stale key column is ignored.',
               'Episodes are the unit because humans reviewed full trajectories and propagation depends on later actions;',
               'repeated event samples from one episode cannot be treated as independent annotations.',
               'These are unweighted, selected-cohort agreement estimates, not benchmark-wide accuracy or prevalence.',
               'Human judgments are the reference for FP/FN labels, not independently verified gold truth.',
               'Unresolved cases remain visible in matrices and case files; conditional precision does not cover them.',
               'The author confirmed that no second independent annotator is arranged. Cohen kappa is NOT_AVAILABLE;',
               'repeat review rounds are not treated as independent annotators. No double-annotation sheet was issued.', '',
               '## Files and integrity', '',
               'episode_alignment_80.csv: one-to-one human/key episode alignment and original event aliases.',
               'scorer_event_lineage.csv: every selected original key event before aggregation.',
               'false_positive_cases.csv / false_negative_cases.csv: complete appendix candidate lists.',
               'disagreement_and_unresolved_cases.csv additionally includes subtype disagreements and unresolved cases.',
               'Original annotation files, scorer key, and registered inputs are unchanged. WebCraft is read-only.',
               'Zero LLM calls; zero new episodes; no GPU or service manipulation; no remote push.', '',
               '## Reproduction', '', '```bash',
               'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. CUDA_VISIBLE_DEVICES= nice -n 15 python -m scripts.analyze_a19_human_audit',
               '```', 'The calculation refuses to overwrite an existing metrics.csv. Every key access is logged.', '']
    (OUT / 'REPORT.md').write_text('\n'.join(report))


if __name__ == '__main__':
    main()
