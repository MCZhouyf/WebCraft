"""Offline A14: saved-prompt retention, prefix timing, and CDS related evidence."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np

from arbiter.evaluation.external_scoring import ACTION_METHODS, _matches
from arbiter.evaluation.measurement_v3 import classify_actions_v3, intrinsic_discriminative

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
BASE = ROOT / 'reports/main/post_review/cds_agents_20261017'
DEFAULT_OUT = ROOT / 'reports/main/post_review/a14_cds_retention/final'
CONDITIONS = ('K', 'K+S', 'S', 'empty', 'K+N')
METHOD_CONDITIONS = {'direct_llm': ('K', 'K+S', 'empty'), 'react': CONDITIONS}
PIPELINE_METHODS = ('parametric', 'probe_then_parametric', 'naive_rag', 'majority_rag',
                    'truthfinder_rag', 'verify_all', 'arbiter_shell_naive', 'arbiter', 'oracle', 'probe_then_rag')
SEED, DRAWS = 20261017, 10000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Inputs:
    def __init__(self):
        self.hashes = {}

    def text(self, path):
        path = Path(path)
        raw = path.read_bytes()
        self.hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        return raw.decode('utf-8-sig')

    def json(self, path):
        return json.loads(self.text(path))

    def lines(self, path):
        return [json.loads(line) for line in self.text(path).splitlines() if line.strip()]

    def csv(self, path):
        return list(csv.DictReader(self.text(path).splitlines()))

    def verify(self):
        return [path for path, expected in self.hashes.items() if sha(path) != expected]


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def public_facts(value):
    found = []
    if isinstance(value, dict):
        if all(isinstance(value.get(key), str) for key in ('mechanism fact', 'source', 'timestamp')):
            found.append({key: value[key] for key in ('mechanism fact', 'source', 'timestamp')})
        for child in value.values():
            found.extend(public_facts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(public_facts(child))
    return found


def strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in strings(child)]
    return []


def norm(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.casefold().replace('minecraft:', '')))


def semantic_trace(fact, payload, assertion):
    entries = [payload.get('observation'), *payload.get('trace', [])]
    subject = assertion.get('output', assertion.get('block', assertion.get('item', '')))
    other = set()
    def collect(value):
        if isinstance(value, str):
            other.add(value.removeprefix('minecraft:'))
        elif isinstance(value, list):
            for child in value:
                collect(child)
    for key in ('grid', 'ingredients', 'drops', 'layout'):
        collect(assertion.get(key))
    other.discard(subject)
    for entry in entries:
        if entry is None:
            continue
        texts = strings(entry)
        if any(fact['mechanism fact'] in text for text in texts):
            return 'exact_fact_text_without_full_provenance'
        # Inventory-only observations do not establish a mechanism trace.
        if isinstance(entry, list) or (isinstance(entry, dict) and set(entry) <= {'inventory'}):
            continue
        combined = norm(canonical(entry))
        if not subject or norm(subject) not in combined:
            continue
        name_check = assertion.get('kind') == 'name' and any(term in combined for term in ('check name', 'identifier'))
        if name_check:
            return 'subject_and_name_check'
        if not any(norm(item) in combined for item in other):
            continue
        if re.search(r'\b(?:craft\w*|recipe\w*|mak\w*|made|smelt\w*|drop\w*|mining|mine\w*|name\w*|exists)\b', combined):
            return 'subject_input_or_output_and_mechanism_trace'
    return ''


def retention(expected, payload, mechanisms):
    actual = Counter(canonical(fact) for fact in public_facts(payload))
    remaining = actual.copy()
    rows = []
    for index, fact in enumerate(expected, 1):
        key = canonical(fact)
        if remaining[key]:
            remaining[key] -= 1
            label, reason = 'full', 'exact_text_source_timestamp'
        else:
            assertion = mechanisms.get(hashlib.sha256(fact['mechanism fact'].encode()).hexdigest(), {})
            reason = semantic_trace(fact, payload, assertion)
            label = 'semantic_trace_only' if reason else 'no_trace'
        rows.append({'fact_index': index, 'fact_sha256': hashlib.sha256(key.encode()).hexdigest(),
                     'retention_class': label, 'reason': reason or 'no_registered_trace'})
    counts = Counter(row['retention_class'] for row in rows)
    unique = set(canonical(fact) for fact in expected)
    return rows, {'expected_entries': len(expected), 'full_entries': counts['full'],
                  'semantic_trace_only_entries': counts['semantic_trace_only'], 'no_trace_entries': counts['no_trace'],
                  'expected_unique_triples': len(unique), 'full_unique_triples': len(unique & set(actual)),
                  'full_fraction': counts['full'] / len(expected) if expected else '',
                  'all_full': int(counts['full'] == len(expected)), 'vacuous': int(not expected)}


def frozen_parser(source):
    tree = ast.parse(source)
    names = {'EXTERNAL_METHODS', '_ACTIONS', '_ARGUMENTS', 'ModelOutputError',
             '_parse', '_validate_args', 'public_system_prompt'}
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            selected.append(node)
    namespace = {'json': json, 're': re, 'Any': Any}
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<frozen pure response helpers>', 'exec'), namespace)
    return namespace


def request_pairs(log, method):
    result = []
    for index, row in enumerate(log):
        if row.get('record_type') != 'full_prompt' or row.get('call_type') != f'external_{method}':
            continue
        response = None
        for following in log[index + 1:]:
            # The audited client can nest a canary request inside an action call.
            if following.get('record_type') == 'full_prompt' and following.get('call_type') == row['call_type']:
                break
            if following.get('record_type') == 'full_response' and following.get('call_type') == row['call_type']:
                response = following
                break
        result.append((index, row, response))
    return result


def reconstruct(pairs, boundary_payload, events, task, method, helpers):
    api_trace = boundary_payload['episode_trace']
    if len(api_trace) != len(events):
        raise ValueError('boundary_trace_raw_event_length_difference')
    for index, (entry, event) in enumerate(zip(api_trace, events)):
        observation = entry['observation']
        if index == 0 and isinstance(observation, dict) and 'diagnostic_mechanism_facts' in observation:
            observation = observation['inventory']
        if entry['action'] != event['method'] or observation != event['result']:
            raise ValueError('boundary_trace_raw_event_result_difference')
    public_task = {'goal': {'item': str(task['target']['item']), 'count': int(task['target']['count'])},
                   'initial_inventory': task['standard_items'] + task.get('task_supplements', [])}
    observation, trace, cursor, cost = api_trace[0]['observation'], [], 1, 0
    mapping, checks = {}, []
    for step, (record_index, request, response) in enumerate(pairs, 1):
        payload = {'task': public_task, 'observation': observation,
                   'trace': trace[-(2 if method == 'direct_llm' else 8):]}
        messages = [{'role': 'system', 'content': helpers['public_system_prompt'](method)},
                    {'role': 'user', 'content': json.dumps(payload, sort_keys=True)}]
        equal = messages == request['messages']
        checks.append({'request_step': step, 'prompt_record_index': record_index,
                       'reconstruction_equal': int(equal),
                       'reconstructed_messages_sha256': hashlib.sha256(canonical(messages).encode()).hexdigest(),
                       'saved_messages_sha256': hashlib.sha256(canonical(request['messages']).encode()).hexdigest()})
        if not equal:
            raise ValueError('saved_request_reconstruction_difference')
        if response is None:
            raise ValueError('missing_saved_response')
        try:
            if response.get('finish_reason') == 'length':
                raise helpers['ModelOutputError']('completion_truncated')
            action = helpers['_parse'](response['content'])
        except helpers['ModelOutputError'] as exc:
            observation = {'tool_error': str(exc), 'retry': 'Return one complete action object using the documented fields.'}
            trace.append({'invalid_model_output': True, 'observation': observation})
            continue
        next_cost = action['args']['n'] if action['action'] == 'smelt' else int(action['action'] in ACTION_METHODS)
        if cost + next_cost > 90:
            if step != len(pairs):
                raise ValueError('requests_after_action_budget_break')
            continue
        entry = api_trace[cursor]
        expected_args = {'result': action['args']} if action['action'] == 'submit' else action['args']
        if entry['action'] != action['action'] or entry['args'] != expected_args:
            raise ValueError('parsed_response_api_trace_difference')
        mapping[cursor] = step
        observation = entry['observation']
        trace.append({'action': action, 'observation': observation})
        cost += next_cost
        cursor += 1
    return mapping, checks


def prefix_state(events, mapping, prefix, target, total_requests):
    inventory, attained, success, submitted = Counter(), False, False, False
    for index, event in enumerate(events):
        if index != 0 and (index not in mapping or mapping[index] > prefix):
            continue
        if event['method'] == 'inventory' and isinstance(event['result'], list):
            inventory = Counter({row['name'].removeprefix('minecraft:'): int(row['count']) for row in event['result']})
        for row in event.get('item_flow', {}).get('consumed', []):
            inventory[row['name'].removeprefix('minecraft:')] -= int(row['count'])
        for row in event.get('item_flow', {}).get('produced', []):
            inventory[row['name'].removeprefix('minecraft:')] += int(row['count'])
        enough = inventory[target['item']] >= target['count']
        attained |= enough
        if event['method'] == 'submit':
            submitted = True
            success |= enough and event['params'].get('result', {}).get('feasible') is True
    return {'prefix_success': int(success), 'prefix_goal_attained': int(attained),
            'prefix_censored': int(prefix < total_requests and not submitted),
            'prefix_model_submit_observed': int(submitted)}


def interval(values, clusters, *, contrast, unit='family', contact_positives=None):
    values = np.asarray(values, dtype=float)
    n = len(values)
    result = {'estimate': float(values.mean()) if n else '', 'ci95_low': '', 'ci95_high': '',
              'n': n, 'bootstrap_unit': unit, 'bootstrap_draws': DRAWS, 'bootstrap_seed': SEED,
              'ci_status': 'OK'}
    grouped = defaultdict(list)
    for value, cluster in zip(values, clusters):
        grouped[str(cluster)].append(value)
    result['independent_units'] = len(grouped)
    reasons = []
    if n < 10:
        reasons.append('fewer_than_10_eligible_instances')
    if unit == 'family':
        if len(grouped) < 5:
            reasons.append('fewer_than_5_families')
        if contrast and sum(values != 0) < 5:
            reasons.append('fewer_than_5_discordant_pairs')
        elif not contrast and (sum(values > 0) < 5 or sum(values == 0) < 5):
            reasons.append('fewer_than_5_positive_or_negative_instances')
    if contact_positives is not None and contact_positives < 5:
        reasons.append('fewer_than_5_contact_positive_episodes')
    if reasons:
        result['ci_status'] = ';'.join(reasons)
        return result
    sums = np.asarray([sum(grouped[key]) for key in sorted(grouped)])
    sizes = np.asarray([len(grouped[key]) for key in sorted(grouped)])
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(sums), size=(DRAWS, len(sums)))
    estimates = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
    result.update(ci95_low=float(np.quantile(estimates, .025)), ci95_high=float(np.quantile(estimates, .975)))
    return result


def describe(values, prefix):
    return {f'{prefix}_min': min(values) if values else '', f'{prefix}_median': median(values) if values else '',
            f'{prefix}_max': max(values) if values else '', f'{prefix}_mean': mean(values) if values else ''}


class Analysis:
    def __init__(self, output):
        self.output, self.inputs, self.exceptions = output, Inputs(), []
        self.inputs.text(Path(__file__))
        self.inputs.text(ROOT / 'prereg/a14_cds_retention_20261017.md')
        self.inputs.text(ROOT / 'prereg/a14_cds_retention_correction_20261017.md')
        self.inputs.text(Path('/root/autodl-tmp/server.lock'))
        self.manifest = self.inputs.json(WEB / 'cds_materials/cds_manifest.json')['rows']
        self.materials, self.private = {}, {}
        for entry in self.manifest:
            instance = entry['instance_id']
            for field, digest_field, destination in [('visible_path', 'visible_sha256', self.materials),
                                                       ('evaluator_path', 'evaluator_sha256', self.private)]:
                path = WEB / entry[field]
                destination[instance] = self.inputs.json(path)
                if sha(path) != entry[digest_field]:
                    self.issue('material', instance, 'frozen_hash_mismatch', severity='not_reported')
        self.tasks = {row['id']: row for row in self.inputs.lines(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
        self.helpers = frozen_parser(self.inputs.text(ROOT / 'src/arbiter/evaluation/external_agents.py'))
        for path in ['src/arbiter/evaluation/cds_agents.py', 'scripts/run_cds_agents_20261017.py',
                     'src/arbiter/evaluation/measurement_v3.py', 'src/arbiter/evaluation/external_scoring.py',
                     'src/arbiter/evaluation/consequence_htr.py']:
            self.inputs.text(ROOT / path)

    def issue(self, item, identifier, detail, severity='qualification'):
        self.exceptions.append({'item': item, 'identifier': identifier, 'detail': detail, 'severity': severity})

    def llm(self):
        metrics = {(row['method'], row['condition'], row['instance_id']): row
                   for row in self.inputs.csv(BASE / 'episode_metrics.csv')}
        annotations = defaultdict(list)
        for row in self.inputs.csv(BASE / 'stale_action_audit.csv'):
            annotations[(row['episode_method'], row['condition'], row['instance_id'])].append(row)
        indexed = {}
        for path in sorted((ROOT / 'artifacts/post_review/cds_agents_20261017').rglob('episodes.jsonl')):
            for record in self.inputs.lines(path):
                key = (record['method'], record['condition'], record['instance_id'])
                if key in indexed:
                    self.issue('A_coverage', canonical(key), 'duplicate_key', 'not_reported')
                    continue
                indexed[key] = record
        expected_keys = {(method, condition, instance) for method, conditions in METHOD_CONDITIONS.items()
                         for condition in conditions for instance in self.materials}
        if set(indexed) != expected_keys:
            self.issue('A_coverage', 'all', f'expected_416_got_{len(indexed)}', 'qualification')
        episode_rows, requests, facts, boundary_rows, mapping_rows = [], [], [], [], []
        for key, record in sorted(indexed.items()):
            method, condition, instance = key
            meta = {'method': method, 'condition': condition, 'instance_id': instance,
                    'family_id': record['family_id'], 'task_id': record['task_id'], 'world': record['target_world']}
            expected = self.materials[instance]['conditions'][condition]
            mechanism_map = {row['text_sha256']: row['assertion'] for row in self.private[instance]['conditions'][condition]}
            log = self.inputs.lines(ROOT / record['prompt_log'])
            events = self.inputs.lines(ROOT / record['action_log'])
            pairs = request_pairs(log, method)
            total = len(pairs)
            if total != record['action_model_calls']:
                self.issue('A_request_count', canonical(key), 'saved_requests_differ_from_action_model_calls')
            if not 1 <= total <= 40:
                self.issue('A_request_count', canonical(key), 'outside_registered_1_to_40')
            checks = []
            prefix, first_loss, recovery = 0, False, 0
            for step, (log_index, request, _) in enumerate(pairs, 1):
                payload = json.loads(request['messages'][1]['content'])
                # Full matching scans all decoded messages; trace proxy uses only user evidence.
                fact_rows, counts = retention(expected, payload, mechanism_map)
                checks.append(counts)
                if not first_loss and counts['all_full']:
                    prefix = step
                elif not counts['all_full']:
                    first_loss = True
                elif first_loss:
                    recovery += 1
                request_row = {**meta, 'request_step': step, 'total_requests': total,
                               'prompt_record_index': log_index, **counts}
                requests.append(request_row)
                facts.extend({**meta, 'request_step': step, **fact} for fact in fact_rows)
            boundary = [row for row in log if row.get('record_type') == 'full_prompt' and row.get('call_type') == 'cds_boundary']
            mapping, reconstruction_ok = {}, False
            boundary_payload = None
            if len(boundary) == 1:
                boundary_payload = json.loads(boundary[0]['messages'][1]['content'])
                context = {k: v for k, v in boundary_payload.items() if k != 'candidates'}
                _, counts = retention(expected, context, mechanism_map)
                boundary_rows.append({**meta, **counts, 'candidates_removed': True})
                try:
                    mapping, reconstruction_checks = reconstruct(pairs, boundary_payload, events,
                                                                 self.tasks[record['task_id']], method, self.helpers)
                    reconstruction_ok = True
                    mapping_rows.extend({**meta, **row} for row in reconstruction_checks)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    self.issue('A_reconstruction', canonical(key), str(exc) if type(exc) is ValueError else type(exc).__name__, 'not_reported_alignment_outcomes')
            else:
                self.issue('A_boundary', canonical(key), f'expected_one_got_{len(boundary)}', 'not_reported_boundary_and_alignment')
            old = metrics.get(key)
            if old is None:
                self.issue('A_old_metrics', canonical(key), 'missing_frozen_export', 'not_reported_outcomes')
                continue
            disc = [row for row in annotations[key] if int(row['discriminative'])]
            contact_valid = all(int(row['event_index']) < len(events)
                                and row['method'] == events[int(row['event_index'])]['method'] for row in disc)
            if int(bool(disc)) != int(old['discriminative_stale_contact']):
                contact_valid = False
            if not contact_valid:
                self.issue('A_contact_annotation', canonical(key), 'frozen_event_or_episode_mismatch', 'not_reported_prefix_contact')
            state = prefix_state(events, mapping, prefix, self.tasks[record['task_id']]['target'], total) if reconstruction_ok else {
                field: '' for field in ('prefix_success', 'prefix_goal_attained', 'prefix_censored', 'prefix_model_submit_observed')}
            chosen = [row for row in disc if int(row['event_index']) in mapping and mapping[int(row['event_index'])] <= prefix]
            references_after = sum(any(mapping.get(int(i), total + 1) > prefix for i in ast.literal_eval(row['referenced_at']))
                                   for row in chosen)
            episode_rows.append({**meta, 'true_feasible': int(old['true_feasible']), 'total_requests': total,
                                 'prefix_requests': prefix, 'half_retained': int(2 * prefix >= total),
                                 'expected_entries': len(expected), 'vacuous': int(not expected),
                                 'recovered_complete_requests_after_first_loss': recovery,
                                 'request_reconstruction_ok': int(reconstruction_ok),
                                 'tool_events': len(events), 'semantic_events': sum(e['method'] in ACTION_METHODS for e in events),
                                 'full_success': int(old['success']),
                                 'full_contact': int(old['discriminative_stale_contact']),
                                 'prefix_contact': int(bool(chosen)) if reconstruction_ok and contact_valid else '',
                                 'prefix_contact_events': len(chosen) if reconstruction_ok and contact_valid else '',
                                 'prefix_contacts_with_reference_after_prefix': references_after if reconstruction_ok and contact_valid else '',
                                 **state})
        write_csv(self.output / 'A1_requests.csv', requests)
        write_csv(self.output / 'A1_fact_retention.csv', facts)
        write_csv(self.output / 'A1_episodes.csv', episode_rows)
        write_csv(self.output / 'A1_reconstruction.csv', mapping_rows)
        write_csv(self.output / 'A3_boundary_coverage.csv', boundary_rows)
        self.llm_episodes, self.requests, self.boundaries = episode_rows, requests, boundary_rows
        self.cross_products()
        self.a_effects()

    def cross_products(self):
        rows = []
        for method in METHOD_CONDITIONS:
            for condition in CONDITIONS:
                for length in range(1, max(40, max((r['total_requests'] for r in self.llm_episodes), default=40)) + 1):
                    episodes = [r for r in self.llm_episodes if r['method'] == method and r['condition'] == condition and r['total_requests'] == length]
                    requests = [r for r in self.requests if r['method'] == method and r['condition'] == condition and r['total_requests'] == length]
                    expected = sum(r['expected_entries'] for r in requests)
                    rows.append({'method': method, 'condition': condition, 'total_requests': length,
                                 'supported_cell': int(condition in METHOD_CONDITIONS[method]), 'episodes': len(episodes),
                                 'action_requests': len(requests), 'expected_fact_occurrences': expected,
                                 **{field: sum(r[field] for r in requests) for field in
                                    ('full_entries', 'semantic_trace_only_entries', 'no_trace_entries')},
                                 'full_retention_fraction': sum(r['full_entries'] for r in requests) / expected if expected else '',
                                 'zero_list_episodes': sum(r['vacuous'] for r in episodes),
                                 **describe([r['prefix_requests'] for r in episodes], 'prefix')})
        write_csv(self.output / 'A1_cross_condition_family_length.csv', rows)
        summaries = []
        for method in METHOD_CONDITIONS:
            for condition in CONDITIONS:
                selected = [r for r in self.llm_episodes if r['method'] == method and r['condition'] == condition]
                bearing = [r for r in selected if not r['vacuous']]
                summaries.append({'method': method, 'condition': condition, 'episodes': len(selected),
                                  'knowledge_bearing_episodes': len(bearing),
                                  **describe([r['total_requests'] for r in selected], 'requests'),
                                  **describe([r['prefix_requests'] for r in selected], 'prefix_including_vacuous'),
                                  **describe([r['prefix_requests'] for r in bearing], 'prefix_knowledge_bearing'),
                                  'half_retained_episodes': sum(r['half_retained'] for r in selected),
                                  'censored_prefixes': sum(r['prefix_censored'] for r in selected if r['prefix_censored'] != '')})
        write_csv(self.output / 'A1_family_summary.csv', summaries)
        overall = []
        for method in METHOD_CONDITIONS:
            episodes = [r for r in self.llm_episodes if r['method'] == method]
            bearing = [r for r in episodes if not r['vacuous']]
            overall.append({'method': method, 'episodes': len(episodes), 'knowledge_bearing': len(bearing),
                            **describe([r['total_requests'] for r in episodes], 'requests'),
                            **describe([r['prefix_requests'] for r in bearing], 'prefix_knowledge_bearing'),
                            'qualification': 'condition_mix_differs_between_methods_use_shared_condition_pairs_for_comparison'})
        write_csv(self.output / 'A1_family_overall.csv', overall)
        differences = []
        for condition in METHOD_CONDITIONS['direct_llm']:
            groups = {method: {r['instance_id']: r for r in self.llm_episodes if r['method'] == method and r['condition'] == condition}
                      for method in METHOD_CONDITIONS}
            for field in ('total_requests', 'prefix_requests'):
                for bearing_only in (False, True):
                    keys = sorted(set(groups['direct_llm']) & set(groups['react']))
                    if bearing_only:
                        keys = [k for k in keys if not groups['direct_llm'][k]['vacuous']]
                    values = [groups['react'][k][field] - groups['direct_llm'][k][field] for k in keys]
                    # Descriptive continuous quantities: no binary event threshold.
                    result = interval(values, [groups['react'][k]['family_id'] for k in keys], contrast=True, unit='family_continuous')
                    if result['independent_units'] < 5:
                        result.update(ci95_low='', ci95_high='', ci_status='fewer_than_5_families')
                    differences.append({'condition': condition, 'metric': field, 'contrast': 'react-direct_llm',
                                        'knowledge_bearing_only': int(bearing_only), **result})
        write_csv(self.output / 'A1_paired_family_lengths.csv', differences)

    def a_effects(self):
        frozen_effects = self.inputs.csv(BASE / 'primary_contrasts.csv')
        frozen_levels = self.inputs.csv(BASE / 'condition_intervals.csv')
        output = []
        specs = [('react', 'K+S', 'K+N', 'contact', False),
                 ('direct_llm', 'empty', 'K', 'success', True),
                 ('react', 'empty', 'K', 'success', True), ('react', 'K', None, 'success', True)]
        for method, left, right, metric, feasible in specs:
            groups = {condition: {r['instance_id']: r for r in self.llm_episodes if r['method'] == method and r['condition'] == condition
                                  and (not feasible or r['true_feasible'])} for condition in (left, right) if condition}
            metric_name = 'discriminative_stale_contact' if metric == 'contact' else 'feasible_SR'
            frozen = next((r for r in (frozen_effects if right else frozen_levels) if r['method'] == method
                           and r.get('contrast', r.get('condition')) == (f'{left} - {right}' if right else left)
                           and r['metric'] == metric_name), {})
            for scope in ('full', 'prefix', 'half_retained'):
                field = f'prefix_{metric}' if scope == 'prefix' else f'full_{metric}'
                keys = sorted(groups[left])
                if right:
                    keys = sorted(set(keys) & set(groups[right]))
                keys = [k for k in keys if groups[left][k][field] != '' and (not right or groups[right][k][field] != '')
                        and (scope != 'half_retained' or (groups[left][k]['half_retained'] and (not right or groups[right][k]['half_retained'])))]
                values = [groups[left][k][field] - (groups[right][k][field] if right else 0) for k in keys]
                result = interval(values, [groups[left][k]['family_id'] for k in keys], contrast=bool(right))
                row = {'dataset': 'A8_LLM', 'method': method, 'scope': scope,
                       'contrast': f'{left} - {right}' if right else left, 'metric': metric_name,
                       **result, 'left_positive': sum(groups[left][k][field] for k in keys),
                       'right_positive': sum(groups[right][k][field] for k in keys) if right else '',
                       'discordant_pairs': sum(value != 0 for value in values) if right else '',
                       'left_prefix_censored': sum(groups[left][k]['prefix_censored'] for k in keys
                                                   if groups[left][k]['prefix_censored'] != ''),
                       'right_prefix_censored': sum(groups[right][k]['prefix_censored'] for k in keys
                                                    if groups[right][k]['prefix_censored'] != '') if right else '',
                       'frozen_estimate': frozen.get('difference', frozen.get('estimate', '')) if scope == 'full' else '',
                       'frozen_ci95_low': frozen.get('ci95_low', '') if scope == 'full' else '',
                       'frozen_ci95_high': frozen.get('ci95_high', '') if scope == 'full' else '',
                       'frozen_bootstrap_unit': frozen.get('bootstrap_unit', '') if scope == 'full' else ''}
                if scope == 'full' and row['frozen_estimate'] != '' and abs(float(row['frozen_estimate']) - result['estimate']) > 1e-10:
                    self.issue('A_full_point', f'{method}/{left}/{right}', 'new_point_does_not_match_frozen', 'not_reported_full_comparison')
                    row['ci_status'] = 'NOT_REPORTED_frozen_point_disagreement'
                output.append(row)
        write_csv(self.output / 'A2_three_scope_effects.csv', output)
        counts = []
        for method, conditions in METHOD_CONDITIONS.items():
            for condition in conditions:
                selected = [r for r in self.llm_episodes if r['method'] == method and r['condition'] == condition and r['true_feasible']]
                valid = [r for r in selected if r['prefix_success'] != '']
                counts.append({'method': method, 'condition': condition, 'feasible_episodes': len(selected),
                               'prefix_alignment_available': len(valid),
                               'full_successes': sum(r['full_success'] for r in selected),
                               **{field: sum(r[field] for r in valid) for field in
                                  ('prefix_goal_attained', 'prefix_success', 'prefix_censored', 'prefix_model_submit_observed')}})
        write_csv(self.output / 'A2_prefix_outcome_counts.csv', counts)
        self.prefix_outcome_counts = counts
        self.a_results = output

    def pipeline(self):
        builder = self.inputs.text(WEB / 'scripts/build_cds_materials_20261016.py')
        runner = self.inputs.text(WEB / 'scripts/run_ext_v2_cds_20261016.py')
        self.inputs.text(ROOT / 'src/arbiter/evaluation/p6_execution.py')
        self.inputs.text(ROOT / 'src/arbiter/agent/core.py')
        original = self.inputs.lines(WEB / 'reports/paperA/ext_v2/cds_run/episodes.jsonl')
        keys = [(r['method'], r['cds_condition'], r['cds_instance_id']) for r in original]
        expected_keys = {(m, c, i) for m in PIPELINE_METHODS for c in CONDITIONS for i in self.materials}
        coverage = len(keys) == 2600 and len(set(keys)) == 2600 and set(keys) == expected_keys
        if not coverage:
            self.issue('B_coverage', 'all', f'expected_2600_got_{len(keys)}_unique_{len(set(keys))}')
        equivalence, details = [], []
        for instance, material in sorted(self.materials.items()):
            for condition in CONDITIONS:
                facts = material['conditions'][condition]
                private = self.private[instance]['conditions'][condition]
                by_hash = {hashlib.sha256(f['mechanism fact'].encode()).hexdigest(): f for f in facts}
                pipeline_facts = [{'mechanism fact': by_hash[r['text_sha256']]['mechanism fact'],
                                   'source': r['source'], 'timestamp': r['timestamp']} for r in private]
                equal = Counter(map(canonical, facts)) == Counter(map(canonical, pipeline_facts))
                recipes = [r for r in private if r['assertion'].get('kind') == 'recipe']
                # raw_claim compiles these private fields; public English text lacks layouts/count declarations.
                extra_layout = sum(bool(r['assertion'].get('ordered') and r['assertion'].get('grid')) for r in recipes)
                extra_quantities = sum('count' in r['assertion'] and
                                       not re.search(r'\b(?:count|quantity|output\s*[=:]|produces?\s+\d)', by_hash[r['text_sha256']]['mechanism fact'])
                                       for r in recipes)
                role_metadata = len(private)
                equivalent = equal and not (extra_layout or extra_quantities or role_metadata)
                equivalence.append({'instance_id': instance, 'condition': condition, 'same_instance_set': int(coverage),
                                    'public_fact_multiset_equal': int(equal), 'public_fact_entries': len(facts),
                                    'private_layout_entries': extra_layout, 'private_output_count_entries': extra_quantities,
                                    'role_bearing_page_titles': role_metadata, 'strict_semantic_input_equivalent': int(equivalent)})
                for r in recipes[:1]:
                    details.append({'instance_id': instance, 'condition': condition,
                                    'public_fact': by_hash[r['text_sha256']]['mechanism fact'],
                                    'public_source': r['source'], 'public_timestamp': r['timestamp'],
                                    'private_assertion': canonical(r['assertion']),
                                    'pipeline_page_title': f"CDS {r['role']}",
                                    'interpretation': 'extra_structured_layout_quantity_and_role_metadata_not_in_A8_fact_text'})
        gate = 'EQUIVALENT' if all(r['strict_semantic_input_equivalent'] and r['same_instance_set'] for r in equivalence) else 'NOT_EQUIVALENT'
        write_csv(self.output / 'B0_equivalence.csv', equivalence)
        write_csv(self.output / 'B0_field_evidence.csv', details)
        condition_rows = []
        for condition in CONDITIONS:
            cells = [r for r in equivalence if r['condition'] == condition]
            condition_rows.append({'condition': condition, 'instances': len(cells),
                                   **{field: sum(r[field] for r in cells) for field in
                                      ('public_fact_multiset_equal', 'strict_semantic_input_equivalent',
                                       'private_layout_entries', 'private_output_count_entries', 'role_bearing_page_titles')}})
        write_csv(self.output / 'B0_condition_summary.csv', condition_rows)
        wiring = []
        for path, names in [
            (WEB / 'scripts/run_ext_v2_cds_20261016.py', {'raw_claim', 'cds_bundle', 'method_belief', 'metric_row', 'run_cds'}),
            (WEB / 'scripts/build_cds_materials_20261016.py', {'main'}),
            (ROOT / 'src/arbiter/evaluation/p6_execution.py', {'_claims', 'run_episode'}),
            (ROOT / 'src/arbiter/agent/core.py', {'configure', 'solve'}),
        ]:
            text = self.inputs.text(path)
            lines = text.splitlines()
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.FunctionDef) and node.name in names:
                    wiring.append({'source': str(path), 'function': node.name, 'first_line': node.lineno,
                                   'last_line': node.end_lineno, 'sha256': sha(path),
                                   'function_source': '\n'.join(lines[node.lineno - 1:node.end_lineno])})
        (self.output / 'B0_source_wiring.json').write_text(json.dumps(wiring, ensure_ascii=False, indent=2) + '\n')
        self.b_gate, self.b_equivalence = gate, equivalence
        if gate != 'EQUIVALENT':
            self.issue('B0', 'all', 'NOT_EQUIVALENT; B1/B2 are related evidence only', 'interpretation_constraint')
        conflicts = {r['id']: r for r in self.inputs.lines(WEB / 'data/conflicts.jsonl')}
        nodes = {r['nodeId']: r['block'] for r in self.inputs.json(WEB / 'harness/config/arena.json')['resources']}
        cache, intrinsic_cache, episode_rows, action_rows = {}, {}, [], []
        for record in original:
            method, condition, instance = record['method'], record['cds_condition'], record['cds_instance_id']
            events, world = record['events'], record['world']
            task = self.tasks[record['task_id']]
            feasible = record['true_feasible']
            valid_feasible = bool(task['feasible'][world]) == feasible
            if not valid_feasible:
                self.issue('B_feasibility', f'{method}/{condition}/{instance}', 'task_record_disagreement', 'not_reported_feasible_metric')
            stale_by_event = {}
            for index, event in enumerate(events):
                if event['method'] not in ACTION_METHODS:
                    continue
                key = canonical([world, event['method'], event['params']])
                if key not in cache:
                    cache[key] = sorted(cid for cid, c in conflicts.items()
                                        if c['truth_vector'].get(world) is False and any(c['truth_vector'].values())
                                        and _matches(event, c['assertion'], nodes))
                stale_by_event[index] = cache[key]
            classified = classify_actions_v3(events, stale_by_event=stale_by_event, conflicts=conflicts,
                                             resource_nodes=nodes, target=task['target'], episode_success=bool(record['metrics']['SR']))
            for row in classified:
                event = events[row['event_index']]
                key = canonical([world, event['method'], event['params'], row['stale_mechanism_ids']])
                if key not in intrinsic_cache:
                    intrinsic_cache[key] = intrinsic_discriminative(event, row['stale_mechanism_ids'], world=world,
                                                                   conflicts=conflicts, resource_nodes=nodes)
                row['discriminative'] = int(intrinsic_cache[key] or bool(row['referenced_at']))
                action_rows.append({'method': method, 'condition': condition, 'instance_id': instance,
                                    'event_index': row['event_index'], 'action': event['method'],
                                    'catalog_stale_ids': canonical(row['stale_mechanism_ids']),
                                    'intrinsic': int(intrinsic_cache[key]), 'referenced_at': canonical(row['referenced_at']),
                                    'discriminative': row['discriminative']})
            episode_rows.append({'dataset': 'deterministic_CDS', 'method': method, 'condition': condition,
                                 'instance_id': instance, 'task_id': record['task_id'], 'world': world,
                                 'family_id': task['independent_family_id'],
                                 'true_feasible': int(feasible) if valid_feasible else '',
                                 'success': int(record['metrics']['SR']),
                                 'contact': int(any(row['discriminative'] for row in classified)),
                                 'contact_events': sum(row['discriminative'] for row in classified),
                                 'legacy_used_id_HTR_all': record['metrics'].get('HTR_all', ''),
                                 'scorer': 'frozen_catalog_matches+classify_actions_v3+intrinsic_or_reference',
                                 'evidence_status': 'related_not_replacement' if gate != 'EQUIVALENT' else 'equivalent_conditions'})
        write_csv(self.output / 'B1_episodes.csv', episode_rows)
        write_csv(self.output / 'B1_action_rescore.csv', action_rows)
        oracle_diagnostics = [r for r in action_rows if r['method'] == 'oracle' and r['discriminative']]
        write_csv(self.output / 'B1_oracle_scorer_diagnostics.csv', oracle_diagnostics)
        if oracle_diagnostics:
            self.issue('B_frozen_scorer_specificity', 'oracle',
                       'oracle_has_discriminative_flags_under_required_frozen_intrinsic_or_reference_rule; not_strict_causal_identification',
                       'measurement_limitation_no_rule_change')
        self.b_episodes = episode_rows
        self.b_effects()

    def b_effects(self):
        output = []
        for method in (*PIPELINE_METHODS, 'ALL_METHODS_EQUAL_WEIGHT'):
            values = [r for r in self.b_episodes if r['method'] == method] if method in PIPELINE_METHODS else self.b_episodes
            grouped = defaultdict(list)
            for row in values:
                grouped[(row['condition'], row['instance_id'])].append(row)
            pooled = {}
            for (condition, instance), rows in grouped.items():
                pooled[(condition, instance)] = {**rows[0], 'contact': mean(r['contact'] for r in rows),
                                                'success': mean(r['success'] for r in rows),
                                                'contact_positive_episodes': sum(r['contact'] for r in rows),
                                                'methods_in_instance': len(rows)}
            for left, right, metric, feasible in [('K+S', 'K+N', 'contact', False),
                                                   ('empty', 'K', 'success', True), ('K', None, 'success', True)]:
                keys = sorted(i for c, i in pooled if c == left and (not right or (right, i) in pooled))
                if feasible:
                    keys = [i for i in keys if pooled[(left, i)]['true_feasible'] == 1
                            and (not right or pooled[(right, i)]['true_feasible'] == 1)]
                deltas = [pooled[(left, i)][metric] - (pooled[(right, i)][metric] if right else 0) for i in keys]
                positives = sum(pooled[(left, i)]['contact_positive_episodes'] + pooled[(right, i)]['contact_positive_episodes']
                                for i in keys) if metric == 'contact' else None
                result = interval(deltas, keys, contrast=bool(right), unit='directed_instance', contact_positives=positives)
                output.append({'dataset': 'deterministic_CDS', 'method': method,
                               'scope': 'full', 'contrast': f'{left} - {right}' if right else left,
                               'metric': 'discriminative_stale_contact' if metric == 'contact' else 'feasible_SR',
                               **result, 'left_positive': sum(pooled[(left, i)][metric] for i in keys),
                               'right_positive': sum(pooled[(right, i)][metric] for i in keys) if right else '',
                               'contact_positive_episodes': positives if positives is not None else '',
                               'evidence_status': 'related_not_replacement' if self.b_gate != 'EQUIVALENT' else 'equivalent_conditions'})
        self.b_results = output
        write_csv(self.output / 'B1_effects_by_method_and_pooled.csv', output)
        direct_k = [r for r in self.llm_episodes if r['method'] == 'direct_llm' and r['condition'] == 'K' and r['true_feasible']]
        supplemental = {'dataset': 'A8_LLM', 'method': 'direct_llm', 'scope': 'full_parallel_comparison',
                        'contrast': 'K', 'metric': 'feasible_SR',
                        **interval([r['full_success'] for r in direct_k], [r['family_id'] for r in direct_k], contrast=False),
                        'left_positive': sum(r['full_success'] for r in direct_k)}
        write_csv(self.output / 'B2_parallel_LLM_pipeline.csv', [*self.a_results, supplemental, *output])

    def finish(self):
        changed = self.inputs.verify()
        if changed:
            self.issue('input_hashes', 'all', 'inputs_changed_during_analysis', 'not_reported_affected_sources')
        self.inputs.text(Path(__file__))
        boundary_bearing = [row for row in self.boundaries if row['expected_entries']]
        requests_bearing = [row for row in self.requests if row['expected_entries']]
        summary = {
            'prereg_commit': '4764388', 'correction_commit': 'f550b67', 'llm_calls': 0, 'new_episodes': 0,
            'llm_episodes': len(self.llm_episodes), 'llm_action_requests': len(self.requests),
            'knowledge_bearing_episodes': sum(not r['vacuous'] for r in self.llm_episodes),
            'knowledge_bearing_requests': len(requests_bearing),
            'fully_retained_knowledge_bearing_requests': sum(r['all_full'] for r in requests_bearing),
            'expected_fact_request_occurrences': sum(r['expected_entries'] for r in self.requests),
            'fact_retention_classes': {field: sum(r[field] for r in self.requests) for field in
                                      ('full_entries', 'semantic_trace_only_entries', 'no_trace_entries')},
            'request_reconstruction_episodes_ok': sum(r['request_reconstruction_ok'] for r in self.llm_episodes),
            'prefix_contacts_using_later_reference': sum(r['prefix_contacts_with_reference_after_prefix']
                                                       for r in self.llm_episodes if r['prefix_contacts_with_reference_after_prefix'] != ''),
            'boundary_calls': len(self.boundaries), 'boundary_knowledge_bearing': len(boundary_bearing),
            'boundary_missing_knowledge': sum(not r['all_full'] for r in self.boundaries),
            'boundary_missing_knowledge_fraction_all': sum(not r['all_full'] for r in self.boundaries) / len(self.boundaries) if self.boundaries else None,
            'boundary_missing_knowledge_fraction_bearing': sum(not r['all_full'] for r in boundary_bearing) / len(boundary_bearing) if boundary_bearing else None,
            'B0': self.b_gate, 'B_public_fact_multisets_equal': sum(r['public_fact_multiset_equal'] for r in self.b_equivalence),
            'B_condition_instance_cells': len(self.b_equivalence), 'B_pipeline_episodes': len(self.b_episodes),
            'B_interpretation': 'related_not_replacement' if self.b_gate != 'EQUIVALENT' else 'equivalent_conditions',
            'changed_inputs': changed, 'exceptions': self.exceptions,
        }
        (self.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        (self.output / 'input_hashes.json').write_text(json.dumps(self.inputs.hashes, indent=2, sort_keys=True) + '\n')
        write_csv(self.output / 'exceptions.csv', self.exceptions, ['item', 'identifier', 'detail', 'severity'])
        report = ['# A14: retention, prefix timing and deterministic-CDS related evidence', '',
                  '原预注册：4764388；计算前更正：f550b67（两者均保留）。零 LLM 调用、零新回合、无智能体重跑、未推送。', '',
                  '## A1 保持范围', '', f"416 对象实际覆盖：{len(self.llm_episodes)} 回合、{len(self.requests)} 次动作请求。",
                  f"请求重建一致的回合：{summary['request_reconstruction_episodes_ok']}/{len(self.llm_episodes)}。",
                  '嵌套金丝雀不算动作请求；按 call_type 匹配外层动作回复，没有将金丝雀误作动作回复或当成缺失回复。',
                  f"有注入知识的请求中完整保持：{summary['fully_retained_knowledge_bearing_requests']}/{len(requests_bearing)}。",
                  '三档逐事实计数：' + canonical(summary['fact_retention_classes']) + '。',
                  '完整保持按原文+来源+时间戳的多重集；语义痕迹只是已冻结的词面/结构代理，不作为保持。',
                  '400 格交叉表含零单元格；长度单位为动作请求，不是语义动作。Direct 两步、ReAct 八步的 trace 不是初始知识的永久记忆。', '',
                  '## A2 三口径', '',
                  '| 方法 | 对比/水平 | 指标 | 口径 | n | 点估计 | 新95%区间 | 状态 |',
                  '| --- | --- | --- | --- | ---: | ---: | --- | --- |']
        for row in self.a_results:
            ci = f"[{row['ci95_low']:.6f}, {row['ci95_high']:.6f}]" if row['ci95_low'] != '' else '不报告'
            point = f"{row['estimate']:.6f}" if row['estimate'] != '' else '不适用'
            report.append(f"| {row['method']} | {row['contrast']} | {row['metric']} | {row['scope']} | {row['n']} | {point} | {ci} | {row['ci_status']} |")
        report.extend(['', '前缀未终止的回合记为删失，而非断言其最终失败；prefix_success 仅指已在前缀内观测到达成目标并肯定提交。',
                       'half_retained 是条件筛选后的描述性子集，不能解释为缺陷修复后的反事实性能。',
                       '冻结值及旧区间另保留在 frozen_*；新 A 区间按簇重采样后汇总实例（pooled ratio），旧 A8 区间为等权家族均值。', '',
                       '### 前缀内达成目标不等于已提交成功', '',
                       '| 方法 | 条件 | 可行n | 全回合成功 | 前缀目标已达成 | 前缀成功提交 | 前缀删失 |',
                       '| --- | --- | ---: | ---: | ---: | ---: | ---: |'])
        for row in self.prefix_outcome_counts:
            report.append(f"| {row['method']} | {row['condition']} | {row['feasible_episodes']} | {row['full_successes']} | {row['prefix_goal_attained']} | {row['prefix_success']} | {row['prefix_censored']} |")
        report.extend(['', f"选入前缀的接触中，{summary['prefix_contacts_using_later_reference']} 个动作带有前缀后才出现的引用证据；保留冻结分类，但不能声称引用行为本身也在知识保持时发生。", '',
                       '## A3 一句协议结论', '',
                       f"移除问询候选后，回合末原始边界请求缺失应有注入事实的比例为 {summary['boundary_missing_knowledge']}/{len(self.boundaries)}；"
                       f"有知识分母为 {len(boundary_bearing)}。边界调用保留完整 API trace，不能从动作请求的有界 trace 丢失推断边界问询也丢失。未产生新 BIA 数值。", '',
                       '## B0 等价性与测量', '',
                       f"结论：{self.b_gate}。公共事实多重集相同：{summary['B_public_fact_multisets_equal']}/{len(self.b_equivalence)} 条件×实例。",
                       '同一材料构造：K=非焦点+当前焦点；K+S=非焦点+当前焦点+陈旧焦点；S=非焦点+陈旧焦点；empty=非焦点；K+N=非焦点+当前焦点+中性。',
                       '两套共享同一冻结实例与同一中性/陈旧材料；但确定性 raw_claim 还消费私有结构中的布局、原料备选、数量，并把 role 写为 page_title。',
                       '逐条件等价性见 B0_condition_summary.csv，逐函数行号及源码见 B0_source_wiring.json；字段实例见 B0_field_evidence.csv。',
                       '这些不是 A8 可见句子里全部声明的信息；参数法使用冻结先验，Oracle 绕过证言。B 只能作相关证据，不能替代同输入下的 LLM 因果对照。',
                       '原确定性导出使用 used_mechanism_ids 与 stale 目录集合/intent，不调用 classify_actions_v3 或 intrinsic_discriminative。',
                       '本次新列使用与 A8 相同的冻结目录匹配、classify_actions_v3 和 intrinsic OR 后续引用，旧 used-ID HTR 原样另存。未修复或掩盖冻结匹配器的局限。', '',
                       '评分器局限：Oracle 在每个条件也有 6/52 个回合被该冻结“intrinsic OR 引用”算法标记；相关动作见 B1_oracle_scorer_diagnostics.csv。'
                       '其中同一布局产物改名时，按产物 subject 找当前有效对照会漏掉另一产物名；原料范围交叠的合法摆法也可因后续同名产物引用而被标记。'
                       '本次不改冻结规则，因此这些列是同算法的可比接触量，不是陈旧断言确为行动前提的充分因果证据。', '',
                       '## B1/B2（相关证据）', '',
                       '| 方法 | 对比/水平 | 指标 | n实例 | 点估计 | 95%区间 | 状态 |',
                       '| --- | --- | --- | ---: | ---: | --- | --- |'])
        for row in self.b_results:
            ci = f"[{row['ci95_low']:.6f}, {row['ci95_high']:.6f}]" if row['ci95_low'] != '' else '不报告'
            point = f"{row['estimate']:.6f}" if row['estimate'] != '' else '不适用'
            report.append(f"| {row['method']} | {row['contrast']} | {row['metric']} | {row['n']} | {point} | {ci} | {row['ci_status']} |")
        report.extend(['', 'B 的重采样单位为 52 个有方向实例（可行子集为30），不是2,600行；合并结果先在每个实例内等权平均十方法，再重采样实例。',
                       'B2_parallel_LLM_pipeline.csv 只并列、不合并确定性与 LLM 结果。', '', '## 异常与纪律', '',
                       'B 中完全相同的逐实例配对向量会产生 [0,0] 自助区间，全成功水平可产生 [1,1]；这是固定经验样本的退化区间，不是对未观测实例无不确定性的断言。',
                       f'记录的差异/限定共 {len(self.exceptions)} 条，见 exceptions.csv；受影响项保守处理，其余分析完成。',
                       f'输入前后哈希变化：{len(changed)}。WebCraft、原预注册、原停止报告、原始结果与 server.lock 均未改；不读取人工审计 key。',
                       '运行：`nice -n 15 env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. OPENBLAS_NUM_THREADS=1 python -m scripts.analyze_a14_cds_retention`。',
                       '各文件名对应 A1/A2/A3/B0/B1/B2；输入路径及 SHA256 见 input_hashes.json，汇总见 summary.json。'])
        (self.output / 'REPORT.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
        print(json.dumps({k: v for k, v in summary.items() if k != 'exceptions'}, ensure_ascii=False, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError('Refusing to overwrite existing analysis results')
    args.output_dir.mkdir(parents=True)
    analysis = Analysis(args.output_dir)
    analysis.llm()
    analysis.pipeline()
    analysis.finish()


if __name__ == '__main__':
    main()
