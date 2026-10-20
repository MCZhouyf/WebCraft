#!/usr/bin/env python3
"""Summarize frozen-parameter D1 evidence before the D2 arena change."""
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    trace_path = ROOT / 'reports/d1_reproduction_trace.jsonl'
    action_path = ROOT / 'reports/d1_reproduction_actions.jsonl'
    trace = read_jsonl(trace_path)
    passed = []
    for path in sorted((ROOT / 'logs/b2/dev/1.16.5').glob('*.jsonl')):
        if path.name.startswith('fea_009.'):
            continue
        task_id = path.name.split('.')[0]
        for event in read_jsonl(path):
            if event['method'] in {'mine', 'craft_grid', 'smelt'}:
                passed.append({'task_id': task_id, 'method': event['method'],
                               'wall_ms': event['cost']['wall_ms'],
                               'net_displacement': event['cost']['net_displacement']})
    non_smelt = [row['wall_ms'] for row in passed if row['method'] != 'smelt']
    all_steps = [row['wall_ms'] for row in passed]
    timeout = next(row for row in trace if row['event'] == 'navigation_timeout')
    resets = [row for row in trace if row['event'] == 'path_reset'
              and row.get('reason') == 'stuck']
    nav_reached = [row for row in trace if row['event'] == 'navigation_reached']
    output = {
        'frozen_parameter_reproduction': {
            'status': 'REPRODUCED', 'task_id': 'fea_009', 'world': '1.16.5',
            'timeout_seconds': 15,
            'action_log_sha256': sha256(action_path),
            'trace_log_sha256': sha256(trace_path),
            'successful_navigation_segments_ms': [row['elapsed_ms'] for row in nav_reached],
            'failed_target': timeout['target'], 'failed_position': timeout['position'],
            'latest_path': timeout['latestPath'],
            'stuck_reset_count': len(resets),
            'stuck_reset_elapsed_ms': [
                round((PathLikeTime(row['timestamp']) - PathLikeTime(trace[0]['timestamp'])), 3)
                for row in resets
            ],
            'surroundings_3x3x3': timeout['surroundings_3x3x3'],
            'fell_into_hole': False,
            'fully_enclosed': False,
            'evidence_note': ('The bot stood on a complete stone floor with air at feet/head. '
                              'Resource blocks bounded the one-cell corridor north and south; '
                              'pathfinder repeatedly returned a valid path then reset as stuck.'),
        },
        'passed_six_task_step_distribution': {
            'semantic_action_count': len(all_steps),
            'all_action_median_ms': statistics.median(all_steps),
            'all_action_max_ms': max(all_steps),
            'non_smelt_action_count': len(non_smelt),
            'non_smelt_action_median_ms': statistics.median(non_smelt),
            'non_smelt_action_max_ms': max(non_smelt),
            'note': ('Historical action logs do not separate navigation from interaction. '
                     'Non-smelt wall time is the conservative navigation-plus-interaction proxy; '
                     'smelt maxima include furnace processing.'),
        },
        'classification': {
            'A_arena_layout': True,
            'B_pathfinding_configuration': True,
            'C_timeout_too_short': False,
            'D_other': False,
            'primary': 'A',
            'rationale': ('The arena is graph-reachable but the one-cell corridors cause repeated '
                          '1.16.5 pathfinder stuck resets. The 15-second threshold is well above '
                          'the maximum 7.180-second non-smelt action among six passing tasks.'),
        },
    }
    (ROOT / 'reports/d1_diagnosis.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['classification']))


def PathLikeTime(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


if __name__ == '__main__':
    main()
