"""A20: offline human agreement and static positive-case CDS checks."""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from arbiter.evaluation.consequence_htr import inventory_dependencies
from arbiter.evaluation.measurement_v3 import (
    event_inputs, predicted_products, prerequisite_ready, _inventory_before,
)
from scripts import analyze_a19_human_audit as a19
from scripts import analyze_cds_agents_20261017 as a8
from scripts.check_human_audit_v2 import decode_actions, reassemble
from scripts.export_human_audit_v3b import FrozenEvidence, cds_metadata, unnamespace
from scripts.validate_v3b import validate_rows

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'reports/main/post_review/a20_audit_cds_scoring_20261017'
PREREG = ROOT / 'prereg/a20_audit_cds_scoring_20261017.md'
INPUTS = ROOT / 'prereg/a20_audit_cds_inputs_20261017.json'
KEY = ROOT / 'reports/main/post_review/a9_cds_followup/audit_key_cds.csv'
CATEGORIES = ('prerequisite_loop', 'repeated_collection', 'shared_or_current_valid',
              'reasonable_verification', 'other')
Z = 1.959963984540054
SOURCES = {}
EXCEPTIONS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def track(path):
    path = Path(path)
    value = sha(path)
    if str(path) in SOURCES and SOURCES[str(path)] != value:
        raise ValueError('source_changed_during_analysis')
    SOURCES[str(path)] = value
    return path


def read_csv(path):
    return a19.read_csv(track(path))


def load_json(path):
    return json.loads(track(path).read_text())


def dump(name, value):
    a19.write_json(OUT / name, value)


def table(name, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else ['status'])
    a19.write_csv(OUT / name, fields, rows)


def js(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def note_exception(part, reason, **details):
    EXCEPTIONS.append({'part': part, 'reason': reason, **details})


def wilson(k, n):
    if not n:
        return '', ''
    if not 0 <= k <= n:
        raise ValueError('wilson_invalid_binomial_counts')
    p = k / n
    denominator = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denominator
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def cds_key():
    now = datetime.now(timezone.utc)
    entry = {'event': 'cds_key_open_intent', 'timestamp_utc': now.isoformat(),
             'timestamp_client': now.astimezone(ZoneInfo('America/Tijuana')).isoformat(),
             'prereg_commit': a19.git('log', '-1', '--format=%H', '--', str(PREREG.relative_to(ROOT))),
             'code_commit': a19.git('rev-parse', 'HEAD'), 'key_path': str(KEY)}
    with (OUT / 'key_access.jsonl').open('a') as handle:
        handle.write(json.dumps(entry) + '\n')
    data = KEY.read_bytes()
    SOURCES[str(KEY)] = hashlib.sha256(data).hexdigest()
    rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    entry.update(event='cds_key_open_completed', sha256=SOURCES[str(KEY)], rows=len(rows))
    with (OUT / 'key_access.jsonl').open('a') as handle:
        handle.write(json.dumps(entry) + '\n')
    return a19.indexed(rows, 'trajectory_id')


def normalize_contact(value):
    # The CDS key calls inventory-changing contact "action", unlike the main key.
    mapping = {**a19.CONTACT_MAP, 'action': '执行'}
    return a19.aggregate_contact([mapping[v] for v in value.split('+')])


def agreement():
    human = read_csv(Path('/root/autodl-tmp/audit_sheet_cds_v3.csv'))
    if validate_rows(human, 30)['status'] != 'PASS':
        raise ValueError('cds_human_validation_failed')
    a19.immutable_check(human, read_csv(ROOT / 'reports/main/post_review/human_audit_v3b/audit_sheet_cds_v3.csv'))
    key = cds_key()
    mapping = a19.indexed(read_csv(ROOT / 'reports/main/post_review/review_v13_audit/reexport_v2_private/audit_idmap_cds.csv'))
    if len(key) != 30 or set(key) != {r['trajectory_id'] for r in mapping.values()}:
        raise ValueError('cds_key_identity_mismatch')
    metadata = cds_metadata(human)
    groups = defaultdict(list)
    for row in human:
        meta = metadata[row['audit_id']]
        groups[(row['method'], row['condition'], meta['instance_id'])].append(row)
    episodes = []
    for identity, members in sorted(groups.items()):
        events = [key[mapping[r['audit_id']]['trajectory_id']] for r in members]
        contacts = {r['contact_type'] for r in members}
        props = {a19.human_propagation(r) for r in members}
        human_contact = next(iter(contacts)) if len(contacts) == 1 else '无法确定'
        human_prop = next(iter(props)) if len(props) == 1 else '无法确定'
        if len(members) > 1:
            note_exception('agreement', 'duplicate_CDS_episode_aggregated', instance_id=identity[2],
                           audit_ids=[r['audit_id'] for r in members])
        episodes.append({'cohort': 'cds', 'audit_id': ';'.join(r['audit_id'] for r in members),
                         'method': identity[0], 'condition': identity[1], 'instance_id': identity[2],
                         'world': members[0]['world'], 'task_id': members[0]['task_id'],
                         'event_count': len(events), 'human_contact': human_contact,
                         'scorer_contact': a19.aggregate_contact([normalize_contact(e['contact_type']) for e in events]),
                         'human_propagation': human_prop,
                         'scorer_propagation': a19.aggregate_propagation([a19.PROP_MAP[e['propagation']] for e in events]),
                         'human_notes': ' || '.join(r['notes'] for r in members)})
    table('cds_episode_alignment.csv', episodes)
    main = read_csv(a19.OUT / 'episode_alignment_80.csv')
    all_metrics = []
    for cohort, rows in (('main_80', main), ('cds_30', episodes), ('pooled_reference', main + episodes)):
        for metric in ('contact', 'propagation'):
            result = a19.binary_counts(rows, metric)
            for score in ('precision', 'recall'):
                k, n = result[f'{score}_numerator'], result[f'{score}_denominator']
                low, high = wilson(k, n)
                all_metrics.append({'cohort': cohort, 'metric': metric, 'statistic': score,
                                    'numerator': k, 'denominator': n, 'estimate': result[score],
                                    'wilson_low': low, 'wilson_high': high,
                                    'cohort_n': len(rows), 'eligible_n': result['n_eligible'],
                                    'human_undetermined': result['human_undetermined'],
                                    'scorer_undetermined': result['scorer_undetermined']})
            matrix = a19.confusion(rows, metric)
            table(f'{cohort}_{metric}_confusion.csv', matrix)
            counts = {'human_unresolved': result['human_undetermined'],
                      'scorer_unresolved': result['scorer_undetermined'],
                      'either_side_unresolved': result['excluded_any_undetermined']}
            for statistic, k in counts.items():
                low, high = wilson(k, len(rows))
                all_metrics.append({'cohort': cohort, 'metric': metric, 'statistic': statistic,
                                    'numerator': k, 'denominator': len(rows), 'estimate': k / len(rows),
                                    'wilson_low': low, 'wilson_high': high,
                                    'cohort_n': len(rows), 'eligible_n': len(rows),
                                    'human_undetermined': result['human_undetermined'],
                                    'scorer_undetermined': result['scorer_undetermined']})
        k = sum('无法确定' in (r['human_contact'], r['human_propagation']) for r in rows)
        low, high = wilson(k, len(rows))
        all_metrics.append({'cohort': cohort, 'metric': 'either_human_metric', 'statistic': 'human_unresolved',
                            'numerator': k, 'denominator': len(rows), 'estimate': k / len(rows),
                            'wilson_low': low, 'wilson_high': high, 'cohort_n': len(rows),
                            'eligible_n': len(rows), 'human_undetermined': k, 'scorer_undetermined': ''})
    table('human_agreement_wilson.csv', all_metrics)
    return {'status': 'REPORTED', 'cds_unique_episodes': len(episodes), 'main_episodes': len(main),
            'pooled_reference_n': len(episodes) + len(main)}


def rationale_category(notes):
    # Literal descriptive tags, not a new human or causal adjudication.
    cues = {
        'prerequisite_loop': ('前提打转', '重复缺料', '反复缺料', '循环缺料', '反复缺少', '重复缺少',
                              '反复缺工具', '缺工具循环', '缺燃料循环', '仍缺料'),
        'repeated_collection': ('为凑数量', '补足数量', '重复采集', '反复采集', '重复挖矿', '为数量'),
        'shared_or_current_valid': ('共享', '新旧均', '两个版本均', '来源可追溯', '真实批次',
                                    '实际采集', '实际制作', '实际制得', '实际制熔炉', '实采', '实产', '实制'),
        'reasonable_verification': ('合理验证', '探索性验证', '只查询', '仅查询', '仅检查', '核对', '验证后'),
    }
    matched = {k: [c for c in values if c in notes] for k, values in cues.items()}
    categories = [c for c in CATEGORIES if matched.get(c)]
    return (categories[0] if categories else 'other'), matched


def error_decomposition():
    main = read_csv(a19.OUT / 'episode_alignment_80.csv')
    cases = read_csv(a19.OUT / 'false_positive_cases.csv') + read_csv(a19.OUT / 'false_negative_cases.csv')
    expected = Counter({('contact', 'false_positive'): 10, ('propagation', 'false_positive'): 5,
                        ('propagation', 'false_negative'): 1})
    actual = Counter((r['metric'], r['case_type']) for r in cases)
    if actual != expected:
        note_exception('error_decomposition', 'A19_case_counts_differ', actual=js(dict((str(k), v) for k, v in actual.items())))
    for row in cases:
        category, cues = rationale_category(row['human_notes'])
        row['rationale_category'] = category
        row['matched_note_cues'] = js({k: v for k, v in cues.items() if v})
    table('a19_error_cases_categorized.csv', cases)
    counts = []
    examples = []
    for metric, kind in expected:
        subset = [r for r in cases if (r['metric'], r['case_type']) == (metric, kind)]
        for category in CATEGORIES:
            values = sorted((r for r in subset if r['rationale_category'] == category), key=lambda r: r['audit_id'])
            counts.append({'metric': metric, 'case_type': kind, 'category': category, 'n': len(values)})
            if values:
                examples.append({k: values[0][k] for k in
                                 ('metric', 'case_type', 'rationale_category', 'audit_id', 'human_notes', 'matched_note_cues')})
    table('a19_error_rationale_counts.csv', counts)
    table('a19_error_rationale_examples.csv', examples)
    strata = []
    for metric, kind in expected:
        for stratum in a19.PRIORITY:
            strata.append({'metric': metric, 'case_type': kind, 'stratum': stratum,
                           'n': sum(r['metric'] == metric and r['case_type'] == kind and r['stratum'] == stratum for r in cases)})
    table('a19_errors_by_stratum.csv', strata)
    condition_rows = []
    conditions = sorted({r['condition'] for r in main})
    for condition in (*conditions, 'matched_all', 'mismatched_all'):
        if condition.endswith('_all'):
            prefix = 'matched_' if condition == 'matched_all' else 'mismatched_'
            selected = [r for r in main if r['condition'].startswith(prefix)]
        else:
            selected = [r for r in main if r['condition'] == condition]
        ids = {r['audit_id'] for r in selected}
        eligible = [r for r in selected if '无法确定' not in (r['human_contact'], r['scorer_contact'])]
        negative = [r for r in eligible if r['human_contact'] == '无']
        fps = sum(r['audit_id'] in ids and r['metric'] == 'contact' and r['case_type'] == 'false_positive' for r in cases)
        condition_rows.append({'condition': condition, 'sample_n': len(selected),
                               'contact_determinate_n': len(eligible), 'human_negative_n': len(negative),
                               'contact_FP': fps, 'FP_per_sample': fps / len(selected) if selected else '',
                               'FP_per_human_negative': fps / len(negative) if negative else ''})
    table('a19_contact_fp_by_condition.csv', condition_rows)
    return {'status': 'REPORTED', 'cases': len(cases), 'distinct_episodes': len({r['audit_id'] for r in cases})}


def physical_inputs(event):
    if event.get('method') not in {'craft_grid', 'smelt'}:
        return set()
    return event_inputs(event)


def downstream_proof(events, index, predicted):
    projected = [{**e, 'result': e['result']['inventory']}
                 if e.get('method') == 'inventory' and isinstance(e.get('result'), dict)
                 and isinstance(e['result'].get('inventory'), list) else e for e in events]
    flow = inventory_dependencies(projected)
    evidence = []
    for later in range(index + 1, len(events)):
        required = physical_inputs(events[later]) & predicted
        if not required:
            continue
        fifo = sorted({name for consumer, name, _ in flow.get(index, []) if consumer == later and name in required})
        before = _inventory_before(projected, later)
        absent = sorted(name for name in required if before is not None and before[name] <= 0)
        if fifo or absent:
            evidence.append({'event_index': later, 'method': events[later]['method'],
                             'params': events[later].get('params', {}), 'FIFO_from_stale_action': fifo,
                             'required_item_absent_from_inventory': absent})
    return evidence


def static_outcome(evidence, world, event, nodes):
    method, params = event['method'], event.get('params', {})
    if method == 'craft_grid':
        layout = params.get('layout')
        if not isinstance(layout, list) or len(layout) != 9 or any(x is not None and not isinstance(x, str) for x in layout):
            raise ValueError('unsupported_craft_layout')
        normalized = {'layout': [unnamespace(x) for x in layout]}
    elif method == 'smelt':
        normalized = {'item': unnamespace(params.get('input', params.get('item')))}
        if not normalized['item']:
            raise ValueError('missing_smelt_input')
    elif method == 'mine':
        block = nodes.get(str(params.get('nodeId')), params.get('block', params.get('node_type')))
        normalized = {'block': unnamespace(block)}
        if normalized['block'] not in evidence.sources:
            raise ValueError('unidentified_resource_node')
    elif method == 'check_name':
        normalized = {'name': unnamespace(params.get('name'))}
    else:
        raise ValueError('unsupported_static_mechanism')
    return evidence.outcome(world, {'method': method, 'params': normalized})


def licensed(outcome, method):
    if method == 'check_name':
        return bool(outcome.get('resolvable'))
    return bool(outcome.get('product'))


def target_same_prediction(outcome, assertion, method):
    if method == 'check_name':
        return outcome.get('resolvable') is assertion.get('exists')
    product = outcome.get('product')
    if method == 'mine' and isinstance(product, list):
        return product[0] in {unnamespace(value) for value in assertion.get('drops', [])}
    return (product == unnamespace(assertion.get('output'))
            and (assertion.get('count') is None or outcome.get('count') == assertion['count']))


def mechanism_category(*, assertion, target_outcome, outcomes, method, dependency,
                       target_truth, comparison_truth, world_consistent):
    if not world_consistent or (target_truth is True and comparison_truth is False):
        return 'iii', 'demonstrable_reference_world_mismatch'
    if licensed(target_outcome, method) and target_same_prediction(target_outcome, assertion, method):
        return 'ii', 'full_target_truth_licenses_same_concrete_action_and_prediction'
    if dependency:
        return 'i', 'downstream_physical_input_has_FIFO_or_absent_inventory_stale_prediction_witness'
    if licensed(target_outcome, method):
        return 'ii', 'target_licensed_action_without_stale_prediction_dependency'
    if any(licensed(value, method) and target_same_prediction(value, assertion, method) for value in outcomes.values()):
        return 'i', 'target_does_not_license_action_but_another_world_licenses_stale_prediction'
    return 'iv', 'no_static_proof_of_stale_specificity_or_target_legality'


def primary_category(categories):
    return next(value for value in ('i', 'iv', 'iii', 'ii') if value in categories)


def corrected_bounds(categories):
    return int('i' in categories), int(bool(set(categories) & {'i', 'iv'}))


def contact_audit():
    saved = read_csv(a8.DEFAULT_OUTPUT / 'episode_metrics.csv')
    selected = [r for r in saved if r['method'] == 'react' and r['condition'] in {'K+N', 'K+S'}]
    expected_keys = {(r['condition'], r['instance_id']) for r in selected}
    if len(selected) != len(expected_keys) or len(expected_keys) != 104:
        raise ValueError('A8_pair_scope_not_identifiable')
    for condition, expected in (('K+N', 14), ('K+S', 24)):
        actual = sum(int(r['discriminative_stale_contact']) for r in selected if r['condition'] == condition)
        if actual != expected:
            note_exception('contact_audit', 'frozen_positive_count_differs', condition=condition, expected=expected, actual=actual)
    raw = {}
    for path in sorted(a8.DEFAULT_INPUT.rglob('episodes.jsonl')):
        for record in a8.jsonl(track(path)):
            if record['method'] == 'react' and (record['condition'], record['instance_id']) in expected_keys:
                key = (record['condition'], record['instance_id'])
                if key in raw:
                    raise ValueError('duplicate_original_A8_episode_not_selectable')
                raw[key] = record
    evidence = FrozenEvidence()
    nodes = a8.load_resource_nodes(track(WEB / 'harness/config/arena.json'))
    manifest = {r['instance_id']: r for r in load_json(WEB / 'cds_materials/cds_manifest.json')['rows']}
    annotations = read_csv(a8.DEFAULT_OUTPUT / 'stale_action_audit.csv')
    originals = defaultdict(list)
    for annotation in annotations:
        if annotation['episode_method'] == 'react' and annotation['condition'] in {'K+N', 'K+S'} and annotation['discriminative'] == '1':
            originals[(annotation['condition'], annotation['instance_id'])].append(annotation)
    action_rows, mechanism_rows, episode_rows = [], [], []
    for metric in sorted(selected, key=lambda r: (r['condition'], r['instance_id'])):
        key = (metric['condition'], metric['instance_id'])
        record = raw.get(key)
        positive = int(metric['discriminative_stale_contact'])
        if not positive:
            episode_rows.append({**{f: metric[f] for f in ('condition', 'instance_id', 'family_id', 'target_world')},
                                 'original_contact': 0, 'corrected_lower': 0, 'corrected_upper': 0,
                                 'primary_category': 'original_negative_not_reaudited', 'positive_actions': 0})
            continue
        if record is None or not originals[key]:
            note_exception('contact_audit', 'missing_positive_episode_evidence', condition=key[0], instance_id=key[1])
            episode_rows.append({**{f: metric[f] for f in ('condition', 'instance_id', 'family_id', 'target_world')},
                                 'original_contact': positive, 'corrected_lower': 0, 'corrected_upper': 1,
                                 'primary_category': 'iv', 'positive_actions': ''})
            continue
        meta = manifest[record['instance_id']]
        material = load_json(WEB / meta['visible_path'])
        evaluator = load_json(WEB / meta['evaluator_path'])
        if sha(WEB / meta['visible_path']) != meta['visible_sha256'] or sha(WEB / meta['evaluator_path']) != meta['evaluator_sha256']:
            raise ValueError('CDS_material_hash_mismatch')
        focal_stale = sum(r.get('role') == 'focal_stale' for r in evaluator['conditions'][metric['condition']])
        events = a8.jsonl(track(ROOT / record['action_log']))
        target, comparison = material['target_world'], material['comparison_world']
        consistent = record['target_world'] == target == metric['target_world']
        categories = []
        for annotation in originals[key]:
            index = int(annotation['event_index'])
            event = events[index]
            candidates = ast.literal_eval(annotation['stale_mechanism_ids'])
            candidate_categories = []
            for cid in candidates:
                conflict = evidence.conflicts[cid]
                assertion = conflict['assertion']
                predicted = predicted_products(assertion)
                dependency = downstream_proof(events, index, predicted)
                try:
                    outcomes = {v: static_outcome(evidence, v, event, nodes) for v in evidence.truth}
                    category, reason = mechanism_category(
                        assertion=assertion, target_outcome=outcomes[target], outcomes=outcomes,
                        method=event['method'], dependency=dependency,
                        target_truth=conflict['truth_vector'].get(target),
                        comparison_truth=conflict['truth_vector'].get(comparison), world_consistent=consistent)
                except (KeyError, ValueError, TypeError, IndexError) as exc:
                    outcomes = {}
                    category, reason = 'iv', 'static_check_unidentifiable_' + type(exc).__name__
                candidate_categories.append(category)
                mechanism_rows.append({'condition': metric['condition'], 'instance_id': metric['instance_id'],
                                       'event_index': index, 'method': event['method'], 'mechanism_id': cid,
                                       'assertion': js(assertion), 'target_world': target, 'comparison_world': comparison,
                                       'target_truth': conflict['truth_vector'].get(target),
                                       'comparison_truth': conflict['truth_vector'].get(comparison),
                                       'all_catalog_truth': js(conflict['truth_vector']),
                                       'scorer_world_argument': record['target_world'],
                                       'world_reference_consistent': consistent,
                                       'static_outcomes_by_world': js(outcomes),
                                       'downstream_dependency_witness': js(dependency),
                                       'category': category, 'reason': reason})
            category = primary_category(candidate_categories)
            categories.append(category)
            action_rows.append({'condition': metric['condition'], 'instance_id': metric['instance_id'],
                                'task_id': record['task_id'], 'target_world': target, 'comparison_world': comparison,
                                'event_index': index, 'semantic_index': annotation['semantic_index'],
                                'method': event['method'], 'params': js(event.get('params', {})),
                                'world_feedback': js(event.get('result')), 'success': event.get('success'),
                                'catalog_stale_mechanism_ids': ';'.join(candidates),
                                'original_referenced_at': annotation['referenced_at'],
                                'referenced_actions': js([{'event_index': int(i), 'method': events[int(i)]['method'],
                                                          'params': events[int(i)].get('params', {})}
                                                         for i in ast.literal_eval(annotation['referenced_at'])]),
                                'prerequisite_ready': prerequisite_ready(events, index),
                                'injected_fact_count': len(material['conditions'][metric['condition']]),
                                'injected_focal_stale_fact_count': focal_stale,
                                'category': category, 'action_log': record['action_log']})
        lower, upper = corrected_bounds(categories)
        episode_rows.append({**{f: metric[f] for f in ('condition', 'instance_id', 'family_id', 'target_world')},
                             'original_contact': positive, 'corrected_lower': lower, 'corrected_upper': upper,
                             'primary_category': primary_category(categories), 'positive_actions': len(categories)})
    table('a8_positive_actions_audit.csv', action_rows)
    table('a8_positive_mechanisms_audit.csv', mechanism_rows)
    table('a8_corrected_episode_scores.csv', episode_rows)
    summary = []
    for condition in ('K+N', 'K+S'):
        group = [r for r in episode_rows if r['condition'] == condition]
        for category in ('i', 'ii', 'iii', 'iv'):
            summary.append({'condition': condition, 'category': category,
                            'positive_episodes': sum(r['primary_category'] == category for r in group),
                            'positive_actions': sum(r['condition'] == condition and r['category'] == category for r in action_rows),
                            'original_positive_n': sum(r['original_contact'] for r in group), 'condition_n': len(group)})
    table('a8_positive_category_counts.csv', summary)
    left = [r for r in episode_rows if r['condition'] == 'K+S']
    right = [r for r in episode_rows if r['condition'] == 'K+N']
    contrasts = []
    for name, a, b in (('frozen_original', 'original_contact', 'original_contact'),
                        ('corrected_difference_lower_bound', 'corrected_lower', 'corrected_upper'),
                        ('corrected_difference_upper_bound', 'corrected_upper', 'corrected_lower')):
        la = [{**r, 'value': r[a]} for r in left]
        rb = [{**r, 'value': r[b]} for r in right]
        point, low, high, n = a8.bootstrap_difference(la, rb, 'value')
        contrasts.append({'estimand': name, 'contrast': 'K+S - K+N', 'point': point,
                          'ci_low': low, 'ci_high': high, 'paired_n': n,
                          'draws': 10000, 'seed': 20261017,
                          'correction_scope': 'original_positive_cases_only_negatives_not_reaudited'})
    table('a8_corrected_contact_contrast.csv', contrasts)
    return {'status': 'REPORTED', 'positive_actions': len(action_rows),
            'positive_episodes': sum(r['original_contact'] for r in episode_rows),
            'corrected_point_identified': all(r['corrected_lower'] == r['corrected_upper'] for r in episode_rows)}


def pct(value):
    return 'NA' if value == '' else f'{float(value) * 100:.2f}%'


def pp(value):
    return 'NA' if value == '' else f'{float(value) * 100:+.2f}'


def report(parts):
    lines = ['# A20 audit completion and CDS scorer checks', '',
             'Pre-computation rules/input commit: `e9dfc2c`. Zero LLM calls and zero new episodes.',
             'Original A8/A19 tables and human/key files are unchanged; all corrections are additional analyses.', '',
             '## Human agreement (Wilson 95% intervals)', '',
             '| Cohort | Metric | Statistic | Numerator / denominator | Estimate | 95% Wilson |',
             '|---|---|---|---|---:|---|']
    path = OUT / 'human_agreement_wilson.csv'
    if path.exists():
        for row in a19.read_csv(path):
            if row['statistic'] in {'precision', 'recall'}:
                lines.append(f'| {row["cohort"]} | {row["metric"]} | {row["statistic"]} | '
                             f'{row["numerator"]}/{row["denominator"]} | {pct(row["estimate"])} | '
                             f'[{pct(row["wilson_low"])}, {pct(row["wilson_high"])}] |')
    lines += ['', '| Cohort | Human unresolved metric | Count | Rate | 95% Wilson |',
              '|---|---|---:|---:|---|']
    if path.exists():
        for row in a19.read_csv(path):
            if row['statistic'] == 'human_unresolved' and row['metric'] in {'contact', 'propagation'}:
                lines.append(f'| {row["cohort"]} | {row["metric"]} | {row["numerator"]}/{row["denominator"]} | '
                             f'{pct(row["estimate"])} | [{pct(row["wilson_low"])}, {pct(row["wilson_high"])}] |')
    lines += ['', 'The 80 and 30 cohorts are primary; pooling is reference-only. Unresolved cells are separate, never negatives.',
              'The 30 CDS judgments come from the original K+S stratified audit sample, not a random sample across all CDS conditions.',
              'Wilson intervals condition on determinate pairs and are not design-adjusted population estimates.',
              'Full confusion matrices and all human/scorer unresolved rates are exported.', '',
              '## Main A19 errors', '',
              'a19_error_cases_categorized.csv preserves all 10 contact FP, five propagation FP and one propagation FN.',
              'Categories are literal rationale-cue summaries, not new human labels or causal adjudications.',
              'No forced example is supplied for a zero-count category; all matched cues and full notes remain inspectable.',
              'a19_errors_by_stratum.csv and a19_contact_fp_by_condition.csv include the relevant sample/negative denominators.', '']
    path = OUT / 'a19_contact_fp_by_condition.csv'
    if path.exists():
        for row in a19.read_csv(path):
            lines.append(f'- {row["condition"]}: contact FP={row["contact_FP"]}, sampled={row["sample_n"]}, '
                         f'determinate={row["contact_determinate_n"]}, human-negative={row["human_negative_n"]}.')
    lines += ['', '### Rationale counts', '',
              '| Metric / error | Prerequisite loop | Repeated collection | Shared/current-valid | Reasonable verification | Other |',
              '|---|---:|---:|---:|---:|---:|']
    path = OUT / 'a19_error_rationale_counts.csv'
    if path.exists():
        categories = a19.read_csv(path)
        for metric, kind in (('contact', 'false_positive'), ('propagation', 'false_positive'), ('propagation', 'false_negative')):
            counts = {r['category']: r['n'] for r in categories if r['metric'] == metric and r['case_type'] == kind}
            lines.append('| ' + metric + ' / ' + kind + ' | ' + ' | '.join(counts.get(c, '0') for c in CATEGORIES) + ' |')
    lines += ['', '### Original-stratum counts', '',
              '| Metric / error | Propagation stratum | Query | Execution | Nonspecific | No-contact abstention |',
              '|---|---:|---:|---:|---:|---:|']
    path = OUT / 'a19_errors_by_stratum.csv'
    if path.exists():
        strata = a19.read_csv(path)
        for metric, kind in (('contact', 'false_positive'), ('propagation', 'false_positive'), ('propagation', 'false_negative')):
            counts = {r['stratum']: r['n'] for r in strata if r['metric'] == metric and r['case_type'] == kind}
            lines.append('| ' + metric + ' / ' + kind + ' | ' + ' | '.join(counts.get(c, '0') for c in a19.PRIORITY) + ' |')
    lines += ['', 'One lexicographically selected example per observed rationale category/error is in a19_error_rationale_examples.csv.',
              'IMPLEMENTATION_CHECK.md records a cue-parsing ambiguity: same materials alone did not establish old/new equivalence.',
              'The initial draft category is retained in a parallel column and initial_rationale_implementation/, with one changed note tag.',
              'No human/scorer label, error membership, agreement metric, or corrected A8 contrast changed in that implementation check.']
    lines += ['', 'Raw count balance is a property of this selected audit sample, not proof that benchmark condition effects are or are not biased.', '',
              '## A8 original positive-case mechanism audit', '',
              'Categories: i stale-specific; ii target-licensed/non-specific; iii reference-world defect; iv unresolved/other.',
              'Every action and mechanism has a row with parameters, feedback, both world truths, and static full-truth witnesses.',
              'A name query is not material consumption and cannot alone establish the FIFO origin of a mined product.',
              'Mechanism validity is separate from missing-input/tool/fuel execution errors.', '',
              '| Condition | Category | Positive episodes | Positive actions |', '|---|---|---:|---:|']
    path = OUT / 'a8_positive_category_counts.csv'
    if path.exists():
        for row in a19.read_csv(path):
            lines.append(f'| {row["condition"]} | {row["category"]} | {row["positive_episodes"]} | {row["positive_actions"]} |')
    lines += ['', '| Contrast version | Difference (pp) | Family-cluster 95% interval (pp) | Pairs |', '|---|---:|---|---:|']
    path = OUT / 'a8_corrected_contact_contrast.csv'
    if path.exists():
        for row in a19.read_csv(path):
            lines.append(f'| {row["estimand"]} | {pp(row["point"])} | [{pp(row["ci_low"])}, {pp(row["ci_high"])}] | {row["paired_n"]} |')
    lines += ['', 'Identified-difference bounds are not themselves confidence intervals. Where both correction bounds coincide, the contrast is point-identified.',
              'The frozen negatives were not comprehensively reaudited. Corrected results only remove proven positive-case misclassification.',
              'Absence of stale injected facts is not proof that an action was caused by the model prior. No causal attribution is made.',
              'Known A8 knowledge-retention limitations remain; no new prompt or agent call was made.', '',
              '## Exceptions and provenance', '', js(parts), '',
              'See exceptions.json for every data/specification mismatch and any item withheld from reporting.',
              'source_integrity.json records source hashes, original-key preservation, and key_access.jsonl records every CDS key opening.',
              'No second annotator or kappa is added in this task. No remote push, service changes, GPU use, or WebCraft edits.', '']
    (OUT / 'REPORT.md').write_text('\n'.join(lines))


def main():
    if (OUT / 'analysis_status.json').exists():
        raise RuntimeError('A20_closed_no_overwrite')
    OUT.mkdir(exist_ok=True)
    lock = load_json(INPUTS)
    changed = [path for path, expected in lock['inputs'].items() if not Path(path).is_file() or sha(Path(path)) != expected]
    for path in changed:
        note_exception('inputs', 'registered_source_changed', path=path)
    parts = {}
    for name, function in (('agreement', agreement), ('error_decomposition', error_decomposition), ('contact_audit', contact_audit)):
        try:
            parts[name] = function()
        except Exception as exc:
            # Explicitly withhold the affected part; continue the independent parts.
            parts[name] = {'status': 'NOT_REPORTED', 'exception_type': type(exc).__name__}
            note_exception(name, 'part_unidentifiable', exception_type=type(exc).__name__, detail=str(exc)[:300])
    mismatches = [path for path, expected in SOURCES.items() if not Path(path).is_file() or sha(Path(path)) != expected]
    integrity = {'status': 'PASS' if not mismatches else 'SOURCE_CHANGED',
                 'input_hashes': SOURCES, 'changed_during_read': mismatches,
                 'registered_input_changes': changed, 'llm_calls': 0, 'new_episodes': 0,
                 'webcraft_files_written': 0, 'original_cds_key_unchanged': str(KEY) in SOURCES and sha(KEY) == SOURCES[str(KEY)]}
    dump('exceptions.json', EXCEPTIONS)
    dump('source_integrity.json', integrity)
    dump('analysis_status.json', parts)
    report(parts)
    print(js({'parts': parts, 'exceptions': len(EXCEPTIONS), 'integrity': integrity['status']}))


if __name__ == '__main__':
    main()
