#!/usr/bin/env python3
"""Preregistered, network-free comparison of existing frozen event arrays."""
from __future__ import annotations

import csv
import gzip
import hashlib
import itertools
import json
import os
import resource
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/paperA/w12_trajectory_identity'
PREREG = ROOT / 'prereg/w12_trajectory_identity_20261017.md'
MAIN = ROOT / 'release/package/assets/frozen/main.jsonl.gz'
W11 = ROOT / 'reports/paperA/w11_attribution/episode_attribution_sensitivity.csv'
W9 = ROOT / 'reports/paperA/w9_release_fix/replay/main_comparison.csv'
METHODS = ('arbiter', 'arbiter_shell_naive', 'majority_rag', 'naive_rag',
           'oracle', 'parametric', 'probe_then_parametric', 'probe_then_rag',
           'truthfinder_rag', 'verify_all')
MODES = ('all_public', 'llm_only', 'oracle_reading')
PUBLIC = frozenset(('inventory', 'mine', 'craft_grid', 'smelt', 'check_name',
                    'report_version', 'submit', 'search'))
KEYS = ('reading_mode', 'method', 'condition', 'world', 'task_id')
LEVELS = ('strict', 'normalized', 'method_names')
LABELS = ('plan_acceptance', 'specific_action', 'specific_contact')
FIELDS = ('matched', 'mismatched_new2old', 'mismatched_old2new', 'mixed', 'polluted')


def check(value, message):
    if not value:
        raise RuntimeError('STOP: ' + str(message))


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_csv(path):
    with path.open(newline='', encoding='utf8') as handle:
        return list(csv.DictReader(handle))


def write_csv(name, rows, fields=None):
    rows = list(rows)
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    check(fields, f'no CSV columns: {name}')
    with (OUT / name).open('w', newline='', encoding='utf8') as handle:
        writer = csv.DictWriter(handle, fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def resource_gate(stage):
    available = int(next(line.split()[1] for line in Path('/proc/meminfo')
                         .read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    limit = Path('/sys/fs/cgroup/memory.max')
    if limit.exists() and limit.read_text().strip() != 'max':
        available = min(available, int(limit.read_text()) -
                        int(limit.with_name('memory.current').read_text()))
    check(available >= 10 * 1024**3, '2 GiB planned plus 8 GiB headroom unavailable')
    check(os.environ.get('CUDA_VISIBLE_DEVICES') == '', 'CUDA not disabled')
    (OUT / f'resources_{stage}.json').write_text(json.dumps({
        'stage': stage, 'available_bytes': available, 'planned_bytes': 2 * 1024**3,
        'headroom_bytes': 8 * 1024**3, 'GPU_used': False,
        'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }, indent=2) + '\n')


def item_alias(value):
    return value.removeprefix('minecraft:') if isinstance(value, str) else value


def normalized_params(method, params):
    result = dict(params)
    layout = params.get('layout')
    if method == 'craft_grid' and isinstance(layout, list) and len(layout) == 9:
        cells = []
        for cell in layout:
            if (isinstance(cell, dict) and set(cell) <= {'name', 'count'}
                    and isinstance(cell.get('name'), str)
                    and cell.get('count', 1) == 1):
                cell = cell['name']
            cells.append(item_alias(cell) if isinstance(cell, str) else cell)
        result['layout'] = cells
    for field in (('input', 'fuel') if method == 'smelt'
                  else ('name',) if method == 'check_name' else ()):
        if field in result:
            result[field] = item_alias(result[field])
    return result


def projections(events):
    strict = tuple(encoded({'method': e['method'], 'params': e['params']}) for e in events)
    normalized = tuple(encoded({'method': e['method'],
                                'params': normalized_params(e['method'], e['params'])})
                       for e in events)
    names = tuple(encoded(e['method']) for e in events)
    # Length-delimited JSON arrays preserve full projection bytes, not just hashes.
    strict_bytes = b'[' + b','.join(strict) + b']'
    intent = encoded([{'method': e['method'], 'params': e['params'],
                       'intent': e.get('intent')} for e in events])
    history = encoded([{key: e.get(key) for key in
                        ('method', 'params', 'intent', 'result', 'success', 'cost', 'item_flow')}
                       for e in events])
    return (strict, normalized, names), strict_bytes, intent, history


def lcp(a, b):
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def condition_group(condition):
    if condition.startswith('matched_'):
        return 'matched'
    check(condition in FIELDS, ('unknown condition', condition))
    return condition


def pair_stratum(a, b):
    return 'both' if a and b else 'A_only' if a else 'B_only' if b else 'neither'


@dataclass
class Episode:
    key: tuple
    tokens: tuple
    strict: bytes
    intent: bytes
    history: bytes
    mids: bytes
    retrieval: bytes
    labels: tuple
    sensitive: bool
    event_count: int
    submit_count: int
    public_counts: Counter


def load():
    w11_rows, w9_rows = read_csv(W11), read_csv(W9)
    w11 = {tuple(r[k] for k in KEYS): r for r in w11_rows}
    w9 = {tuple(r[k] for k in KEYS): r for r in w9_rows}
    check(len(w11) == len(w11_rows) == len(w9) == len(w9_rows) == 11220,
          'W9/W11 population/unique-key mismatch')
    episodes = {}
    index_rows = []
    mode_cells = defaultdict(lambda: defaultdict(set))
    completeness = defaultdict(Counter)
    with gzip.open(MAIN, 'rt', encoding='utf8') as handle:
        for line in handle:
            rec = json.loads(line)
            key = tuple(rec[k] for k in KEYS)
            mode, method, condition, world, task = key
            check(key not in episodes and key in w11 and key in w9, ('population key', key))
            check(mode in MODES and method in METHODS, ('mode/method', key))
            check(rec.get('schema_version') == 2 and rec.get('record_type') == 'episode',
                  ('episode schema', key))
            events = rec.get('events')
            check(isinstance(events, list) and events, ('missing event array', key))
            for sequence, event in enumerate(events, 1):
                check(isinstance(event, dict) and event.get('method') in PUBLIC,
                      ('unknown public method', key, sequence))
                check(isinstance(event.get('params'), dict) and event.get('schema_version') == 2
                      and event.get('sequence') == sequence, ('malformed/truncated event', key, sequence))
                check(all(field in event for field in ('result', 'success', 'cost', 'item_flow')),
                      ('missing public observation', key, sequence))
            labels = (int(rec['metrics']['HTR_commit']), int(w11[key]['specific_action']),
                      int(w11[key]['specific_contact']))
            check(all(v in (0, 1) for v in labels), ('nonbinary label', key))
            check(labels[0] == int(w9[key]['frozen_plan_acceptance']) and
                  labels[1] == int(w9[key]['frozen_specific_action']) and
                  labels[2] == int(bool(int(w9[key]['frozen_specific_action']) or
                                        int(w9[key]['frozen_specific_query']))),
                  ('baseline annotation mismatch', key))
            sensitive = bool(int(w11[key]['action_attribution_sensitive']) or
                             int(w11[key]['contact_attribution_sensitive']))
            tokens, strict, intent, history = projections(events)
            counts = Counter(e['method'] for e in events)
            episode = Episode(key, tokens, strict, intent, history,
                              encoded(rec['used_mechanism_ids']),
                              encoded(rec['reader'].get('search_events', [])), labels, sensitive,
                              len(events), counts['submit'], counts)
            episodes[key] = episode
            mode_cells[mode][method].add((condition, world, task))
            cc = completeness[(mode, method)]
            cc['episodes'] += 1
            cc['no_submit'] += int(counts['submit'] == 0)
            cc['public_calls'] += len(events)
            cc['separate_reader_queries'] += len(rec['reader'].get('search_events', []))
            cc['w11_sensitive'] += sensitive
            cc.update({f'calls_{name}': counts[name] for name in sorted(PUBLIC)})
            index_rows.append({**dict(zip(KEYS, key)), 'public_calls': len(events),
                               'submit_count': counts['submit'], 'strict_sha256': digest(strict),
                               'commands_intent_sha256': digest(intent),
                               'public_history_sha256': digest(history),
                               'attribution_sha256': digest(episode.mids),
                               'reader_queries_sha256': digest(episode.retrieval),
                               **dict(zip(LABELS, labels)), 'w11_sensitive': int(sensitive)})
    check(len(episodes) == 11220 and set(episodes) == set(w11) == set(w9), 'incomplete matrix')
    common = None
    for mode in MODES:
        check(set(mode_cells[mode]) == set(METHODS), ('missing methods', mode))
        for method in METHODS:
            cells = mode_cells[mode][method]
            check(len(cells) == 374 and (common is None or cells == common),
                  ('not 374 fully paired cells', mode, method))
            common = cells
    check(sum(e.sensitive for e in episodes.values()) == 39, 'W11 membership is not 39')
    cell_groups = Counter(condition_group(c) for c, _, _ in common)
    check(cell_groups == {'matched': 102, 'mismatched_new2old': 34, 'mismatched_old2new': 34,
                          'mixed': 102, 'polluted': 102}, ('unexpected cell groups', cell_groups))
    check(len({(c, w) for c, w, _ in common}) == 11, 'not eleven condition/world cells')
    write_csv('episode_index.csv', sorted(index_rows, key=lambda r: tuple(r[k] for k in KEYS)))
    write_csv('completeness.csv', [{'reading_mode': k[0], 'method': k[1], **v}
                                  for k, v in sorted(completeness.items())])
    return episodes, sorted(common)


def compare(episodes, cells):
    pairs, counters = [], Counter()
    lcp_hist = Counter()
    for mode in MODES:
        for ma, mb in itertools.combinations(METHODS, 2):
            for condition, world, task in cells:
                a = episodes[(mode, ma, condition, world, task)]
                b = episodes[(mode, mb, condition, world, task)]
                equal = [x == y for x, y in zip(a.tokens, b.tokens)]
                check(not equal[0] or equal[1], 'strict must imply normalized')
                check(not equal[1] or equal[2], 'normalized must imply method names')
                check(equal[0] == (a.strict == b.strict), 'byte-vs-token mismatch')
                check((digest(a.strict) == digest(b.strict)) == equal[0], 'hash-vs-byte mismatch')
                lengths = [lcp(x, y) for x, y in zip(a.tokens, b.tokens)]
                for level, length, same in zip(LEVELS, lengths, equal):
                    check(0 <= length <= min(a.event_count, b.event_count), 'LCP bounds')
                    check(same == (length == a.event_count == b.event_count), 'LCP/full-match mismatch')
                    lcp_hist[(mode, ma, mb, level, length)] += 1
                row = {'reading_mode': mode, 'method_A': ma, 'method_B': mb,
                       'condition': condition, 'condition_group': condition_group(condition),
                       'world': world, 'task_id': task, 'calls_A': a.event_count,
                       'calls_B': b.event_count, **{f'{lev}_identical': int(eq)
                       for lev, eq in zip(LEVELS, equal)},
                       **{f'{lev}_lcp': length for lev, length in zip(LEVELS, lengths)},
                       **{f'{lev}_lcp_fraction_shorter': length / min(a.event_count, b.event_count)
                          for lev, length in zip(LEVELS, lengths)},
                       'commands_and_intent_identical': int(a.intent == b.intent),
                       'public_history_identical': int(a.history == b.history),
                       'operator_attributions_identical': int(a.mids == b.mids),
                       'reader_queries_identical': int(a.retrieval == b.retrieval),
                       'w11_sensitive_A': int(a.sensitive), 'w11_sensitive_B': int(b.sensitive),
                       'w11_pair_stratum': pair_stratum(a.sensitive, b.sensitive),
                       **{f'{label}_A': x for label, x in zip(LABELS, a.labels)},
                       **{f'{label}_B': x for label, x in zip(LABELS, b.labels)},
                       **{f'{label}_different': int(x != y)
                          for label, x, y in zip(LABELS, a.labels, b.labels)}}
                check(not row['public_history_identical'] or row['commands_and_intent_identical'],
                      'history must imply commands+intent')
                check(not row['commands_and_intent_identical'] or equal[0],
                      'commands+intent must imply strict')
                pairs.append(row)
                counters[mode] += 1
    check(len(pairs) == 50490 and all(counters[m] == 45 * 374 for m in MODES), 'pair totals')
    write_csv('paired_cells.csv', pairs)
    write_csv('lcp_histograms.csv', [{
        'reading_mode': m, 'method_A': a, 'method_B': b, 'projection': level,
        'lcp_public_calls': length, 'cells': count, 'denominator': 374,
        'fraction': count / 374,
    } for (m, a, b, level, length), count in sorted(lcp_hist.items())])
    return pairs


def scopes(row):
    result = [('overall', 'all', 'all'),
              ('condition_group', row['condition_group'], 'all'),
              ('condition_world', row['condition'], row['world'])]
    if row['condition_group'].startswith('mismatched_'):
        result.append(('mismatched_union', 'both_mismatches', 'all'))
    return result


def matrices(pairs):
    grouped = defaultdict(list)
    for row in pairs:
        for scope, condition, world in scopes(row):
            grouped[(row['reading_mode'], row['method_A'], row['method_B'],
                     scope, condition, world)].append(row)
    tables, labels, lcp_summaries = [], [], []
    for (mode, ma, mb, scope, condition, world), rows in sorted(grouped.items()):
        den = len(rows)
        base = {'reading_mode': mode, 'method_A': ma, 'method_B': mb,
                'scope': scope, 'condition': condition, 'world': world, 'denominator': den}
        table = dict(base)
        for level in LEVELS:
            numerator = sum(r[f'{level}_identical'] for r in rows)
            table.update({f'{level}_identical_cells': numerator, f'{level}_rate': numerator / den,
                          f'{level}_different_cells': den - numerator})
            if scope == 'overall':
                values = [r[f'{level}_lcp'] for r in rows]
                lcp_summaries.append({**base, 'projection': level, 'lcp_min': min(values),
                                      'lcp_median': statistics.median(values), 'lcp_max': max(values),
                                      'lcp_mean': statistics.mean(values),
                                      'mean_fraction_shorter': statistics.mean(
                                          r[f'{level}_lcp_fraction_shorter'] for r in rows),
                                      'full_match_cells': numerator})
        tables.append(table)
        # Include full histories and intent to falsify stronger functionality claims.
        for level, flag in [(lev, f'{lev}_identical') for lev in LEVELS] + [
                ('commands_and_intent', 'commands_and_intent_identical'),
                ('public_history', 'public_history_identical')]:
            for stratum in ('all', 'neither', 'A_only', 'B_only', 'both', 'any_sensitive'):
                population = [r for r in rows if stratum == 'all' or
                              r['w11_pair_stratum'] == stratum or
                              (stratum == 'any_sensitive' and r['w11_pair_stratum'] != 'neither')]
                identical = [r for r in population if r[flag]]
                result = {**base, 'projection': level, 'w11_pair_stratum': stratum,
                          'stratum_paired_cells': len(population), 'identical_cells': len(identical)}
                for label in LABELS:
                    different = sum(r[f'{label}_different'] for r in identical)
                    result.update({f'{label}_disagreements': different,
                                   f'{label}_disagreement_rate_among_identical':
                                   different / len(identical) if identical else '',
                                   f'{label}_disagreement_rate_all_paired': different / den})
                labels.append(result)
    overall = [r for r in tables if r['scope'] == 'overall']
    check(len(overall) == 135 and all(r['denominator'] == 374 for r in overall), 'overall matrix shape')
    write_csv('pair_matrix.csv', overall)
    write_csv('condition_pair_matrix.csv', [r for r in tables if r['scope'] != 'overall'])
    focus = [r for r in tables if 'naive_rag' in (r['method_A'], r['method_B']) and
             ({r['method_A'], r['method_B']} - {'naive_rag'}) <= {
                 'verify_all', 'majority_rag', 'truthfinder_rag', 'arbiter', 'arbiter_shell_naive',
                 'probe_then_rag', 'probe_then_parametric'}]
    check(sum(r['scope'] == 'overall' for r in focus) == 21, 'Naive focus matrix shape')
    write_csv('naive_focused_pairs.csv', focus)
    write_csv('label_disagreements.csv', labels)
    write_csv('lcp_summary.csv', lcp_summaries)
    return overall, labels


def groups_and_counterexamples(episodes, cells, pairs):
    groups = []
    for mode in MODES:
        for condition, world, task in cells:
            by_bytes = defaultdict(list)
            for method in METHODS:
                episode = episodes[(mode, method, condition, world, task)]
                by_bytes[episode.strict].append(episode)
            for blob, group in by_bytes.items():
                if len(group) < 2:
                    continue
                groups.append({'reading_mode': mode, 'condition': condition, 'world': world,
                               'task_id': task, 'strict_sha256': digest(blob),
                               'public_calls': group[0].event_count, 'methods': '/'.join(e.key[1] for e in group),
                               'method_count': len(group), 'pair_incidences': len(group) * (len(group) - 1) // 2,
                               **{f'{label}_distinct_values': len({e.labels[i] for e in group})
                                  for i, label in enumerate(LABELS)},
                               'public_history_distinct': len({e.history for e in group}),
                               'operator_attribution_distinct': len({e.mids for e in group})})
    write_csv('strict_identity_groups.csv', groups)
    counterexamples = []
    for row in pairs:
        if not row['strict_identical'] or not any(row[f'{label}_different'] for label in LABELS):
            continue
        base = (row['reading_mode'], row['condition'], row['world'], row['task_id'])
        a = episodes[(base[0], row['method_A'], *base[1:])]
        b = episodes[(base[0], row['method_B'], *base[1:])]
        counterexamples.append({**row, 'strict_sha256': digest(a.strict),
                                'attribution_A_sha256': digest(a.mids),
                                'attribution_B_sha256': digest(b.mids),
                                'public_history_A_sha256': digest(a.history),
                                'public_history_B_sha256': digest(b.history)})
    write_csv('strict_label_counterexamples.csv', counterexamples,
              fields=list(counterexamples[0]) if counterexamples else list(pairs[0]) + [
                  'strict_sha256', 'attribution_A_sha256', 'attribution_B_sha256',
                  'public_history_A_sha256', 'public_history_B_sha256'])
    for mode in MODES:
        check(sum(r['pair_incidences'] for r in groups if r['reading_mode'] == mode)
              == sum(r['strict_identical'] for r in pairs if r['reading_mode'] == mode),
              ('identity groups/pair counts disagree', mode))
    return groups, counterexamples


def evidence_examples(counterexamples):
    selected = [r for r in counterexamples if r['specific_action_different'] or
                r['specific_contact_different'] or r['public_history_identical']]
    wanted = {(r['reading_mode'], r[f'method_{side}'], r['condition'], r['world'], r['task_id'])
              for r in selected for side in ('A', 'B')}
    records = {}
    with gzip.open(MAIN, 'rt', encoding='utf8') as handle:
        for line in handle:
            rec = json.loads(line)
            key = tuple(rec[k] for k in KEYS)
            if key in wanted:
                records[key] = rec
    check(set(records) == wanted, 'counterexample evidence missing')
    output, overview = [], []
    semantic = {'mine', 'craft_grid', 'smelt', 'check_name'}
    for r in selected:
        key = (r['reading_mode'], r['condition'], r['world'], r['task_id'])
        a = records[(key[0], r['method_A'], *key[1:])]
        b = records[(key[0], r['method_B'], *key[1:])]
        check(len(a['events']) == len(b['events']), 'identical evidence length')
        ia = ib = 0
        differing_intent = differing_obs = differing_mids = 0
        for i, (ea, eb) in enumerate(zip(a['events'], b['events'])):
            ma = a['used_mechanism_ids'][ia] if ea['method'] in semantic else ''
            mb = b['used_mechanism_ids'][ib] if eb['method'] in semantic else ''
            if ea['method'] in semantic:
                ia += 1
            if eb['method'] in semantic:
                ib += 1
            obs_a = {k: ea[k] for k in ('result', 'success', 'cost', 'item_flow')}
            obs_b = {k: eb[k] for k in ('result', 'success', 'cost', 'item_flow')}
            intent_diff = encoded(ea.get('intent')) != encoded(eb.get('intent'))
            obs_diff = encoded(obs_a) != encoded(obs_b)
            mid_diff = ma != mb
            differing_intent += intent_diff
            differing_obs += obs_diff
            differing_mids += mid_diff
            if intent_diff or obs_diff or mid_diff:
                output.append({**{k: r[k] for k in ('reading_mode', 'method_A', 'method_B',
                                                    'condition', 'world', 'task_id')},
                               'event_index_0based': i, 'method': ea['method'],
                               'params_sha256': digest(encoded(ea['params'])),
                               'params_identical': int(encoded(ea['params']) == encoded(eb['params'])),
                               'intent_A': ea.get('intent'), 'intent_B': eb.get('intent'),
                               'public_observations_identical': int(not obs_diff),
                               'operator_A': ma, 'operator_B': mb,
                               'operator_identical': int(not mid_diff)})
        overview.append({**{k: r[k] for k in ('reading_mode', 'method_A', 'method_B',
                                             'condition', 'world', 'task_id')},
                         'public_calls': len(a['events']),
                         'intent_different_events': differing_intent,
                         'observation_different_events': differing_obs,
                         'attribution_different_events': differing_mids,
                         'penultimate_method_A': a['events'][-2]['method'],
                         'last_method_A': a['events'][-1]['method'],
                         'reader_queries_identical': r['reader_queries_identical'],
                         'plan_acceptance_different': r['plan_acceptance_different'],
                         'specific_action_different': r['specific_action_different'],
                         'specific_contact_different': r['specific_contact_different']})
    write_csv('counterexample_event_metadata.csv', output)
    write_csv('counterexample_overview.csv', overview)


def summary(episodes, pairs, matrix, labels, groups, counterexamples):
    result = {'population': len(episodes), 'methods': len(METHODS), 'pairs_per_mode': 45,
              'cells_per_pair': 374, 'paired_cells': len(pairs), 'LLM_calls': 0, 'new_episodes': 0,
              'w11_sensitive_episodes': sum(e.sensitive for e in episodes.values()), 'modes': {}}
    mode_summaries = []
    for mode in MODES:
        rows = [r for r in pairs if r['reading_mode'] == mode]
        identical = [r for r in rows if r['strict_identical']]
        item = {'paired_cells': len(rows), 'strict_identical_pairs': len(identical),
                'normalized_identical_pairs': sum(r['normalized_identical'] for r in rows),
                'method_names_identical_pairs': sum(r['method_names_identical'] for r in rows),
                'unique_episode_traces': len({e.strict for e in episodes.values() if e.key[0] == mode}),
                'identity_groups': sum(r['reading_mode'] == mode for r in groups)}
        for label in LABELS:
            collisions = [r for r in identical if r[f'{label}_different']]
            item.update({f'{label}_strict_disagreements': len(collisions),
                         f'{label}_strict_disagreement_rate': len(collisions) / len(identical) if identical else None,
                         f'{label}_strict_disagreements_w11_neither':
                         sum(r['w11_pair_stratum'] == 'neither' for r in collisions),
                         f'{label}_strict_disagreements_w11_any':
                         sum(r['w11_pair_stratum'] != 'neither' for r in collisions),
                         f'{label}_commands_intent_disagreements':
                         sum(r[f'{label}_different'] and r['commands_and_intent_identical'] for r in rows),
                         f'{label}_public_history_disagreements':
                         sum(r[f'{label}_different'] and r['public_history_identical'] for r in rows)})
        result['modes'][mode] = item
        mode_summaries.append({'reading_mode': mode, **item})
    write_csv('mode_summary.csv', mode_summaries)
    # Aggregate within, never across, modes; retain all W11 pair strata.
    strata = []
    for mode in MODES:
        for stratum in ('all', 'neither', 'A_only', 'B_only', 'both', 'any_sensitive'):
            source = [r for r in labels if r['reading_mode'] == mode and r['scope'] == 'overall'
                      and r['projection'] == 'strict' and r['w11_pair_stratum'] == stratum]
            identical = sum(r['identical_cells'] for r in source)
            for label in LABELS:
                different = sum(r[f'{label}_disagreements'] for r in source)
                strata.append({'reading_mode': mode, 'w11_pair_stratum': stratum, 'label': label,
                               'paired_cells': sum(r['stratum_paired_cells'] for r in source),
                               'strict_identical_cells': identical, 'different_labels': different,
                               'rate_among_identical': different / identical if identical else ''})
    write_csv('mode_w11_label_strata.csv', strata)
    lowest = []
    for mode in MODES:
        for level in LEVELS:
            rows = [r for r in matrix if r['reading_mode'] == mode]
            minimum = min(r[f'{level}_identical_cells'] for r in rows)
            for row in rows:
                if row[f'{level}_identical_cells'] == minimum:
                    lowest.append({**row, 'projection': level, 'minimum_identical_cells': minimum})
    write_csv('lowest_identity_pairs.csv', lowest)
    # Unique episode incidences and unique context cells are distinct denominators.
    unique = []
    for mode in MODES:
        for label in LABELS:
            for level, flag in [('strict', 'strict_identical'), ('public_history', 'public_history_identical')]:
                selected = [r for r in pairs if r['reading_mode'] == mode and
                            r[flag] and r[f'{label}_different']]
                eps, sensitive_eps, contexts = set(), set(), set()
                for row in selected:
                    context = (mode, row['condition'], row['world'], row['task_id'])
                    contexts.add(context)
                    for side in ('A', 'B'):
                        key = (mode, row[f'method_{side}'], *context[1:])
                        eps.add(key)
                        if episodes[key].sensitive:
                            sensitive_eps.add(key)
                unique.append({'reading_mode': mode, 'projection': level, 'label': label,
                               'disagreeing_pair_cells': len(selected), 'unique_episodes': len(eps),
                               'unique_context_cells': len(contexts),
                               'unique_w11_sensitive_episodes': len(sensitive_eps)})
    write_csv('unique_collision_counts.csv', unique)
    example = [r for r in groups if r['reading_mode'] == 'all_public' and
               r['condition'] == 'mismatched_old2new' and r['world'] == '1.21.11' and
               r['task_id'] == 'pla_010']
    result['named_example'] = example
    (OUT / 'SUMMARY.json').write_text(json.dumps(result, indent=2) + '\n')
    report(result, matrix, lowest, pairs, labels)
    return result


def report(result, matrix, lowest, pairs, labels):
    lines = ['# W12: cross-method action identity and label-function audit', '',
             'Post-review; preregistration committed before calculation. Zero LLM calls,',
             'zero new episodes. Frozen sources, releases and other processes unchanged.', '',
             '## Definitions and completeness', '',
             '11,220 seed-deduplicated records; ten executable methods, three separate modes,',
             '45 method pairs per mode, 374 cells per pair (50,490 pair-cell incidences).',
             'All eight logged public methods are retained, including inventory and submit',
             'with its full submitted payload. Strict = byte comparison of canonical JSON',
             'method/params projections, NOT original transport/whitespace bytes.',
             'Normalized = only executor-supported item namespace and unit-cell aliases;',
             'no layout rotation/mirroring, resource-node pooling or invalid-number coercion.',
             'Name-only = full ordered method-name list. See prereg for exact definitions.', '',
             'All event arrays passed schema, sequence and baseline-label checks. A stored',
             'array cannot prove instrumentation outside it was complete. The pipeline reader',
             'queries SearchBackend outside the world API and stores query/chunk summaries in',
             'reader.search_events (p6_execution.py:246), not interleaved world events.',
             'Thus these are complete archived WORLD API traces, not complete internal',
             'retrieval/compilation histories. completeness.csv reports both call counts.',
             'report_version stores only guess_sha256; raw guesses are not reconstructible.',
             'No new score or unlogged API call was imputed.', '',
             '## Mode-specific population proportions', '',
             '|Mode|Strict identical / paired cells|Normalized|Name-only|',
             '|---|---:|---:|---:|']
    for mode, r in result['modes'].items():
        n = r['paired_cells']
        lines.append(f"|{mode}|{r['strict_identical_pairs']}/{n} ({100*r['strict_identical_pairs']/n:.2f}%)|"
                     f"{r['normalized_identical_pairs']}/{n}|{r['method_names_identical_pairs']}/{n}|")
    lines += ['', 'These are method-pair incidences, not independent episode samples.',
              'No cross-mode percentage is reported. pair_matrix.csv gives all 135 rows,',
              'each denominator 374; condition_pair_matrix.csv preserves all eleven cells',
              'and separately labelled condition/overlapping mismatch-union summaries.', '',
              '## Naive versus requested comparison methods', '',
              '|Mode|Other method|Strict / 374|Both mismatches / 68|Plan-label disagreements / strict-identical|',
              '|---|---|---:|---:|---:|']
    focus = read_csv(OUT / 'naive_focused_pairs.csv')
    for r in focus:
        if r['scope'] != 'overall':
            continue
        other = r['method_B'] if r['method_A'] == 'naive_rag' else r['method_A']
        wrong = next(x for x in focus if x['reading_mode'] == r['reading_mode'] and
                     x['method_A'] == r['method_A'] and x['method_B'] == r['method_B'] and
                     x['scope'] == 'mismatched_union')
        disagreement = next(x for x in labels if x['reading_mode'] == r['reading_mode'] and
                            x['method_A'] == r['method_A'] and x['method_B'] == r['method_B'] and
                            x['scope'] == 'overall' and x['projection'] == 'strict' and
                            x['w11_pair_stratum'] == 'all')
        lines.append(f"|{r['reading_mode']}|{other}|{r['strict_identical_cells']}/374|"
                     f"{wrong['strict_identical_cells']}/68|"
                     f"{disagreement['plan_acceptance_disagreements']}/{disagreement['identical_cells']}|")
    lines += ['', '## Identical commands, different labels', '',
              'A label disagreement denominator is identical pair-cells, not all 374.',
              'The CSV additionally reports disagreements/all paired cells; zero identical',
              'cells has an undefined conditional fraction. W11 membership is the union of',
              'action/contact sensitivity (39 unique episodes), NOT all 329 tied episodes.', '',
              '|Mode|Label|Different / strict-identical|W11 neither / any|Still different with public history|',
              '|---|---|---:|---:|---:|']
    for mode, r in result['modes'].items():
        for label in LABELS:
            lines.append(f"|{mode}|{label}|{r[label+'_strict_disagreements']}/{r['strict_identical_pairs']}|"
                         f"{r[label+'_strict_disagreements_w11_neither']} / {r[label+'_strict_disagreements_w11_any']}|"
                         f"{r[label+'_public_history_disagreements']}|")
    lines += ['', 'label_disagreements.csv gives each method-pair/mode/scope/projection and',
              'W11 neither/A_only/B_only/both/any stratum, with numerator and denominator.',
              'unique_collision_counts.csv distinguishes unique episodes and context cells',
              'from repeated pair incidences. strict_label_counterexamples.csv contains every',
              'strict-command collision, public-history/intent checks and provenance hashes.',
              'mode_w11_label_strata.csv gives aggregate conditional numerators AND denominators',
              'for the 39-episode stratum and its complement, separately within each mode.', '',
              '### Functionality conclusion', '']
    for label in LABELS:
        affected = [m for m in MODES if result['modes'][m][label+'_public_history_disagreements']]
        if affected:
            lines.append(f'- {label}: same-context, same recorded public history can have different labels '
                         f'({", ".join(affected)}). It is NOT solely a function of that projection.')
        else:
            lines.append(f'- {label}: no recorded-public-history collision found; this does NOT prove '
                         'global functionality. Check command-only and intent strata separately.')
    lines += ['', 'The scorer takes catalogue/operator attribution as an additional input:',
              'scripts/w9_runtime.py:111 zips semantic actions with used_mechanism_ids before',
              'classify_actions_v3/intrinsic_discriminative. Plan-level HTR_commit is a declared',
              'plan-source/intent annotation. Neither identity of commands alone nor a private',
              'provenance association establishes that a method merely relabels all behavior.', '',
              'All five specific-action/contact collisions are the same pla_010 context:',
              'ARBITER versus shell/majority/Naive/TruthFinder/Verify-All. They involve six',
              'unique W11-sensitive episodes, not five independent tasks. The episode labels',
              'are 0 for ARBITER and 1 for the five counterparts. Their intents and private',
              'attributions differ. Event-level evidence is in counterexample_event_metadata.csv;',
              'the classifier and intrinsic predicate do not read intent (measurement_v3.py',
              'classify_actions_v3 / intrinsic_discriminative). Thus different intent is NOT',
              'by itself an explanation for the specific-score differences. Conversely, with',
              'intent included these are not byte-identical full public histories.', '',
              'The remaining nine plan-label collisions per all_public/llm_only mode involve',
              'Parametric versus other methods on fea_007 in the matched 1.19/1.21.11 cells.',
              'Commands, intent and recorded observations match but private operator IDs',
              'differ; reader queries also differ. This refutes a world-API-only function',
              'for the plan label, NOT a claim that entire retrieval histories are identical.', '',
              '## Prefixes, counterexamples and limits', '',
              'lcp_histograms.csv gives the exact call-prefix-length distribution for each of',
              '45 pairs x 3 modes x 3 projections; lcp_summary.csv gives min/median/max,',
              'mean and mean fraction of the shorter trace. Full paired_cells.csv allows',
              'inspection of late divergences without pretending an unequal tail is a match.', '',
              'Lowest strict-identity pairs (all ties):']
    for row in lowest:
        if row['projection'] == 'strict':
            lines.append(f"- {row['reading_mode']}: {row['method_A']} vs {row['method_B']}: "
                         f"{row['strict_identical_cells']}/374 identical; "
                         f"{row['strict_different_cells']}/374 different.")
    lines += ['', 'Named motivating cell (not an estimator):']
    for row in result['named_example']:
        lines.append(f"- all_public / mismatched_old2new / 1.21.11 / pla_010: "
                     f"{row['methods']}, {row['public_calls']} calls, "
                     f"{row['public_history_distinct']} distinct public histories.")
    lines += ['', 'For the 39-call group, call 38 is submit and call 39 is the framework final',
              'inventory read (p6_execution.py:716). Counting through submit gives 38 calls;',
              'the preregistered full-array definition includes the final read, hence 39.',
              'No definition was changed to fit the motivating sample.']
    lines += ['', 'Different-cell counts are explicit in every matrix row. Identical cells',
              'do not imply method equivalence outside them, equal internal beliefs, or equal',
              'retrieval histories. Submit is included rather than inferred from final scores.',
              'Probe-then-RAG logs report_version in every cell (unlike the other methods),',
              'so its zero full-trace matches include this diagnostic behavior; zero matches',
              'is not itself a claim about task success or a wholly different goal plan.',
              'The motivating 17/47 stratified audit proportion is NOT extrapolated.', '',
              '## Two reasoning passes / validation', '',
              'Pass 1 distinguished commands, intent, observations and private attribution',
              'before computing rates; these distinctions were preregistered.',
              'Pass 2 checked strict => normalized => names, history => intent => strict,',
              'direct bytes against hashes, LCP limits/full-match equivalence, 50,490 pair',
              'counts, 135 overall rows, group/pair conservation, all 11,220 W9/W11 baselines',
              'and 39 unique W11 sensitivity members. No definitions were changed after results.',
              'All gates passed. See input_manifest.csv and OUTPUTS.sha256 for provenance.', '']
    (OUT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf8')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    resource_gate('entry')
    source_paths = [MAIN, W11, W9, PREREG, Path(__file__), ROOT / 'sim/semantic_agent.py',
                    ROOT / 'scripts/w9_runtime.py', ROOT / 'tests/test_w12_trajectory_identity.py',
                    ROOT.parent / 'arbiter/src/arbiter/evaluation/measurement_v3.py',
                    ROOT.parent / 'arbiter/src/arbiter/evaluation/p6_execution.py']
    before = {p: sha(p) for p in source_paths}
    write_csv('input_manifest.csv', [{'path': str(p.relative_to(ROOT)) if p.is_relative_to(ROOT)
                                     else 'arbiter_readonly/' + str(p.relative_to(ROOT.parent / 'arbiter')),
                                     'sha256': h, 'bytes': p.stat().st_size}
                                    for p, h in before.items()])
    episodes, cells = load()
    print('Validated all 11,220 frozen records and W9/W11 labels', flush=True)
    resource_gate('comparison')
    pairs = compare(episodes, cells)
    print('Compared all 50,490 paired cells', flush=True)
    matrix, labels = matrices(pairs)
    groups, counterexamples = groups_and_counterexamples(episodes, cells, pairs)
    evidence_examples(counterexamples)
    result = summary(episodes, pairs, matrix, labels, groups, counterexamples)
    check(all(sha(p) == h for p, h in before.items()), 'input changed during audit')
    resource_gate('complete')
    (OUT / 'VALIDATION.json').write_text(json.dumps({
        'all_gates_passed': True, 'frozen_records': len(episodes), 'w9_w11_matches': len(episodes),
        'w11_sensitive': sum(e.sensitive for e in episodes.values()), 'overall_rows': len(matrix),
        'paired_cells': len(pairs), 'inputs_unchanged': True, 'network_calls': 0,
        'new_episodes': 0, 'LLM_calls': 0, 'python': sys.version.split()[0],
        'PYTHONHASHSEED': os.environ.get('PYTHONHASHSEED'),
    }, indent=2) + '\n')
    manifest = ''.join(f'{sha(p)}  {p.name}\n' for p in sorted(OUT.iterdir())
                       if p.is_file() and p.name != 'OUTPUTS.sha256')
    (OUT / 'OUTPUTS.sha256').write_text(manifest)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'STOP.json').write_text(json.dumps({'status': 'STOP', 'error': str(exc),
                                                  'new_episodes': 0, 'LLM_calls': 0}, indent=2) + '\n')
        raise
