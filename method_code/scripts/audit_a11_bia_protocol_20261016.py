"""A11 stage-0 fail-fast audit of retained prompts; no gateway/simulator imports."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path('/root/autodl-tmp/webcraft')
INPUT = ROOT / 'artifacts/post_review/cds_agents_20261017'
OUTPUT = ROOT / 'reports/main/post_review/a11_bia_protocol_audit'
SEED = 20261016


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def injected_facts(value: Any) -> list[dict[str, Any]]:
    """Find facts in decoded message payloads, not candidate/material metadata."""
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'diagnostic_mechanism_facts' and isinstance(child, list):
                found.extend(child)
            else:
                found.extend(injected_facts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(injected_facts(child))
    return found


def message_facts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for message in messages:
        try:
            value = json.loads(message['content'])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        found.extend(injected_facts(value))
    return found


def fact_check(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = Counter(canonical(row) for row in expected)
    retained = Counter(canonical(row) for row in actual)
    missing = wanted - retained
    return {
        'expected_entries': len(expected), 'actual_injected_entries': len(actual),
        'matched_entries': sum((wanted & retained).values()),
        'missing_entries': sum(missing.values()),
        'missing_facts': [json.loads(fact) for fact, count in missing.items() for _ in range(count)],
        'passed': not missing and (bool(expected) or not actual),
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def main() -> int:
    prereg = ROOT / 'prereg/a11_bia_protocol_audit_20261016.md'
    if not prereg.is_file():
        raise RuntimeError('A11 preregistration missing')
    episodes = []
    paths = sorted(INPUT.glob('**/episodes.jsonl'))
    for path in paths:
        episodes.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    keys = [(row['method'], row['condition'], row['instance_id']) for row in episodes]
    expected_cells = {('direct_llm', c) for c in ('K', 'K+S', 'empty')} | {
        ('react', c) for c in ('K', 'K+S', 'S', 'empty', 'K+N')}
    cells = Counter((row['method'], row['condition']) for row in episodes)
    if len(episodes) != 416 or len(set(keys)) != 416 or set(cells) != expected_cells or set(cells.values()) != {52}:
        raise RuntimeError('frozen A8 episode coverage changed')
    grouped = defaultdict(list)
    for row in sorted(episodes, key=lambda r: (r['method'], r['condition'], r['instance_id'])):
        grouped[row['condition']].append(row)
    rng = np.random.default_rng(SEED)
    selected = []
    for condition, rows in sorted(grouped.items()):
        for index in sorted(rng.choice(len(rows), size=3, replace=False).tolist()):
            selected.append(rows[index])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    samples = [{k: row[k] for k in ('method', 'condition', 'instance_id', 'prompt_log', 'visible_material_path')}
               for row in selected]
    write_json(OUTPUT / 'stage0_sample_manifest.json', {
        'seed': SEED, 'prereg_sha256': digest(prereg), 'samples': samples,
        'episode_source_sha256': {str(path.relative_to(ROOT)): digest(path) for path in paths},
    })
    checks = []
    stopped = None
    completed_episodes = 0
    for episode in selected:
        prompt_path = ROOT / episode['prompt_log']
        material_path = WEBCRAFT / episode['visible_material_path']
        material = json.loads(material_path.read_text())
        expected = material['conditions'][episode['condition']]
        log = [json.loads(line) for line in prompt_path.read_text().splitlines() if line.strip()]
        step = 0
        previous_request = None
        for record_index, record in enumerate(log):
            if record.get('record_type') != 'full_prompt' or record.get('call_type') == 'cds_boundary':
                continue
            step += 1
            check = {
                'method': episode['method'], 'condition': episode['condition'],
                'instance_id': episode['instance_id'], 'action_request_step': step,
                'prompt_record_index': record_index, 'call_type': record.get('call_type'),
                'prompt_log': episode['prompt_log'], 'prompt_log_sha256': digest(prompt_path),
                'messages_sha256': hashlib.sha256(canonical(record['messages']).encode()).hexdigest(),
                'material_path': episode['visible_material_path'], 'material_sha256': digest(material_path),
                **fact_check(expected, message_facts(record['messages'])),
            }
            checks.append(check)
            if not check['passed']:
                stopped = check
                write_json(OUTPUT / 'stage0_missing_knowledge_evidence.json', {
                    'episode': {k: episode[k] for k in ('method', 'condition', 'instance_id')},
                    'expected_injected_knowledge': expected,
                    'last_action_request': previous_request,
                    'failing_action_request': record,
                    'missing_knowledge': check['missing_facts'],
                })
                break
            previous_request = record
        if stopped:
            break
        if step == 0:
            raise RuntimeError('missing action prompts')
        completed_episodes += 1
    status = {
        'stage0': 'STOP_ACTION_KNOWLEDGE_MISSING' if stopped else 'PASS',
        'planned_sample_episodes': len(selected), 'completed_sample_episodes': completed_episodes,
        'checked_action_requests': len(checks), 'first_failure': stopped,
        'model_calls': 0, 'new_environment_episodes': 0,
        'stages_1_to_5_executed': False,
    }
    write_json(OUTPUT / 'stage0_prompt_checks.json', checks)
    write_json(OUTPUT / 'status.json', status)
    report = ['# A11 BIA protocol audit', '', f"Stage 0: **{status['stage0']}**.", '',
              f'Sample seed: {SEED}; 15 planned samples (three per condition).',
              f'Checked action requests: {len(checks)}; completed sampled episodes before stopping: {completed_episodes}.',
              'Model calls: 0. New environment episodes: 0.', '']
    if stopped:
        report.extend([
            f"First failing episode: `{stopped['method']} / {stopped['condition']} / {stopped['instance_id']}`.",
            f"Action request {stopped['action_request_step']}: required {stopped['expected_entries']} injected facts; "
            f"retained {stopped['actual_injected_entries']}; missing {stopped['missing_entries']}.", '',
            'The preceding action request and the failing request are retained verbatim in',
            '`stage0_missing_knowledge_evidence.json`; complete source hashes and per-fact missing',
            'content are recorded in the audit outputs.', '',
            'Code-level mechanism: CDSKnowledgeAdapter.inventory injects the full knowledge list only',
            'on the initial observation. run_external_episode replaces that observation after an action',
            'and constructs later stateless requests from the latest observation and a bounded action',
            'trace (two steps for Direct, eight for ReAct); the initial observation is not separately',
            'retained. Therefore the initial knowledge is not guaranteed to remain in later requests.', '',
            'Qualification: the failing request still retains the preceding craft action, its successful',
            'feedback and the model thought paraphrasing the recipe. The missing object is the original',
            'injected public fact with its source/timestamp, not necessarily every semantic trace of the',
            'recipe. This finding does not establish that this sampled episode failed or that a model',
            'never had the knowledge. It violates the registered per-request retention check.', '',
            'Per the mandatory stage-0 stop rule, stages 1-5 were not executed. No determination of',
            'the boundary-only root causes a-e or new context-match BIA is made. No corrected',
            'question was sent. The observed action-context defect also limits interpretation of',
            'the existing CDS behavior results; its quantitative extent is not established by this',
            'fail-fast sample, and previous reported numbers are unchanged.',
        ])
    (OUTPUT / 'REPORT.md').write_text('\n'.join(report) + '\n')
    print(json.dumps({k: v for k, v in status.items() if k != 'first_failure'}, sort_keys=True))
    return 2 if stopped else 0


if __name__ == '__main__':
    raise SystemExit(main())
