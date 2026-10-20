#!/usr/bin/env python3
"""Run preregistered A21 MDS development or formal ReAct episodes."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import importlib
import json
import multiprocessing
import os
import queue as queue_module
import resource
import shutil
import subprocess
import sys
import time
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter
from arbiter.checkpoint import effective_available_bytes
from arbiter.evaluation.cds_agents import CDSKnowledgeAdapter, public_rows, replace_search_results
from arbiter.evaluation.external_agents import public_system_prompt, run_external_episode
from arbiter.evaluation.external_controls import public_task
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace
from arbiter.llm.gateway import GatewayClient, GatewayError, RateLimited
from scripts.run_cds_agents_20261017 import AuditedClient, goal_count
from scripts.a21_author_glm_config import load_author_config, partition_credential_pools
from scripts.a21_saved_prefix import matches, select_prefix

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
BASE = ROOT / 'artifacts/post_review/a21_mds_20261018'
REPORT = ROOT / 'reports/main/post_review/a21_mds_20261018'
PREREG = ROOT / 'prereg/a21_mds_20261018.md'
MODEL = 'glm-5.2'
PROMPT_SHA256 = '5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84'
MIN_BYTES = int(1.8 * 1024**3)
STAGES = ('development', 'development_short', 'formal')
DEVELOPMENT_GATES = {'development': 'development_gate.json',
                     'development_short': 'development_short_gate.json'}
SHORTENED_MANIFEST_SHA256 = 'e13a61441097d3507d9e82fa54a9202180f0f1c5f276aacafb7fd195ce24ceb1'
FORMAL_MANIFEST_SHA256 = '0b31fb38861d1152c8059c8bba3dcd90ff566e42978e58ec6eefbce2038f5e78'
DEVELOPMENT_MANIFEST_SHA256 = 'a44428ad6586417b1efa64b6234e4798288bf21a3954af3eb96468562929607f'
GATE_AMENDMENT = ROOT / 'prereg/a21_formal_gate_correction_20261019.md'
GATE_AMENDMENT_SHA256 = '5c028b57de70dff74ee7857e9c8c2bb1fab5949422d618bfbb28ea0fc36c6fcc'


class SavedPrefixEnd(GatewayError):
    pass


def retry_after_seconds(error: BaseException) -> float:
    """Read Retry-After without persisting headers or provider error bodies."""
    current: BaseException | None = error
    for _ in range(5):
        if isinstance(current, urllib.error.HTTPError):
            value = current.headers.get('Retry-After') if current.headers else None
            if not value:
                return 0.0
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return max(0.0, parsed.timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    return 0.0
        if current is None:
            break
        current = current.__context__
    return 0.0


class PooledAuditedClient(AuditedClient):
    """A21-only audited client with a worker-local, disjoint fallback pool."""

    def __init__(self, ledger: Path, *, model: str) -> None:
        # OpenAIProbeClient deliberately enforces one credential for legacy
        # probe runs. A21's preregistered transport instead partitions six
        # author-authorized slots into disjoint worker-local pools.
        GatewayClient.__init__(self, ledger)
        if self.model != model:
            raise GatewayError('requested A21 model differs from registration')
        self.calls = self.input_tokens = self.output_tokens = 0
        self.task_calls = self.task_input_tokens = self.task_output_tokens = 0
        self.returned_models: set[str] = set()
        self.priced_cost: float | None = 0.0
        self.task_priced_cost: float | None = 0.0
        self.offline_only = False
        self.replayed = 0
        self.replay_pairs: list[Any] = []
        self.replay_pending: Any = None
        self.replay_source: Path | None = None
        self.request_clock: Any = None
        self.global_request_gap = 0.0
        self.cooldown_clock: Any = None
        self.wait_on_429 = False
        self.rate_limit_streak = 0
        self.worker_number = 0

    def _wait_for_http_slot(self) -> None:
        while True:
            if self._last_finished is not None:
                local_wait = self.min_interval_seconds - (time.monotonic()-self._last_finished)
                if local_wait > 0:
                    time.sleep(local_wait)
            cooldown = getattr(self, 'cooldown_clock', None)
            if self.request_clock is not None:
                with self.request_clock.get_lock():
                    target = max(time.monotonic(), self.request_clock.value,
                                 cooldown.value if cooldown is not None else 0.0)
                    self.request_clock.value = target + self.global_request_gap
            else:
                target = max(time.monotonic(), cooldown.value if cooldown is not None else 0.0)
            delay = target-time.monotonic()
            if delay > 0:
                time.sleep(delay)
            # Another worker can extend cooldown while this worker sleeps.
            if cooldown is None or time.monotonic() >= cooldown.value:
                return

    def _chat_once(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        while True:
            self._wait_for_http_slot()
            try:
                response = super()._chat_once(*args, **kwargs)
            except RateLimited as exc:
                if not getattr(self, 'wait_on_429', False):
                    raise
                self.rate_limit_streak += 1
                wait = max(min(300.0, 60.0 * 2**min(self.rate_limit_streak-1, 3)),
                           retry_after_seconds(exc))
                target = time.monotonic() + wait
                if self.cooldown_clock is not None:
                    with self.cooldown_clock.get_lock():
                        self.cooldown_clock.value = max(self.cooldown_clock.value, target)
                        target = self.cooldown_clock.value
                row = {'record_type': 'rate_limit_wait', 'status': 'WAITING_HTTP_429',
                       'worker': self.worker_number, 'model': MODEL,
                       'call_type': args[0] if args else kwargs.get('call_type'),
                       'consecutive_429': self.rate_limit_streak,
                       'minimum_wait_seconds': target-time.monotonic(),
                       'retry_not_before_utc': datetime.fromtimestamp(
                           time.time()+max(0.0, target-time.monotonic()), timezone.utc).isoformat(),
                       'same_request': True, 'timestamp': datetime.now(timezone.utc).isoformat()}
                self._append(row)
                write(self.ledger.parent / 'transport_status.json', row)
                if self.cooldown_clock is None:
                    time.sleep(wait)
                continue
            if not kwargs.get('canary', False):
                self.rate_limit_streak = 0
            if getattr(self, 'wait_on_429', False):
                write(self.ledger.parent / 'transport_status.json', {
                    'status': 'ACTIVE', 'worker': self.worker_number,
                    'model': response.get('model'), 'timestamp': datetime.now(timezone.utc).isoformat(),
                })
            return response

    def validate_canary(self, *, thinking: str | None = None) -> None:
        if not self.offline_only:
            super().validate_canary(thinking=thinking)

    def chat(self, call_type: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        if call_type == 'external_react' and self.replayed < len(self.replay_pairs):
            prompt, response = self.replay_pairs[self.replayed]
            if not matches(prompt, call_type, messages, kwargs):
                raise GatewayError('A21_saved_prompt_or_observation_diverged')
            if self.audit_path is not None:
                for row in (prompt, response):
                    append(self.audit_path, {**row, 'replayed_from_saved_prefix': True,
                                             'counted_as_new_generation': False})
            usage = response.get('usage') or {}
            inputs = int(usage.get('prompt_tokens', 0))
            outputs = int(usage.get('completion_tokens', 0))
            cached = int((usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0)
            cost = (max(0, inputs-cached)*self.price_in + cached*self.price_cached + outputs*self.price_out)/1_000_000
            self.replayed += 1
            self.calls += 1
            self.task_calls += 1
            self.task_input_tokens += inputs
            self.task_output_tokens += outputs
            self.returned_models.add(MODEL)
            if self.task_priced_cost is not None:
                self.task_priced_cost += cost
            return {**{key: response[key] for key in ('content', 'model', 'usage', 'finish_reason')},
                    'cost': cost}
        if call_type == 'external_react' and self.replay_pending is not None:
            if not matches(self.replay_pending, call_type, messages, kwargs):
                raise GatewayError('A21_unanswered_saved_request_diverged')
            self.replay_pending = None
        if self.offline_only:
            raise SavedPrefixEnd('offline_saved_prefix_end')
        return super().chat(call_type, messages, **kwargs)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=STAGES, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--private-config', type=Path)
    source.add_argument('--author-context', action='store_true')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4, 5, 6), default=2)
    parser.add_argument('--request-interval', type=float, default=2.0)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--formal-phase', choices=('K', 'controls'), default='K')
    parser.add_argument('--credential-slots', type=int, nargs='+')
    parser.add_argument('--start-stagger', type=float, default=0.0)
    parser.add_argument('--global-request-gap', type=float, default=0.0)
    parser.add_argument('--wait-on-429', action='store_true')
    return parser.parse_args()


def stage_output(stage: str) -> Path:
    return BASE / {'development': 'development_run', 'development_short': 'development_short_run',
                   'formal': 'formal_run'}[stage]


def material_manifest(stage: str) -> Path:
    return BASE / {'development': 'development_materials/manifest.json',
                   'development_short': 'development_short_materials/manifest.json',
                   'formal': 'materials/manifest.json'}[stage]


def shortened_development_eligible() -> bool:
    path = REPORT / DEVELOPMENT_GATES['development']
    if not path.is_file():
        return False
    value = json.loads(path.read_text())
    return (value.get('n') == 8 and not value.get('passed')
            and value['feasible_success_rate'] < value['lower'])


def eligible_development_gate() -> str | None:
    """Historical numeric dev gates; not an authorization for formal entry."""
    for stage, name in DEVELOPMENT_GATES.items():
        if stage == 'development_short' and not shortened_development_eligible():
            return None
        path = REPORT / name
        value = json.loads(path.read_text()) if path.is_file() else {}
        if value.get('passed'):
            if stage == 'development_short' and (
                    value.get('n') != 8 or value.get('feasible_n') != 8
                    or value.get('retry_number') != 1
                    or value.get('material_manifest_sha256') != SHORTENED_MANIFEST_SHA256
                    or sha(material_manifest(stage)) != SHORTENED_MANIFEST_SHA256):
                raise RuntimeError('short_development_gate_input_mismatch')
            return name
    return None


def material_rows(stage: str) -> list[dict[str, Any]]:
    manifest = material_manifest(stage)
    if stage == 'formal' and sha(manifest) != FORMAL_MANIFEST_SHA256:
        raise RuntimeError('formal_frozen_manifest_mismatch')
    value = json.loads(manifest.read_text())
    rows = list(value['instances'])
    if stage == 'development_short':
        if sha(manifest) != SHORTENED_MANIFEST_SHA256:
            raise RuntimeError('short_development_frozen_manifest_mismatch')
        source = material_manifest('development')
        original = json.loads(source.read_text())['instances']
        if (value.get('source_manifest_sha256') != sha(source) or value.get('retry_number') != 1
                or {row['source_instance_id'] for row in rows} != {row['instance_id'] for row in original}):
            raise RuntimeError('short_development_source_or_scope_mismatch')
    if stage in DEVELOPMENT_GATES:
        jobs = [{'condition': 'K', **row} for row in rows]
        expected = 8
    else:
        jobs = [{'condition': condition, **row} for condition in ('K', 'K+S', 'K+N', 'empty') for row in rows]
        expected = 128
    if len(jobs) != expected or len({(r['condition'], r['instance_id']) for r in jobs}) != expected:
        raise RuntimeError('registered A21 material scope changed')
    return jobs


def validate_config(path: Path, workers: int) -> dict[str, Any]:
    config = json.loads(path.read_text())
    keys = list(dict.fromkeys(config.get('api_keys', [])))
    if (config.get('model') != MODEL or not config.get('enabled') or not config.get('allow_api_calls')
            or not isinstance(config.get('base_url'), str) or len(keys) < workers
            or any(not isinstance(key, str) or not key for key in keys)):
        raise RuntimeError('A21 private GLM configuration is not enabled for requested worker count')
    return {'base_url': config['base_url'], 'api_keys': keys}


def clean_env() -> None:
    for key in ('WC_LLM_API_KEY', 'WC_LLM_API_KEYS', 'WC_LLM_BASE_URL', 'WC_LLM_MODEL',
                'ARB_LLM_BUDGET', 'ARB_LLM_RUN_CAP', 'WC_LLM_PRICE_IN_PER_MTOK',
                'WC_LLM_PRICE_CACHED_IN_PER_MTOK', 'WC_LLM_PRICE_OUT_PER_MTOK', 'WC_LLM_CURRENCY'):
        os.environ.pop(key, None)


def knowledge_rows(material: dict[str, Any], condition: str, stage: str) -> list[dict[str, str]]:
    rows = public_rows(material, condition)
    if stage == 'formal':
        # Stable pseudorandom order with a common salt across conditions keeps
        # shared facts in the same relative order and hides oracle path order.
        salt = f"20261018:{material['instance_id']}:".encode()
        rows.sort(key=lambda row: hashlib.sha256(
            salt + json.dumps(row, sort_keys=True).encode()).digest())
    return rows


def run_one(job: dict[str, Any], *, stage: str, worker: int, output: Path,
            client: PooledAuditedClient) -> dict[str, Any]:
    if sha(ROOT / job['visible_path']) != job['visible_sha256']:
        raise RuntimeError('A21_visible_material_hash_mismatch')
    if stage == 'formal' and sha(ROOT / job['evaluator_path']) != job['evaluator_sha256']:
        raise RuntimeError('A21_evaluator_material_hash_mismatch')
    material = json.loads((ROOT / job['visible_path']).read_text())
    evaluator = (json.loads((ROOT / job['evaluator_path']).read_text()) if stage == 'formal' else None)
    initial = Counter({row['name']: int(row['count']) for row in material['public_task']['standard_items']
                       + material['public_task'].get('task_supplements', [])})
    stem = f"react__{job['condition']}__{job['instance_id']}"
    worker_dir = output / f'worker_{worker}'
    actions = worker_dir / 'actions' / f'{stem}.jsonl'
    prompts = worker_dir / 'prompts' / f'{stem}.jsonl'
    client.task_calls = client.task_input_tokens = client.task_output_tokens = 0
    client.task_priced_cost = 0.0
    client.audit_path = prompts
    source_root = stage_output(stage)
    client.replay_source, client.replay_pairs, client.replay_pending = select_prefix(source_root, stem, prompts)
    client.replayed = 0
    if client.replay_source is not None and not client.offline_only:
        proofs = []
        for proof_path in REPORT.glob('saved_prefix_verification*.json'):
            proof = json.loads(proof_path.read_text())
            if proof.get('status') == 'OFFLINE_PREFIX_PASS':
                proofs.extend(proof['prefixes'])
        if not any(row['source_path'] == str(client.replay_source.relative_to(ROOT))
                   and row['source_sha256'] == sha(client.replay_source)
                   and row['saved_replies'] == len(client.replay_pairs) for row in proofs):
            raise GatewayError('A21_saved_prefix_requires_matching_offline_verification')
    handle = None
    try:
        sys.path.insert(0, str(WEB))
        sim = importlib.import_module('sim')
        handle = sim.create_simulated_agent(
            version=material['target_world'], log_path=actions, initial_inventory=initial,
            search_condition=f"matched_{material['target_world']}",
            search_index_dir=str(WEB / 'corpus/indexes/formal_p1' / f"matched_{material['target_world']}"),
        )
        api = CDSKnowledgeAdapter(PublicSemanticAdapter(handle['api']), knowledge_rows(material, job['condition'], stage))
        client.validate_canary(thinking='disabled')
        episode = run_external_episode(api, client, method='react', task=public_task(material['public_task']),
                                       max_calls=40, max_actions=90, thinking='disabled')
        if episode.model_output_error:
            api.submit({'feasible': None}, intent='commit')
        final_inventory = api.inventory(intent='commit')
        handle['evaluator'].finish_episode()
        replace_search_results(actions, api.observations)
        events = read_jsonl(actions)
        feasible = True if evaluator is None else bool(evaluator['true_feasible'])
        attained = goal_count(final_inventory, material['public_task']['target']) >= int(material['public_task']['target']['count'])
        success = int((feasible and attained and episode.reported_feasible is True)
                      or (not feasible and episode.reported_feasible is False))
        conflicts = load_conflicts(WEB / 'data/conflicts.jsonl')
        nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
        legacy = score_external_trace(events, world=material['target_world'], conflicts=conflicts,
                                      resource_nodes=nodes, true_feasible=feasible,
                                      reported_feasible=episode.reported_feasible)
        scoring_task = {**material['public_task'], 'feasible': {material['target_world']: feasible}}
        scored = outcome({'world': material['target_world'], 'true_feasible': feasible,
                          'reported_feasible': episode.reported_feasible, 'metrics': {'SR': success},
                          'model_calls': episode.model_calls, 'diagnostics': list(episode.diagnostics),
                          '_max_calls': 40, '_max_actions': 90}, events, scoring_task)
        return {'record_type': 'a21_mds_episode', 'stage': stage, 'method': 'react',
                'condition': job['condition'], 'instance_id': job['instance_id'],
                'task_id': material['public_task']['id'], 'source_task_id': material['source_task_id'],
                'target_item': material['public_task']['target']['item'],
                'family_id': material['family_id'], 'target_world': material['target_world'],
                'comparison_world': material.get('comparison_world', ''), 'seed': 20261018,
                'true_feasible': feasible, 'reported_feasible': episode.reported_feasible,
                'success': success, 'terminal_attained': attained, 'model_calls': episode.model_calls,
                'input_tokens': episode.input_tokens, 'output_tokens': episode.output_tokens,
                'tool_actions': episode.tool_actions, 'primary_cost': episode.primary_cost,
                'model_output_error': episode.model_output_error, 'metrics': legacy, 'gold_score': scored,
                'replayed_model_calls': client.replayed, 'new_model_calls': episode.model_calls-client.replayed,
                'replay_source': str(client.replay_source.relative_to(ROOT)) if client.replay_source else None,
                'replay_source_sha256': sha(client.replay_source) if client.replay_source else None,
                'action_log': str(actions.relative_to(ROOT)), 'prompt_log': str(prompts.relative_to(ROOT)),
                'visible_material_sha256': job['visible_sha256'],
                'evaluator_sha256': job.get('evaluator_sha256', ''),
                'server_model_identifiers': sorted(client.returned_models), 'estimated_provider_cost': client.task_priced_cost}
    finally:
        if handle is not None:
            handle['shutdown']()


def worker_main(stage: str, worker: int, jobs: Any, output: Path,
                request_interval: float, start_stagger: float = 0.0,
                request_clock: Any = None, global_request_gap: float = 0.0,
                cooldown_clock: Any = None, wait_on_429: bool = False) -> dict[str, Any]:
    completed = 0
    time.sleep((worker-1)*start_stagger)
    client = PooledAuditedClient(output / f'worker_{worker}' / 'ledger.jsonl', model=MODEL)
    client.min_interval_seconds = request_interval
    client.request_clock = request_clock
    client.global_request_gap = global_request_gap
    client.cooldown_clock = cooldown_clock
    client.wait_on_429 = wait_on_429
    client.worker_number = worker
    source = iter(jobs.get, None) if hasattr(jobs, 'get') else iter(jobs)
    for job in source:
        if effective_available_bytes() < MIN_BYTES:
            raise RuntimeError('host_memory_gate')
        row = run_one(job, stage=stage, worker=worker, output=output,
                      client=client)
        if row['server_model_identifiers'] != [MODEL]:
            raise RuntimeError('server_model_identifier_not_locked')
        append(output / f'worker_{worker}' / 'episodes.jsonl', row)
        completed += 1
    return {'worker': worker, 'completed': completed}


def completed_records(output: Path, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept only complete, unique, input-matched episode checkpoints."""
    expected = {(job['condition'], job['instance_id']): job for job in jobs}
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(output.glob('**/worker_*/episodes.jsonl')):
        for row in read_jsonl(path):
            key = (row['condition'], row['instance_id'])
            if key not in expected or key in records:
                raise RuntimeError('unexpected_or_duplicate_A21_checkpoint')
            if (row.get('visible_material_sha256') != expected[key]['visible_sha256']
                    or row.get('server_model_identifiers') != [MODEL]):
                raise RuntimeError('A21_checkpoint_input_or_model_mismatch')
            if not all((ROOT / row[name]).is_file() for name in ('action_log', 'prompt_log')):
                raise RuntimeError('A21_checkpoint_missing_audit_log')
            records[key] = row
    return [records[(job['condition'], job['instance_id'])] for job in jobs
            if (job['condition'], job['instance_id']) in records]


def success_gate(records: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    if len(records) != expected or any(row['condition'] != 'K' for row in records):
        raise RuntimeError('incomplete_K_gate_coverage')
    feasible = [row for row in records if row['true_feasible']]
    if not feasible:
        raise RuntimeError('K_gate_has_no_feasible_instances')
    rate = sum(row['success'] for row in feasible) / len(feasible)
    return {'n': len(records), 'feasible_n': len(feasible), 'feasible_success_rate': rate,
            'lower': 0.40, 'upper': 0.80, 'passed': 0.40 <= rate <= 0.80,
            'rule': 'registered inclusive interval'}


def forbidden_prompt_key(value: Any) -> bool:
    forbidden = {'truth_vector', 'true_feasible', 'oracle_actions', 'oracle_plan',
                 'comparison_world', 'target_world', 'candidate_versions'}
    if isinstance(value, dict):
        return bool(forbidden.intersection(value)) or any(forbidden_prompt_key(v) for v in value.values())
    if isinstance(value, list):
        return any(forbidden_prompt_key(v) for v in value)
    return False


def development_executability() -> dict[str, Any]:
    """Technical entry check under the approved correction, independent of SR."""
    if not GATE_AMENDMENT.is_file() or sha(GATE_AMENDMENT) != GATE_AMENDMENT_SHA256:
        raise RuntimeError('formal_gate_amendment_missing_or_changed')
    source = material_manifest('development')
    if sha(source) != DEVELOPMENT_MANIFEST_SHA256:
        raise RuntimeError('original_development_manifest_changed')
    if hashlib.sha256(public_system_prompt('react').encode()).hexdigest() != PROMPT_SHA256:
        raise RuntimeError('frozen_ReAct_prompt_changed')
    jobs = material_rows('development')
    records = completed_records(stage_output('development'), jobs)
    if len(records) != 8:
        raise RuntimeError('incomplete_development_executability_coverage')
    public_methods = {'inventory', 'search', 'mine', 'craft_grid', 'smelt',
                      'check_name', 'report_version', 'submit'}
    checks = []
    for job, row in zip(jobs, records):
        events = read_jsonl(ROOT / row['action_log'])
        audit = read_jsonl(ROOT / row['prompt_log'])
        prompts = [v for v in audit if v.get('record_type') == 'full_prompt'
                   and v.get('call_type') == 'external_react']
        replies = [v for v in audit if v.get('record_type') == 'full_response'
                   and v.get('call_type') == 'external_react']
        boundaries = (bool(events) and events[0].get('method') == 'inventory'
                      and events[-1].get('method') == 'inventory'
                      and any(v.get('method') == 'submit' for v in events)
                      and all(v.get('method') in public_methods for v in events))
        audit_ok = len(prompts) == len(replies) == row['model_calls']
        for prompt in prompts:
            messages = prompt['messages']
            users = [json.loads(v['content']) for v in messages if v['role'] == 'user']
            systems = [v['content'] for v in messages if v['role'] == 'system']
            audit_ok = (audit_ok and bool(users) and systems == [public_system_prompt('react')]
                        and not any(forbidden_prompt_key(v) for v in users))
        valid = (boundaries and audit_ok and all(v.get('model') == MODEL for v in replies)
                 and 1 <= row['model_calls'] <= 40 and 0 <= row['primary_cost'] <= 90
                 and row.get('model_output_error') is None
                 and sha(ROOT / job['visible_path']) == job['visible_sha256'])
        checks.append({'instance_id': row['instance_id'], 'passed': bool(valid),
                       'public_log_boundaries': bool(boundaries), 'request_audit_valid': bool(audit_ok),
                       'model_calls': row['model_calls'], 'primary_cost': row['primary_cost'],
                       'action_log_sha256': sha(ROOT / row['action_log']),
                       'prompt_log_sha256': sha(ROOT / row['prompt_log'])})
    return {'passed': all(v['passed'] for v in checks), 'n': len(checks),
            'rule': 'author-approved technical executability; no development SR threshold',
            'amendment_sha256': GATE_AMENDMENT_SHA256,
            'development_manifest_sha256': sha(source), 'prompt_sha256': PROMPT_SHA256,
            'checks': checks}


def process_worker(queue: Any, stage: str, worker: int, jobs: Any, output: Path,
                   request_interval: float, start_stagger: float = 0.0,
                   request_clock: Any = None, global_request_gap: float = 0.0,
                   cooldown_clock: Any = None, wait_on_429: bool = False) -> None:
    """Child wrapper that reports only a fixed exception class, never details."""
    try:
        queue.put({'ok': True, **worker_main(stage, worker, jobs, output, request_interval,
                                           start_stagger, request_clock, global_request_gap,
                                           cooldown_clock, wait_on_429)})
    except Exception as exc:
        write(output / f'worker_{worker}' / 'failure.json', {
            'status': 'stopped', 'error_type': type(exc).__name__,
        })
        queue.put({'ok': False, 'worker': worker, 'error_type': type(exc).__name__})
    finally:
        clean_env()


def main() -> int:
    args = parse_args()
    if not 0 <= args.request_interval <= 60:
        raise ValueError('invalid request interval')
    if not 0 <= args.start_stagger <= 20:
        raise ValueError('invalid worker start stagger')
    if not 0 <= args.global_request_gap <= 60:
        raise ValueError('invalid global request gap')
    if effective_available_bytes() < MIN_BYTES:
        raise RuntimeError('host_memory_gate')
    if not PREREG.is_file():
        raise RuntimeError('missing A21 preregistration')
    technical = None
    if args.stage == 'development_short':
        if not shortened_development_eligible():
            raise RuntimeError('short_development_retry_not_eligible')
        if not (ROOT / 'prereg/a21_shortened_development_20261019.md').is_file():
            raise RuntimeError('missing_short_development_preregistration')
    if args.stage == 'formal':
        technical = development_executability()
        if not technical['passed']:
            raise RuntimeError('formal_requires_development_executability')
        write(REPORT / 'development_executability.json', technical)
        if args.formal_phase == 'controls':
            gate = REPORT / 'formal_K_gate.json'
            if not gate.is_file() or not json.loads(gate.read_text()).get('passed'):
                raise RuntimeError('formal controls require passed formal K gate')
    if hashlib.sha256(public_system_prompt('react').encode()).hexdigest() != PROMPT_SHA256:
        raise RuntimeError('frozen ReAct prompt changed')
    all_jobs = material_rows(args.stage)
    jobs = ([job for job in all_jobs if job['condition'] == 'K']
            if args.stage == 'formal' and args.formal_phase == 'K' else all_jobs)
    output = stage_output(args.stage)
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise RuntimeError('A21 output exists; refusing silent rerun')
    saved = completed_records(output, jobs) if args.resume else []
    done = {(row['condition'], row['instance_id']) for row in saved}
    pending = [job for job in jobs if (job['condition'], job['instance_id']) not in done]
    config = (load_author_config() if args.author_context
              else validate_config(args.private_config, args.workers))
    if args.credential_slots is not None:
        slots = args.credential_slots
        if len(set(slots)) != len(slots) or any(slot < 1 or slot > len(config['api_keys']) for slot in slots):
            raise RuntimeError('invalid_A21_credential_slot_selection')
        config['api_keys'] = [config['api_keys'][slot-1] for slot in slots]
    if len(config['api_keys']) < args.workers:
        raise RuntimeError('A21 author configuration lacks requested worker credentials')
    lock = Path('/root/autodl-tmp/server.lock').open('r')
    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    output.mkdir(parents=True, exist_ok=True)
    attempt = output / 'attempts' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    attempt.mkdir(parents=True)
    started = time.monotonic()
    try:
        manifest = {'stage': args.stage, 'planned': len(jobs), 'workers': args.workers,
                                              'model': MODEL, 'temperature': 0.0, 'max_calls': 40,
                                              'max_actions': 90, 'trace_steps': 8, 'prereg_sha256': sha(PREREG),
                                              'prompt_sha256': PROMPT_SHA256, 'jobs': jobs,
                                              'started_at_utc': datetime.now(timezone.utc).isoformat(),
                                              'request_interval': args.request_interval,
                                              'formal_knowledge_order_seed': 20261018,
                                              'credential_slot_order': args.credential_slots,
                                              'start_stagger_seconds': args.start_stagger,
                                              'global_request_gap_seconds': args.global_request_gap,
                                              'wait_on_429': args.wait_on_429,
                                              'development_entry_policy': technical['rule'] if technical else 'development',
                                              'gate_amendment_sha256': GATE_AMENDMENT_SHA256 if technical else None,
                                              'development_executability_sha256': sha(REPORT / 'development_executability.json') if technical else None,
                                              'material_manifest_sha256': sha(material_manifest(args.stage)),
                                              'already_completed': len(saved), 'pending': len(pending)}
        write(attempt / 'run_manifest.json', manifest)
        if not (output / 'run_manifest.json').exists():
            write(output / 'run_manifest.json', manifest)
        pools = partition_credential_pools(config['api_keys'], args.workers)
        old_env = os.environ.copy()
        queue: multiprocessing.Queue[Any] = multiprocessing.Queue()
        jobs_queue: multiprocessing.Queue[Any] = multiprocessing.Queue()
        jobs_queue.cancel_join_thread()
        request_clock = multiprocessing.Value('d', 0.0)
        cooldown_clock = multiprocessing.Value('d', 0.0)
        processes: list[multiprocessing.Process] = []
        # Each forked worker inherits a disjoint fallback pool.  Its gateway
        # rotates only within that pool after a 429 and validates the new slot
        # before resuming.  No credential or endpoint is persisted.
        for index in range(args.workers):
            slot = index + 1
            clean_env()
            os.environ.update({'WC_LLM_API_KEYS': json.dumps(pools[index]), 'WC_LLM_BASE_URL': config['base_url'],
                               'WC_LLM_MODEL': MODEL, 'ARB_LLM_BUDGET': '1000000',
                               'WC_LLM_PRICE_IN_PER_MTOK': '8', 'WC_LLM_PRICE_CACHED_IN_PER_MTOK': '2',
                               'WC_LLM_PRICE_OUT_PER_MTOK': '28', 'WC_LLM_CURRENCY': 'CNY'})
            process = multiprocessing.Process(target=process_worker,
                                              args=(queue, args.stage, slot, jobs_queue, attempt,
                                                    args.request_interval, args.start_stagger,
                                                    request_clock, args.global_request_gap,
                                                    cooldown_clock, args.wait_on_429))
            process.start()
            processes.append(process)
        os.environ.clear(); os.environ.update(old_env)
        for job in pending:
            jobs_queue.put(job)
        for _ in processes:
            jobs_queue.put(None)
        results = []
        while len(results) < len(processes):
            try:
                result = queue.get(timeout=1)
                results.append(result)
                if not result['ok']:
                    # These objects are exactly the children created above;
                    # a model/canary error cannot spill into further jobs.
                    for process in processes:
                        if process.is_alive():
                            process.terminate()
                    break
            except queue_module.Empty:
                if all(not process.is_alive() for process in processes):
                    break
        for process in processes:
            process.join()
        records = completed_records(output, jobs)
        if (len(results) != len(processes) or any(process.exitcode != 0 for process in processes)
                or any(not row['ok'] for row in results)):
            write(attempt / 'status.json', {'status': 'STOPPED_INFRASTRUCTURE',
                  'completed': len(records), 'planned': len(jobs), 'workers': results,
                  'elapsed_seconds': time.monotonic() - started,
                  'peak_RSS_KiB': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss})
            return 2
        if len(records) != len(jobs):
            raise RuntimeError('incomplete A21 coverage')
        # Create a combined, de-duplicated file after all individual rows exist.
        (output / 'episodes.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in records))
        if args.stage in DEVELOPMENT_GATES:
            gate = success_gate(records, 8)
            if args.stage == 'development_short':
                gate.update({'stage': args.stage, 'retry_number': 1,
                             'material_manifest_sha256': sha(material_manifest(args.stage))})
            write(REPORT / DEVELOPMENT_GATES[args.stage], gate)
        elif args.formal_phase == 'K':
            write(REPORT / 'formal_K_gate.json', success_gate(records, 32))
        write(output / 'status.json', {'status': 'COMPLETED', 'completed': len(records), 'planned': len(jobs),
                                        'elapsed_seconds': time.monotonic() - started,
                                        'peak_RSS_KiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                        'worker_peak_RSS_KiB': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                                        'owned_credentials_persisted': False, 'GPU_used': False})
        write(attempt / 'status.json', {'status': 'COMPLETED', 'completed': len(records)})
        return 0
    finally:
        clean_env()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
