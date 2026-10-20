"""Offline oracle-plan runner for the deterministic semantic backend."""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from sim.semantic_agent import ROOT, create_simulated_agent


def load_tasks() -> dict[str, dict[str, Any]]:
    return {row['id']: row for row in (json.loads(line) for line in (ROOT / 'data/tasks.jsonl').read_text().splitlines() if line)}


def node_id(arena: dict[str, Any], action: dict[str, Any]) -> str:
    nodes = [row for row in arena['resources'] if row['block'] == action['node_type']]
    return nodes[action['node_ordinal'] - 1]['nodeId']


def run_oracle(task: dict[str, Any], world: str, *, plan_version: str | None = None,
               log_path: str | Path | None = None) -> dict[str, Any]:
    plan_version = plan_version or world
    plan = task['oracle_plans'][plan_version]
    arena = json.loads((ROOT / 'harness/config/arena.json').read_text())
    handle = create_simulated_agent(version=world, log_path=log_path)
    api = handle['api']
    started = perf_counter()
    actions, failure = [], None
    try:
        initial = api.inventory()
        if plan:
            for index, action in enumerate(plan['actions']):
                if action['method'] == 'mine': response = api.mine(node_id(arena, action))
                elif action['method'] == 'craft_grid': response = api.craft_grid(action['layout'])
                elif action['method'] == 'smelt': response = api.smelt(action['input'], action['fuel'], action['n'])
                else: raise ValueError(f"unsupported plan action {action['method']}")
                actions.append({'actionIndex': index, 'method': action['method'], 'response': response})
                if response.get('success') is False:
                    failure = {'actionIndex': index, 'method': action['method'],
                               'error': response.get('error', 'action_failed')}
                    break
        final = api.inventory()
        goal_count = sum(row['count'] for row in final if row['name'] == task['target']['item'])
        reported_feasible = plan is not None
        expected_feasible = task['feasible'][world]
        produced_goal = goal_count >= task['target']['count']
        success = produced_goal if expected_feasible else not reported_feasible
        api.submit({'feasible': reported_feasible, 'success': success})
        return {
            'task_id': task['id'], 'task_type': task['type'], 'split': task['split'],
            'world': world, 'plan_version': plan_version, 'expected_feasible': expected_feasible,
            'reported_feasible': reported_feasible, 'produced_goal': produced_goal,
            'success': success, 'execution_failure': failure,
            'planned_action_records': len(plan['actions']) if plan else 0,
            'completed_action_records': len(actions),
            'primary_cost': sum(row['response']['cost']['primary'] for row in actions),
            'oracle_cost_current_world': task['oracle_cost'][world],
            'elapsed_seconds': round(perf_counter() - started, 6),
            'initial_inventory': initial, 'final_inventory': final,
        }
    finally:
        handle['shutdown']()
