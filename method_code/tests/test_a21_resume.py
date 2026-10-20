import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from scripts import run_a21_mds as runner


def test_six_worker_cli_ceiling(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run_a21_mds.py', '--stage', 'development',
                                    '--author-context', '--workers', '6'])
    assert runner.parse_args().workers == 6
    monkeypatch.setattr(sys, 'argv', ['run_a21_mds.py', '--stage', 'development',
                                    '--author-context', '--workers', '7'])
    with pytest.raises(SystemExit):
        runner.parse_args()


def test_resume_keeps_complete_checkpoint_and_ignores_partial_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    folder = tmp_path / 'attempts/previous/worker_1'
    folder.mkdir(parents=True)
    (tmp_path / 'actions.jsonl').write_text('{}\n')
    (tmp_path / 'prompts.jsonl').write_text('{}\n')
    row = {'condition': 'K', 'instance_id': 'completed', 'visible_material_sha256': 'hash',
           'server_model_identifiers': ['glm-5.2'], 'action_log': 'actions.jsonl',
           'prompt_log': 'prompts.jsonl'}
    (folder / 'episodes.jsonl').write_text(json.dumps(row) + '\n')
    (folder / 'partial_action.jsonl').write_text('{}\n')
    jobs = [{'condition': 'K', 'instance_id': name, 'visible_sha256': 'hash'}
            for name in ('completed', 'partial')]
    assert runner.completed_records(tmp_path, jobs) == [row]
    (folder / 'episodes.jsonl').write_text((json.dumps(row) + '\n') * 2)
    with pytest.raises(RuntimeError, match='duplicate'):
        runner.completed_records(tmp_path, jobs)


def test_pool_state_survives_episode_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('WC_LLM_API_KEYS', json.dumps(['test-slot-1', 'test-slot-2']))
    monkeypatch.setenv('WC_LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('WC_LLM_MODEL', 'glm-5.2')
    monkeypatch.setenv('ARB_LLM_BUDGET', '100')
    client = runner.PooledAuditedClient(tmp_path / 'ledger.jsonl', model='glm-5.2')
    monkeypatch.setattr(runner, 'PooledAuditedClient', lambda *a, **kw: client)
    monkeypatch.setattr(runner, 'effective_available_bytes', lambda: 10**12)
    seen = []

    def episode(job, **kwargs):
        same = kwargs['client']
        seen.append((id(same), same._credential_index))
        same._credential_index = 1
        return {'server_model_identifiers': ['glm-5.2']}

    monkeypatch.setattr(runner, 'run_one', episode)
    runner.worker_main('development', 1, [{}, {}], tmp_path, 10)
    assert seen == [(id(client), 0), (id(client), 1)]


def test_K_gate_uses_feasible_denominator_and_requires_all_records():
    rows = [{'condition': 'K', 'true_feasible': i < 20, 'success': int(i < 8 or i >= 20)}
            for i in range(32)]
    gate = runner.success_gate(rows, 32)
    assert gate['feasible_success_rate'] == 0.40
    assert gate['passed']
    with pytest.raises(RuntimeError, match='coverage'):
        runner.success_gate(rows[:-1], 32)


def test_formal_fact_shuffle_preserves_facts_and_common_relative_order():
    facts = [{'mechanism fact': f'fact {i}', 'source': 'synthetic', 'timestamp': '2000'}
             for i in range(20)]
    extra = {'mechanism fact': 'extra', 'source': 'synthetic', 'timestamp': '2000'}
    material = {'instance_id': 'synthetic', 'conditions': {'K': facts, 'K+S': facts + [extra]}}
    a = runner.knowledge_rows(material, 'K', 'formal')
    b = runner.knowledge_rows(material, 'K+S', 'formal')
    assert a == [row for row in b if row != extra]
    assert sorted(row['mechanism fact'] for row in a) == sorted(row['mechanism fact'] for row in facts)
    assert a != facts
    assert runner.knowledge_rows(material, 'K', 'development') == facts


def test_worker_claims_shared_jobs_until_sentinel(tmp_path, monkeypatch):
    from queue import Queue

    monkeypatch.setattr(runner, 'PooledAuditedClient', lambda *a, **kw: type('Client', (), {})())
    monkeypatch.setattr(runner, 'effective_available_bytes', lambda: 10**12)
    seen = []

    def episode(job, **kwargs):
        seen.append(job['id'])
        return {'server_model_identifiers': ['glm-5.2']}

    monkeypatch.setattr(runner, 'run_one', episode)
    queue = Queue()
    for number in range(4):
        queue.put({'id': number})
    queue.put(None)
    result = runner.worker_main('development', 1, queue, tmp_path, 15)
    assert result['completed'] == 4
    assert seen == [0, 1, 2, 3]


def test_shared_pacing_covers_different_clients_and_call_types(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    now = [0.0]
    clock = SimpleNamespace(value=0.0, get_lock=lambda: nullcontext())
    starts = []
    monkeypatch.setattr(runner.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(runner.time, 'sleep', lambda delay: now.__setitem__(0, now[0]+delay))

    def request(client, *args, **kwargs):
        starts.append(now[0])
        return {}

    monkeypatch.setattr(runner.GatewayClient, '_chat_once', request)
    for name in ('canary', 'external_react', 'canary'):
        client = object.__new__(runner.PooledAuditedClient)
        client.request_clock = clock
        client.global_request_gap = 20
        client._last_finished = None
        client.min_interval_seconds = 0
        client._chat_once(name, [])
    assert starts == [0.0, 20.0, 40.0]
