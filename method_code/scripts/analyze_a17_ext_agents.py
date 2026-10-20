"""Zero-call A17 oracle lengths, frozen rescoring and family-paired exports."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from arbiter.evaluation.consequence_htr import inventory_dependencies
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import classify_actions_v3, intrinsic_discriminative
from scripts.analyze_cds_agents_20261017 import inputs_absent
from scripts.run_measurement_v3_20261014 import external_stale_map
from scripts.run_a17_ext_agents import ROOT, WEB, OUT, REPORT, METHODS, sha

SEED = 20261017
DRAWS = 10000
CANONICAL_SOURCE = WEB / 'reports/paperA/w11_attribution/episode_attribution_sensitivity.csv'
METRICS = ('SR', 'feasible_SR', 'overconservatism', 'query_contact', 'action_contact',
           'discriminative_contact', 'propagation_lower', 'propagation_upper', 'HTR_all',
           'semantic_actions', 'semantic_cost', 'search_calls', 'model_calls')
COMPARISONS = (('mismatched_old2new', 'matched_1.21.11'),
               ('mismatched_new2old', 'matched_1.16.5'))


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def csv_out(name, data, fields=None):
    data = list(data)
    names = fields or list(dict.fromkeys(k for row in data for k in row))
    if not names:
        names = ['status']
    with (REPORT / name).open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=names, lineterminator='\n')
        writer.writeheader()
        for row in data:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def distribution(values):
    if not values:
        return dict(n=0, minimum=None, q25=None, median=None, q75=None, maximum=None, mean=None)
    a = np.asarray(values, dtype=float)
    return dict(n=len(a), minimum=float(a.min()), q25=float(np.quantile(a, .25)),
                median=float(np.median(a)), q75=float(np.quantile(a, .75)),
                maximum=float(a.max()), mean=float(a.mean()))


def bootstrap_family(values):
    """Each observation is (family, value); families, not rows, are units."""
    grouped = defaultdict(list)
    for family, value in values:
        grouped[family].append(float(value))
    means = np.asarray([np.mean(grouped[k]) for k in sorted(grouped)])
    result = dict(estimate=float(means.mean()) if len(means) else None,
                  low=None, high=None, families=len(means), n=len(values),
                  draws=DRAWS, seed=SEED, status='OK', zero_denominator_draws=0)
    if len(means) < 2:
        result['status'] = 'NO_INTERVAL_less_than_two_eligible_families'
        result['zero_denominator_draws'] = DRAWS if not len(means) else 0
    else:
        sample = np.random.default_rng(SEED).integers(0, len(means), size=(DRAWS, len(means)))
        estimates = means[sample].mean(axis=1)
        result.update(low=float(np.quantile(estimates, .025)), high=float(np.quantile(estimates, .975)))
    return result


def eligible(row, metric):
    return metric not in ('feasible_SR', 'overconservatism') or bool(row['true_feasible'])


def metric_value(row, metric):
    return row['SR'] if metric == 'feasible_SR' else row[metric]


def oracle_lengths():
    ext = {r['id']: r for r in rows(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    main = {r['id']: r for r in rows(WEB / 'data/tasks.jsonl') if r.get('split') == 'eval'}
    config = json.loads((WEB / 'config/ext_v2/web_cells.json').read_text())
    cds = json.loads((WEB / 'cds_materials/cds_manifest.json').read_text())['rows']
    values = []

    def add(scope, task, world, instance_id):
        plan = task['oracle_plans'].get(world)
        feasible = bool(task['feasible'][world])
        available = bool(plan is not None and plan.get('feasible') is not False)
        actions = plan.get('actions', []) if available else []
        values.append(dict(scope=scope, task_id=task['id'], world=world, instance_id=instance_id,
                           true_feasible=int(feasible), plan_available=int(available),
                           semantic_actions=len([a for a in actions if a['method'] in ACTION_METHODS]) if feasible and available else None,
                           primary_semantic_cost=plan.get('primary_cost') if feasible and available else None,
                           plan_interface_calls=len(actions) if feasible and available else None,
                           status='OK' if available or not feasible else 'NOT_REPORTED_missing_feasible_plan'))

    for task_id in config['web_task_ids']:
        for world in ('1.16.5', '1.19', '1.21.11'):
            add('EXT_v2_web_12x3', ext[task_id], world, task_id + '__' + world)
    for row in cds:
        add('CDS_52_directed', ext[row['task_id']], row['target_world'], row['instance_id'])
    for task_id, world in sorted({(r['task_id'], r['target_world']) for r in cds}):
        add('CDS_unique_task_world', ext[task_id], world, task_id + '__' + world)
    for task in main.values():
        for world in ('1.16.5', '1.19', '1.21.11'):
            add('main_34x3', task, world, task['id'] + '__' + world)
    csv_out('oracle_plan_lengths.csv', values)
    summaries = []
    for scope in sorted({r['scope'] for r in values}):
        scoped = [r for r in values if r['scope'] == scope]
        for world in ('all', '1.16.5', '1.19', '1.21.11'):
            group = [r for r in scoped if world == 'all' or r['world'] == world]
            for metric in ('semantic_actions', 'primary_semantic_cost', 'plan_interface_calls'):
                summaries.append(dict(scope=scope, world=world, metric=metric, instances=len(group),
                                      feasible=sum(r['true_feasible'] for r in group),
                                      infeasible=sum(not r['true_feasible'] for r in group),
                                      **distribution([r[metric] for r in group if r[metric] is not None])))
    csv_out('oracle_length_summary.csv', summaries)
    observed = []
    sources = [('EXT_v2_web', WEB / 'reports/paperA/ext_v2/web_run/episodes.jsonl'),
               ('CDS_K_directed', WEB / 'reports/paperA/ext_v2/cds_run/episodes.jsonl'),
               ('main', WEB / 'release/package/assets/frozen/main.jsonl.gz')]
    for scope, path in sources:
        if not path.is_file():
            continue
        for row in rows(path):
            if row.get('method') != 'oracle':
                continue
            if scope == 'CDS_K_directed':
                if row.get('cds_condition') != 'K':
                    continue
            elif row.get('condition') != 'matched_' + row['world'] or row.get('reading_mode') != 'all_public':
                continue
            events = row['events']
            observed.append(dict(scope=scope, task_id=row['task_id'], world=row['world'],
                                 instance_id=row.get('cds_instance_id', ''), interface_calls=len(events),
                                 semantic_calls=sum(e['method'] in ACTION_METHODS for e in events),
                                 inventory_calls=sum(e['method'] == 'inventory' for e in events),
                                 submit_calls=sum(e['method'] == 'submit' for e in events),
                                 semantic_cost=sum(float(e['cost']['primary']) for e in events),
                                 true_feasible=int(row['true_feasible'])))
    csv_out('oracle_observed_calls.csv', observed)
    observed_summary = []
    for scope in sorted({r['scope'] for r in observed}):
        for world in ('all', '1.16.5', '1.19', '1.21.11'):
            for feasible in (0, 1):
                group = [r for r in observed if r['scope'] == scope and r['true_feasible'] == feasible
                         and (world == 'all' or r['world'] == world)]
                for metric in ('interface_calls', 'semantic_calls', 'inventory_calls', 'submit_calls', 'semantic_cost'):
                    observed_summary.append(dict(scope=scope, world=world, true_feasible=feasible, metric=metric,
                                                 **distribution([r[metric] for r in group])))
    csv_out('oracle_observed_call_summary.csv', observed_summary)
    return summaries


def score_record(record, events, task, conflicts, nodes, source, stale_override=None):
    world = record['world']
    stale_map = external_stale_map(events, world, conflicts, nodes) if stale_override is None else stale_override
    labels = classify_actions_v3(events, stale_by_event=stale_map, conflicts=conflicts,
                                 resource_nodes=nodes, target=task['target'],
                                 episode_success=bool(record.get('success', record.get('metrics', {}).get('SR', 0))))
    dependencies = inventory_dependencies(events)
    action_rows = []
    for label in labels:
        index = int(label['event_index'])
        event = events[index]
        disc = bool(intrinsic_discriminative(event, label['stale_mechanism_ids'], world=world,
                                            conflicts=conflicts, resource_nodes=nodes) or label['referenced_at'])
        predicted = set(label['predicted_products'])
        fifo = any(consumer > index and name in predicted for consumer, name, _ in dependencies.get(index, []))
        absent = any(inputs_absent(events, int(later), predicted) for later in label['referenced_at'])
        base = bool(disc and label['mismatch'] and label['v3_label'] == 'commit')
        rules = dict(rule_i=int(base and (fifo or absent)),
                     rule_ii=int(base and bool(label['repeated_at'])),
                     rule_iii=int(base and label['affirmative_submit_at'] != ''))
        action_rows.append(dict(**label, **rules, discriminative=int(disc),
                                propagation=int(any(rules.values())),
                                action_type='query' if event['method'] == 'check_name' else 'action',
                                task_id=record['task_id'], world=world, condition=record['condition'],
                                episode_method=record['method'], source=source,
                                action_params=event.get('params'), world_feedback=event.get('result'),
                                canonical_attribution_status='NOT_IDENTIFIABLE_no_captured_tied_operator_pool'))
    scoring_row = dict(record)
    scoring_row.setdefault('model_calls', 0)
    scoring_row['_max_actions'] = 90
    scoring_row['_max_calls'] = 40
    scoring_row.setdefault('true_feasible', task['feasible'][world])
    scoring_row['metrics'] = dict(record.get('metrics', {}))
    scoring_row['metrics'].setdefault('SR', record.get('success', 0))
    gold = outcome(scoring_row, events, task)
    disc_rows = [r for r in action_rows if r['discriminative']]
    lower = int(any(r['propagation'] for r in disc_rows))
    record_out = dict(method=record['method'], task_id=task['id'], world=world,
                      condition=record['condition'], family_id=task['independent_family_id'],
                      source=source, reading_mode='all_public', seed=record.get('seed'),
                      true_feasible=int(task['feasible'][world]), SR=gold['SR'],
                      feasible_SR=gold['SR'] if task['feasible'][world] else None,
                      overconservatism=gold['false_abstention'], terminal=gold['terminal'],
                      HTR_all=int(bool(labels)), query_contact=int(any(r['action_type'] == 'query' for r in disc_rows)),
                      action_contact=int(any(r['action_type'] == 'action' for r in disc_rows)),
                      discriminative_contact=int(bool(disc_rows)), propagation_lower=lower,
                      propagation_upper=int(lower or any(r['mismatch'] and r['v3_label'] == 'undetermined' for r in disc_rows)),
                      frozen_logged_HTR_all=record.get('metrics', {}).get('HTR_all'),
                      frozen_logged_HTR_commit=record.get('metrics', {}).get('HTR_commit'),
                      semantic_actions=gold['semantic_actions'], semantic_cost=gold['primary_cost'],
                      search_calls=(record.get('reader', {}).get('search_count') if record.get('used_mechanism_ids') is not None
                                    else sum(e['method'] == 'search' for e in events)),
                      search_calls_public_api_log=sum(e['method'] == 'search' for e in events),
                      search_calls_basis=('frozen_reader_report_may_exclude_callback_research' if record.get('used_mechanism_ids') is not None
                                          else 'actual_public_api_log'), model_calls=scoring_row['model_calls'],
                      any_action_failure=gold['any_action_failure'], action_cap_hit=gold['action_cap_hit'],
                      call_cap_hit=gold['call_cap_hit'], input_tokens=record.get('input_tokens', 0),
                      output_tokens=record.get('output_tokens', 0),
                      canonical_query_contact=None, canonical_action_contact=None, canonical_propagation=None,
                      canonical_attribution_status=('NOT_REPORTED_exact_grid_W11_attribution_absent' if record.get('used_mechanism_ids') is not None
                                                    else 'NOT_IDENTIFIABLE_no_operator_provenance'))
    return record_out, action_rows


def aggregate(data):
    output = []
    for method in sorted({r['method'] for r in data}):
        group = [r for r in data if r['method'] == method]
        selections = [('all_cells', group), ('matched_all', [r for r in group if r['condition'].startswith('matched_')]),
                      ('mismatched_all', [r for r in group if r['condition'].startswith('mismatched_')])]
        selections += [(c, [r for r in group if r['condition'] == c]) for c in sorted({r['condition'] for r in group})]
        for condition, selected in selections:
            for metric in METRICS + ('canonical_query_contact', 'canonical_action_contact', 'canonical_propagation'):
                filtered = [r for r in selected if eligible(r, metric) and r.get(metric) is not None]
                output.append(dict(method=method, condition=condition, metric=metric,
                                   observed_episodes=len(selected), eligible_episodes=len(filtered),
                                   raw_numerator=sum(metric_value(r, metric) for r in filtered),
                                   episode_weighted_estimate=float(np.mean([metric_value(r, metric) for r in filtered])) if filtered else None,
                                   **bootstrap_family([(r['family_id'], metric_value(r, metric)) for r in filtered])))
    return output


def paired_contrasts(data):
    output, missing = [], []
    for method in sorted({r['method'] for r in data}):
        selected = [r for r in data if r['method'] == method]
        by_key = {(r['condition'], r['task_id'], r['world']): r for r in selected}
        pools = defaultdict(list)
        for mismatch, matched in COMPARISONS:
            world = '1.21.11' if mismatch.endswith('old2new') else '1.16.5'
            task_ids = sorted({r['task_id'] for r in data})
            for task_id in task_ids:
                a, b = by_key.get((mismatch, task_id, world)), by_key.get((matched, task_id, world))
                if a is None or b is None:
                    missing.append(dict(method=method, contrast=mismatch + '_minus_' + matched,
                                        task_id=task_id, world=world, missing_mismatch=a is None, missing_match=b is None))
                    continue
                pools[mismatch].append((a, b))
                pools['combined_directions'].append((a, b))
        for contrast, pairs in pools.items():
            for metric in METRICS + ('canonical_query_contact', 'canonical_action_contact', 'canonical_propagation'):
                eligible_pairs = [(a, b) for a, b in pairs if eligible(a, metric) and eligible(b, metric)
                                  and a.get(metric) is not None and b.get(metric) is not None]
                values = [(a['family_id'], metric_value(a, metric) - metric_value(b, metric)) for a, b in eligible_pairs]
                output.append(dict(method=method, contrast=contrast, metric=metric,
                                   paired_episodes=len(eligible_pairs), direction='mismatched_minus_matched',
                                   **bootstrap_family(values)))
    return output, missing


def cost_summary():
    all_rows = []
    for path in OUT.glob('**/ledger.jsonl'):
        all_rows.extend(dict(r, stage='worker') for r in rows(path))
    for path in (OUT / 'preflight').glob('*_ledger.jsonl'):
        all_rows.extend(dict(r, stage='preflight') for r in rows(path))
    responses = [r for r in all_rows if r.get('input_tokens') is not None]
    failures = [r for r in all_rows if r.get('status') == 'infrastructure_error']
    return dict(request_records=sum('call_type' in r for r in all_rows),
                successful_responses=sum(r.get('status') == 'ok' for r in responses),
                task_responses=sum(r.get('status') == 'ok' and not r.get('canary') for r in responses),
                canary_responses=sum(r.get('status') == 'ok' and bool(r.get('canary')) for r in responses),
                input_tokens=sum(r.get('input_tokens', 0) for r in responses),
                cached_input_tokens=sum(r.get('cached_input_tokens', 0) for r in responses),
                output_tokens=sum(r.get('output_tokens', 0) for r in responses),
                token_rate_estimated_CNY=sum(float(r.get('cost') or 0) for r in responses),
                infrastructure_error_responses=len(failures),
                http_error_counts=dict(Counter(str(r.get('http_status', 'unknown')) for r in failures)),
                server_model_identifiers=sorted({r['model'] for r in responses if isinstance(r.get('model'), str)}),
                provider_invoice='NOT_AVAILABLE', estimated=True,
                token_rates_CNY_per_million=dict(input_uncached=8, input_cached=2, output=28))


def analyze(allow_partial=False):
    REPORT.mkdir(parents=True, exist_ok=True)
    tasks = {r['id']: r for r in rows(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    cells = json.loads((WEB / 'config/ext_v2/web_cells.json').read_text())
    scope_keys = {(t, c['condition'], c['world']) for t in cells['web_task_ids'] for c in cells['cells']}
    conflicts = {r['id']: r for r in load_conflicts(WEB / 'data/conflicts.jsonl')}
    nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
    data, actions, common_actions, exceptions = [], [], [], []
    seen = set()

    def collect(record, events, source):
        key = (record['method'], record['task_id'], record['condition'], record['world'])
        if key in seen:
            raise ValueError('duplicate_a17_comparison_key')
        seen.add(key)
        try:
            scored, annotated = score_record(record, events, tasks[record['task_id']], conflicts, nodes, source)
            common = scored
            for annotation in annotated:
                annotation['attribution_basis'] = 'common_catalog_action_match'
            common_actions.extend(annotated)
            used_ids = record.get('used_mechanism_ids')
            if used_ids is not None:
                semantic_indices = [i for i, e in enumerate(events) if e['method'] in ACTION_METHODS]
                if len(used_ids) != len(semantic_indices):
                    raise ValueError('recorded_operator_alignment')
                stale_map = {i: [mid] for i, mid in zip(semantic_indices, used_ids)
                             if mid in conflicts and conflicts[mid]['truth_vector'].get(record['world']) is False
                             and any(v is True for v in conflicts[mid]['truth_vector'].values())}
                scored, annotated = score_record(record, events, tasks[record['task_id']], conflicts, nodes, source,
                                                  stale_override=stale_map)
                scored['frozen_attribution_basis'] = 'recorded_operator_id'
                for annotation in annotated:
                    annotation['attribution_basis'] = 'recorded_operator_id'
            else:
                scored['frozen_attribution_basis'] = 'external_catalog_action_match'
            for metric in ('query_contact', 'action_contact', 'discriminative_contact', 'propagation_lower', 'propagation_upper', 'HTR_all'):
                scored['common_action_match_' + metric] = common[metric]
            data.append(scored)
            actions.extend(annotated)
        except Exception as exc:
            exceptions.append(dict(method=record['method'], task_id=record['task_id'], world=record['world'],
                                   condition=record['condition'], error_type=type(exc).__name__, status='NOT_REPORTED_scoring_exception'))

    for path in sorted(OUT.glob('worker_*/episodes.jsonl')):
        for record in rows(path):
            collect(record, list(rows(ROOT / record['action_log'])), 'A17_new_llm')
    llm_count = len([r for r in data if r['source'] == 'A17_new_llm'])
    if not allow_partial and llm_count != 180:
        raise ValueError('incomplete_llm_scope_use_allow_partial_to_export_missingness')
    sources = [('naive_rag', WEB / 'reports/paperA/ext_v2/web_run/episodes.jsonl'),
               ('cp_rag', WEB / 'reports/paperA/review_v14_zero_call/cp_rag/episodes.jsonl'),
               ('vb_rag', WEB / 'reports/paperA/w13_vbrag/episodes.jsonl')]
    baseline_status = []
    baseline_snapshots = []
    for method, path in sources:
        count = 0
        digest = None
        if path.is_file():
            snapshot = list(rows(path))
            digest = sha(path)
            candidates = [r for r in snapshot if r.get('method') == method and r.get('reading_mode') == 'all_public'
                          and (r['task_id'], r['condition'], r['world']) in scope_keys]
            for record in candidates:
                collect(record, record['events'], str(path.relative_to(WEB)))
                baseline_snapshots.append(record)
                count += 1
        baseline_status.append(dict(method=method, expected=60, existing_episodes=count, source_sha256=digest,
                                    status='COMPLETE' if count == 60 else 'NOT_AVAILABLE' if not count else 'PARTIAL'))
    # W13's "canonical" filename is terminal/cost alignment, not W11 attribution.
    canonical_path = CANONICAL_SOURCE
    canonical_input = []
    if canonical_path.is_file():
        with canonical_path.open(newline='') as handle:
            canonical_input = [r for r in csv.DictReader(handle) if r.get('reading_mode') == 'all_public'
                               and (r['task_id'], r['condition'], r['world']) in scope_keys]
    canonical_index = defaultdict(list)
    for row in canonical_input:
        canonical_index[(row['method'], row['task_id'], row['condition'], row['world'])].append(row)
    for row in data:
        candidates = canonical_index.get((row['method'], row['task_id'], row['condition'], row['world']), [])
        if len(candidates) == 1:
            candidate = candidates[0]
            row.update(canonical_query_contact=int(candidate['query_repaired']),
                       canonical_action_contact=int(candidate['action_repaired']),
                       canonical_attribution_status='AVAILABLE_official_exact_grid_export')
        elif len(candidates) > 1:
            row['canonical_attribution_status'] = 'NOT_REPORTED_duplicate_official_keys'
    csv_out('official_canonical_snapshot.csv', canonical_input)
    table = aggregate(data)
    differences, missing_pairs = paired_contrasts(data)
    csv_out('per_episode.csv', data)
    csv_out('per_stale_action.csv', actions)
    csv_out('common_action_match_witnesses.csv', common_actions)
    csv_out('main_table.csv', table)
    csv_out('mismatch_contrasts.csv', differences)
    csv_out('unpaired_instances.csv', missing_pairs)
    csv_out('scoring_exceptions.csv', exceptions)
    csv_out('pipeline_coverage.csv', baseline_status)
    with (OUT / 'pipeline_comparison_snapshot.jsonl').open('w') as handle:
        for record in baseline_snapshots:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + '\n')
    terminals = Counter((r['method'], r['condition'], r['terminal']) for r in data)
    csv_out('terminal_distribution.csv', [dict(method=m, condition=c, terminal=t, n=n) for (m, c, t), n in sorted(terminals.items())])
    canonical = []
    for method in METHODS + ('naive_rag', 'cp_rag', 'vb_rag'):
        scoped = [r for r in data if r['method'] == method]
        available = [r for r in scoped if r['canonical_query_contact'] is not None]
        canonical.append(dict(method=method, expected_episodes=60, aggregation='episode_weighted_summary',
                              frozen_observed_episodes=len(scoped),
                              canonical_observed_episodes=len(available),
                              canonical_attribution='AVAILABLE_official_exact_grid_export' if len(available) == 60
                              else 'NOT_IDENTIFIABLE_no_operator_provenance' if method in METHODS
                              else 'NOT_REPORTED_exact_grid_W11_attribution_absent',
                              frozen_query_contact=sum(r['query_contact'] for r in scoped) / len(scoped) if scoped else None,
                              frozen_action_contact=sum(r['action_contact'] for r in scoped) / len(scoped) if scoped else None,
                              canonical_query_contact=sum(r['canonical_query_contact'] for r in available) / len(available) if available else None,
                              canonical_action_contact=sum(r['canonical_action_contact'] for r in available) / len(available) if available else None,
                              canonical_propagation=(sum(r['canonical_propagation'] for r in available) / len(available)
                                                     if available and all(r['canonical_propagation'] is not None for r in available) else None)))
    csv_out('attribution_comparison.csv', canonical)
    with sqlite3.connect(OUT / 'task_queue.sqlite') as db:
        db.row_factory = sqlite3.Row
        coverage = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY method,condition,task_id')]
    csv_out('planned_episode_coverage.csv', coverage)
    costs = cost_summary()
    (REPORT / 'actual_usage_and_estimated_cost.json').write_text(json.dumps(costs, indent=2, sort_keys=True) + '\n')
    manifest = json.loads((OUT / 'run_manifest.json').read_text())
    changed = [name for name, digest in manifest['webcraft_input_hashes'].items()
               if not (WEB / name).is_file() or sha(WEB / name) != digest]
    audit = dict(prereg_commit='77324cc', observed_llm_episodes=llm_count,
                 run_complete=llm_count == 180, baseline_coverage=baseline_status,
                 scoring_exceptions=exceptions, changed_pinned_inputs=changed,
                 status='OK' if not changed else 'SOURCE_CHANGED_review_required',
                 unchanged_external_runner=sha(ROOT / 'src/arbiter/evaluation/external_agents.py') == manifest['external_runner_sha256'],
                 unchanged_prompt_hashes={m: hashlib.sha256(__import__('arbiter.evaluation.external_agents', fromlist=['public_system_prompt']).public_system_prompt(m).encode()).hexdigest() == manifest['prompt_sha256'][m] for m in METHODS})
    audit['analysis_input_hashes'] = {
        str(path.relative_to(WEB)): sha(path) for path in (
            WEB / 'data/tasks.jsonl', WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl',
            WEB / 'cds_materials/cds_manifest.json', WEB / 'reports/paperA/ext_v2/web_run/episodes.jsonl',
            WEB / 'reports/paperA/ext_v2/cds_run/episodes.jsonl',
            WEB / 'release/package/assets/frozen/main.jsonl.gz') if path.is_file()}
    audit['scorer_source_hashes'] = {str(path.relative_to(ROOT)): sha(path) for path in (
        ROOT / 'src/arbiter/evaluation/measurement_v3.py',
        ROOT / 'src/arbiter/evaluation/consequence_htr.py',
        ROOT / 'src/arbiter/evaluation/external_scoring.py',
        ROOT / 'src/arbiter/evaluation/external_rescore.py',
        ROOT / 'scripts/analyze_cds_agents_20261017.py')}
    audit['official_canonical_source'] = (dict(path=str(canonical_path.relative_to(WEB)), sha256=sha(canonical_path),
                                              exact_grid_rows=len(canonical_input)) if canonical_path.is_file() else None)
    (REPORT / 'fairness_and_integrity.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
    report = ['# A17 EXT-v2 real-Web external agents', '',
              'Preregistration: `77324cc`. All estimates are frozen before execution; no previous result or method was changed.',
              f'Observed LLM episodes: {llm_count}/180. Planned/observed keys and every missing status: planned_episode_coverage.csv.',
              'Direct, ReAct and Reflexion each have 60 planned task/cell keys. Missing infrastructure episodes are not failures.',
              '', '## Matched feasible SR (equal family weight; bootstrap 10,000, seed 20261017)',
              '| Method | Observed matched | Feasible n | Estimate | 95% CI |', '|---|---:|---:|---:|---|']
    for r in table:
        if r['condition'] == 'matched_all' and r['metric'] == 'feasible_SR':
            report.append(f"| {r['method']} | {r['observed_episodes']} | {r['eligible_episodes']} | {r['estimate']} | [{r['low']}, {r['high']}] |")
    report += ['', 'CDS A8 ReAct/K=19/30 (0.6333) is descriptive only, not paired with this different corpus/material setting.',
               '', '## Mismatched minus same-world matched',
               'Direction-specific and combined family-paired estimates are in mismatch_contrasts.csv; all outcomes/directions are retained.',
               '| Method | Metric | Difference | 95% CI | Paired n |', '|---|---|---:|---|---:|']
    for r in differences:
        if r['contrast'] == 'combined_directions' and r['metric'] in ('SR', 'feasible_SR', 'overconservatism', 'query_contact', 'action_contact', 'propagation_lower'):
            report.append(f"| {r['method']} | {r['metric']} | {r['estimate']} | [{r['low']}, {r['high']}] | {r['paired_episodes']} |")
    report += ['', '## Attribution and limitations',
               'Frozen catalog/action matcher and existing v3-d implementation are unchanged. Per-action witnesses and raw logs are exported.',
               'Primary contact/propagation columns retain frozen attribution: recorded operator IDs for pipelines, catalog action matching for LLMs. frozen_logged_* preserves original logged metrics. common_action_match_* columns and common_action_match_witnesses.csv additionally apply the identical action-matcher projection to every method.',
               'Strict W11 canonical attribution requires captured equivalent operator pools/tied optima, absent in LLM traces. LLM canonical fields are NA, not zero or fabricated catalog choices.',
               'Only exact-grid W11 episode_attribution_sensitivity.csv rows can supply canonical attribution. Its current table contains no EXT-v2 tasks, so those pipeline fields are also NA. official_canonical_snapshot.csv is empty when none exist.',
               'The W13 filename episode_metrics_canonical.csv refers to terminal/denominator/search-cost alignment, not W11 operator attribution. It was inspected but deliberately not used to fill attribution fields; the filename alone is not evidence of the requested estimand.',
               'Mismatch contrasts use real corpus replacement, not literally the CDS additive K+S intervention; coverage is not held constant.',
               'Feasible SR/overconservatism exclude infeasible tasks; overall SR includes correct infeasibility judgments.',
               'Baseline exact-grid coverage and source hashes are in pipeline_coverage.csv; no baseline was rerun.',
               '', '## Oracle task length (zero calls)',
               'oracle_plan_lengths.csv and oracle_length_summary.csv use only saved plans. Infeasible lengths are NA. Empty feasible plans are zero.',
               'Each saved action is one plan interface call; semantic cost may differ for batch smelting. oracle_observed_calls.csv separately records actual saved executor overhead.',
               'oracle_observed_call_summary.csv gives actual interface-call distributions by scope/world and feasibility, without silently imputing overhead.',
               'EXT/CDS inventory supplements differ from main. CDS_52_directed repeats some task/world plans; CDS_unique_task_world discloses unique values.',
               '', '## Usage and operational audit',
               f"Actual successful task responses: {costs['task_responses']}; canary responses: {costs['canary_responses']}; total successful responses: {costs['successful_responses']}.",
               f"Server identifiers: {costs['server_model_identifiers']}; input/output tokens: {costs['input_tokens']}/{costs['output_tokens']}.",
               f"Estimated CNY cost from recorded token usage: {costs['token_rate_estimated_CNY']}. Provider invoice: NOT_AVAILABLE. This is not an invoiced charge.",
               f"Changed pinned WebCraft inputs: {len(changed)}. Scoring exceptions: {len(exceptions)}.",
               'No GPU/model/game service used or modified. Launcher nice 15; child nice increment clamps to 19 (lower priority). Six credential-isolated workers; 1.8 GiB effective-memory admission.',
               'LLM search_calls are observed public API events. Pipeline search_calls are frozen reader-reported counts and may omit callback research; search_calls_basis marks this limitation. Zero API search events must not be read as zero pipeline retrieval.',
               'An initial shared-lock wrapper waited without issuing experiment calls; only that owned wrapper was terminated. Actual deterministic execution followed the preregistered no-game/no-lock path; the other session retaining server.lock was not touched.',
               'Cleanup is documented in credential_cleanup.json. Source/runtime artifacts and exact prompts are hashed in run_manifest.json.',
               'The native gateway checks canaries after 200 logged task attempts, including infrastructure-error attempts; with retries this is more frequent than every 200 successful task responses. Successful health checks and failed HTTP attempts are counted separately.',
               '', '## Commands', '```bash',
               'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. python -m scripts.run_a17_ext_agents --prepare',
               'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= nice -n 15 python -m scripts.run_a17_ext_agents --run',
               'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. OPENBLAS_NUM_THREADS=1 nice -n 15 python -m scripts.analyze_a17_ext_agents --allow-partial', '```', '']
    with (REPORT / 'oracle_length_summary.csv').open() as handle:
        length_rows = list(csv.DictReader(handle))
    with (REPORT / 'oracle_observed_call_summary.csv').open() as handle:
        observed_rows = list(csv.DictReader(handle))
    report += ['## Q3 task-length comparison', '',
               '| Scope | Feasible / total | Semantic actions min / median / max / mean | Observed interface calls median / max (feasible) |',
               '|---|---:|---|---|']
    observed_scopes = {'EXT_v2_web_12x3': 'EXT_v2_web', 'CDS_52_directed': 'CDS_K_directed', 'main_34x3': 'main'}
    for scope in ('EXT_v2_web_12x3', 'CDS_52_directed', 'CDS_unique_task_world', 'main_34x3'):
        length = next(r for r in length_rows if r['scope'] == scope and r['world'] == 'all' and r['metric'] == 'semantic_actions')
        actual = next((r for r in observed_rows if r['scope'] == observed_scopes.get(scope) and r['world'] == 'all'
                       and r['true_feasible'] == '1' and r['metric'] == 'interface_calls'), None)
        sem = ' / '.join(f"{float(length[k]):.2f}" for k in ('minimum', 'median', 'maximum', 'mean'))
        api = ' / '.join(f"{float(actual[k]):.2f}" for k in ('median', 'maximum')) if actual else 'NA (not deduplicated from directed logs)'
        report.append(f"| {scope} | {length['feasible']} / {length['instances']} | {sem} | {api} |")
    report += ['', 'Plan-only interface calls equal saved action entries; observed interface calls above include inventory/submit overhead. No oracle was run.', '']
    (REPORT / 'REPORT.md').write_text('\n'.join(report))
    print(json.dumps({'LLM_episodes': llm_count, 'comparison_episodes': len(data), 'actions_audited': len(actions),
                      'scoring_exceptions': len(exceptions), 'changed_inputs': len(changed), 'usage': costs}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lengths-only', action='store_true')
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    REPORT.mkdir(parents=True, exist_ok=True)
    summaries = oracle_lengths()
    if args.lengths_only:
        print(json.dumps([r for r in summaries if r['world'] == 'all' and r['metric'] == 'semantic_actions'], sort_keys=True))
    else:
        analyze(args.allow_partial)


if __name__ == '__main__':
    main()
