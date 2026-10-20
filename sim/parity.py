"""Replay immutable live schema-v2 traces through the deterministic simulator."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sim.semantic_agent import create_simulated_agent
from sim.runner import load_tasks, run_oracle


ROOT = Path(__file__).resolve().parents[1]
RANDOM_MINIMUM_ITEMS = {'redstone', 'lapis_lazuli'}
REPORTS = (
    ROOT / 'reports/b2_dev_live.json',
    ROOT / 'reports/archive/pre_v3_20261006/b2_dev_live.json',
    ROOT / 'reports/archive/pre_teleport_amendment_20261006/b2_dev_live.json',
    ROOT / 'reports/archive/pre_teleport_amendment_20261006/b2_eval_live.json',
    ROOT / 'reports/archive/pre_teleport_amendment_20261006/b4_live_stale_validation.json',
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log_index() -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = defaultdict(list)
    for path in ROOT.glob('logs/**/*.jsonl'):
        index[sha256(path)].append(path)
    return index


def _candidate(path: str, digest: str | None, hashes: dict[str, list[Path]]) -> Path | None:
    if digest and hashes.get(digest):
        return sorted(hashes[digest])[0]
    candidate = ROOT / path
    return candidate if candidate.exists() else None


def _collect_b2(report_path: Path, hashes: dict[str, list[Path]]) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text())
    rows = []
    for episode in report.get('episodes', []):
        for attempt in episode.get('attempts', []):
            record = attempt.get('record') or {}
            path = _candidate(attempt.get('log_path', ''), attempt.get('log_sha256'), hashes)
            if path is None:
                continue
            rows.append({
                'task_id': episode['task_id'], 'world': episode['world'],
                'attempt': attempt['attempt'], 'role': 'oracle',
                'valid': bool(attempt.get('valid')), 'online_success': bool(record.get('success')),
                'path': path, 'sha256': sha256(path), 'source_report': str(report_path.relative_to(ROOT)),
            })
    return rows


def _collect_b4(report_path: Path, hashes: dict[str, list[Path]]) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text())
    rows = []
    for episode in report.get('episodes', []):
        for role in ('oracle', 'stale'):
            branch = episode.get(role)
            if not branch:
                continue
            for attempt in branch.get('attempts', []):
                online = attempt.get('online') or {}
                path = _candidate(attempt.get('log_path', ''), attempt.get('log_sha256'), hashes)
                if path is None:
                    continue
                rows.append({
                    'task_id': episode['task_id'], 'world': report.get('world', '1.21.11'),
                    'attempt': attempt['attempt'], 'role': role,
                    'valid': not branch.get('infrastructure_failure', False),
                    'online_success': bool(online.get('success')), 'path': path,
                    'sha256': sha256(path), 'source_report': str(report_path.relative_to(ROOT)),
                })
    return rows


def collect_traces() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hashes = log_index()
    successful, failed = [], []
    for report_path in REPORTS:
        rows = _collect_b4(report_path, hashes) if 'b4_' in report_path.name else _collect_b2(report_path, hashes)
        for row in rows:
            (successful if row['valid'] and row['online_success'] else failed).append(row)
    probe = ROOT / 'logs/v3_probe/eff_007_12111.jsonl'
    if probe.exists():
        successful.append({
            'task_id': 'eff_007', 'world': '1.21.11', 'attempt': 1, 'role': 'oracle',
            'valid': True, 'online_success': True, 'path': probe, 'sha256': sha256(probe),
            'source_report': 'reports/amendment_v3_result.json',
        })
    return successful, failed


def deduplicate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Deduplicate by required episode tuple, preserving separate B4 policy roles."""
    selected: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row['task_id'], row['world'], row['attempt'], row['role'])
        # Current V3 report wins over V2, which wins over D4; B4 roles are already distinct.
        priority = 3 if row['source_report'] == 'reports/b2_dev_live.json' else (2 if 'pre_v3' in row['source_report'] else 1)
        prior = selected.get(key)
        if prior is None or priority > prior['_priority']:
            selected[key] = {**row, '_priority': priority}
    return [dict(row, _priority=None) for _, row in sorted(selected.items())], len(rows) - len(selected)


def load_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _equivalent(live: dict[str, Any], simulated: dict[str, Any]) -> list[str]:
    differences = []
    if live.get('success') != simulated.get('success'):
        differences.append('success')
    if live.get('cost', {}).get('primary') != simulated.get('cost', {}).get('primary'):
        differences.append('primary_cost')
    def normalized_items(rows):
        return Counter({row['name']: row['count'] for row in rows})
    def d1_equal(live_items, sim_items):
        live_counts, sim_counts = normalized_items(live_items), normalized_items(sim_items)
        for name in set(live_counts) | set(sim_counts):
            if name in RANDOM_MINIMUM_ITEMS:
                if sim_counts[name] > live_counts[name]:
                    return False
            elif live_counts[name] != sim_counts[name]:
                return False
        return True
    def normalized_flow(flow):
        return {key: normalized_items(flow.get(key, [])) for key in ('consumed', 'produced')}
    live_flow, sim_flow = normalized_flow(live.get('item_flow', {})), normalized_flow(simulated.get('item_flow', {}))
    if live_flow['consumed'] != sim_flow['consumed'] or not d1_equal(
            live.get('item_flow', {}).get('produced', []), simulated.get('item_flow', {}).get('produced', [])):
        differences.append('item_flow')
    live_result, sim_result = live.get('result'), simulated.get('result')
    if isinstance(live_result, dict) and isinstance(sim_result, dict):
        for key in ('success', 'error', 'items', 'product', 'resolvable', 'confirmed'):
            if key == 'items' and isinstance(live_result.get(key), list) and isinstance(sim_result.get(key), list):
                if not d1_equal(live_result[key], sim_result[key]):
                    differences.append(f'result.{key}')
            elif live_result.get(key) != sim_result.get(key):
                differences.append(f'result.{key}')
    elif isinstance(live_result, list) and isinstance(sim_result, list):
        if not d1_equal(live_result, sim_result):
            differences.append('result')
    elif live_result != sim_result:
        differences.append('result')
    return differences


def _invoke(api, event: dict[str, Any]):
    params = event.get('params', {})
    method = event['method']
    if method == 'inventory': return api.inventory()
    if method == 'mine': return api.mine(params['nodeId'])
    if method == 'craft_grid': return api.craft_grid(params['layout'])
    if method == 'smelt': return api.smelt(params['input'], params['fuel'], params['n'])
    if method == 'check_name': return api.check_name(params['name'])
    if method == 'report_version': return api.report_version(params.get('guess_sha256', ''))
    if method == 'submit': return api.submit(params.get('result'))
    if method == 'search': return api.search(params.get('q', ''), params.get('k', 0))
    raise ValueError(f'unsupported live method {method}')


def replay_trace(trace: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    events = load_events(trace['path'])
    if not events or events[0].get('method') != 'inventory':
        return {'match': False, 'mismatches': [{'category': 'invalid_live_log', 'detail': 'missing initial inventory'}]}
    initial = Counter({item['name']: item['count'] for item in events[0]['result']})
    sim_log = output_dir / f"{trace['task_id']}_{trace['world']}_{trace['attempt']}_{trace['role']}.jsonl"
    handle = create_simulated_agent(version=trace['world'], log_path=sim_log, initial_inventory=initial)
    mismatches = []
    try:
        for index, live in enumerate(events):
            _invoke(handle['api'], live)
            simulated = load_events(sim_log)[-1]
            for category in _equivalent(live, simulated):
                mismatches.append({'action_index': index, 'method': live['method'], 'category': category})
        live_final = events[-1]
        sim_final = load_events(sim_log)[-1]
        return {'match': not mismatches, 'mismatches': mismatches,
                'actions': len(events), 'live_final_method': live_final['method'],
                'simulated_log_sha256': sha256(sim_log)}
    finally:
        handle['shutdown']()


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    successful, failed = collect_traces()
    traces, duplicates = deduplicate(successful)
    outcomes = []
    for trace in traces:
        outcome = replay_trace(trace, output / 'replayed')
        outcomes.append({**{key: value for key, value in trace.items() if key != 'path'}, **outcome})
    mismatch_categories = Counter(category for row in outcomes for mismatch in row['mismatches']
                                  for category in [mismatch['category']])
    max_stacks = 0
    for trace in traces:
        for event in load_events(trace['path']):
            if event.get('method') == 'inventory' and isinstance(event.get('result'), list):
                max_stacks = max(max_stacks, len(event['result']))
    report = {
        'schema_version': 1, 'status': 'PASS' if not mismatch_categories else 'MISMATCHES_FOUND',
        'successful_trace_candidates': len(successful), 'successful_trace_duplicates_removed': duplicates,
        'successful_traces_replayed': len(traces), 'actions_replayed': sum(row.get('actions', 0) for row in outcomes),
        'mismatch_categories': dict(sorted(mismatch_categories.items())),
        'd1_random_drop_items': ['redstone', 'lapis_lazuli'],
        'd2_inventory_slot_check': {'max_observed_stacks': max_stacks,
                                    'limit': 36, 'passed': max_stacks <= 36},
        'traces': outcomes,
        'failed_live_trace_candidates': len(failed),
    }
    (ROOT / 'reports/sim_parity.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def run_failure_replay(output: Path) -> dict[str, Any]:
    """Execute frozen current-world plans for every distinct failed live oracle pair."""
    _, failed = collect_traces()
    tasks = load_tasks()
    rows = {}
    for trace in failed:
        if trace['role'] != 'oracle' or trace['task_id'] not in tasks:
            continue
        rows.setdefault((trace['task_id'], trace['world']), trace)
    outcomes = []
    for (task_id, world), trace in sorted(rows.items()):
        log_path = output / f'{task_id}_{world}.jsonl'
        result = run_oracle(tasks[task_id], world, log_path=log_path)
        outcomes.append({
            'task_id': task_id, 'world': world, 'live_log_sha256': trace['sha256'],
            'live_source_report': trace['source_report'], 'simulated': result,
            'simulated_log_sha256': sha256(log_path),
        })
    report = {
        'schema_version': 1,
        'status': 'PASS' if all(row['simulated']['success'] for row in outcomes) else 'ORACLE_OR_TRUTH_ISSUE',
        'failed_live_oracle_pairs_replayed': len(outcomes),
        'simulator_successes': sum(row['simulated']['success'] for row in outcomes),
        'outcomes': outcomes,
    }
    (ROOT / 'reports/sim_failure_replay.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default=str(ROOT / 'logs/sim_parity'))
    parser.add_argument('--failures', action='store_true')
    args = parser.parse_args()
    report = run_failure_replay(Path(args.output_dir) / 'failure_replay') if args.failures else run(Path(args.output_dir))
    print(json.dumps({key: report[key] for key in ('status', 'successful_traces_replayed',
                                                     'actions_replayed', 'mismatch_categories',
                                                     'failed_live_oracle_pairs_replayed', 'simulator_successes') if key in report}))
