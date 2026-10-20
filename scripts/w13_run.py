#!/usr/bin/env python3
"""Evaluation-side orchestration; hidden fields never enter the VB policy process."""
from __future__ import annotations
import argparse
import copy
import csv
import gzip
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARB = ROOT.parent / 'arbiter'
OUT = ROOT / 'reports/paperA/w13_vbrag'
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ARB / 'src'), str(ARB / 'scripts')]
import numpy as np
from sim.semantic_agent import create_simulated_agent
from arbiter.adapters.webcraft import PublicSemanticAdapter
from arbiter.evaluation import p6_execution as p6
from arbiter.evaluation.external_scoring import load_resource_nodes
from arbiter.pinning import build_pin, verify_webcraft_pin
import arbiter.adapters.claims as ca
import arbiter.adapters.search as sa
import arbiter.adapters.webcraft as wa
import run_w8_closeout_20261016 as w8
from w13_runtime import canonical

SEED = 20261017
MODES = ('all_public', 'llm_only', 'oracle_reading')
METRICS = ('SR', 'feasible_SR', 'overconservative', 'execution_failure', 'plan_acceptance',
           'specific_query', 'specific_action', 'specific_contact', 'v3d_propagation',
           'semantic_actions', 'semantic_cost', 'search_calls')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def jsonl(path):
    with (gzip.open(path, 'rt') if str(path).endswith('.gz') else path.open()) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def csvout(name, rows):
    rows = list(rows)
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r)) or ['status']
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fields, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def resource_gate(phase):
    available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                         if l.startswith('MemAvailable:'))) * 1024
    limit = Path('/sys/fs/cgroup/memory.max')
    if limit.exists() and limit.read_text().strip() != 'max':
        available = min(available, int(limit.read_text()) - int(limit.with_name('memory.current').read_text()))
    if available < 10 * 1024**3 or os.environ.get('CUDA_VISIBLE_DEVICES') != '':
        raise RuntimeError('SAFETY_STOP: insufficient memory headroom or CUDA not disabled')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'resources_{phase}.json').write_text(json.dumps(dict(
        available_bytes=available, planned_bytes=2*1024**3, headroom_bytes=8*1024**3, GPU_used=False), indent=2)+'\n')


def setup(phase):
    resource_gate(phase)
    def deny(*args, **kwargs):
        raise RuntimeError('SAFETY_STOP: W13 denies network')
    socket.create_connection = deny
    socket.socket.connect = deny
    class NoLLM:
        def __init__(self, *a, **k):
            raise RuntimeError('SAFETY_STOP: W13 denies LLM construction')
    p6.GatewayClient = NoLLM
    pin = OUT / f'pin_{phase}.json'
    pin.write_text(json.dumps(build_pin(ROOT), indent=2, sort_keys=True)+'\n')
    state = verify_webcraft_pin(pin, ROOT, require_ready=True)
    for module in (p6, ca, sa, wa):
        module.verify_webcraft_pin = lambda *a, **k: state
    return pin


def public_bundle(bundle):
    keys = ('claim_id', 'chunk_id', 'claim_type', 'subject_item', 'payload', 'polarity',
            'tense', 'span_text', 'extractor_version', 'parent_claim_id', 'normalizer_version')
    hits = [{k: hit[k] for k in ('text', 'source_site', 'page_title', 'revid', 'timestamp', 'chunk_id')}
            for hit in bundle['search_rows']]
    return {'search_rows': hits,
            'claims': [{k: c[k] for k in keys if k in c} for c in bundle['claims']],
            'mineable_nodes': list(bundle.get('mineable_nodes', []))}


class Worker:
    def __init__(self, phase, pin):
        self.pin = pin
        self.backends, self.caches = {}, {}
        self.tmp = OUT / 'solver_tmp'
        self.tmp.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in
               ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'ENDPOINT', 'BASE_URL'))}
        env.update(TMPDIR=str(self.tmp), CUDA_VISIBLE_DEVICES='', PYTHONHASHSEED='0',
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
        self.stderr = (OUT / f'worker_{phase}.stderr').open('w')
        self.proc = subprocess.Popen([sys.executable, '-B', str(ROOT/'scripts/w13_runtime.py')],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.stderr, text=True, env=env, bufsize=1)

    def send(self, value):
        self.proc.stdin.write(canonical(value)+'\n')
        self.proc.stdin.flush()

    def research(self, subject, goal, mode, condition, bundle):
        if mode not in ('all_public', 'llm_only') or condition.startswith('cds_'):
            return {'search_rows': [], 'claims': []}, False
        key = (mode, condition)
        if key not in self.backends:
            self.backends[key] = p6.load_search_backend(condition, str(ROOT/'corpus/indexes/formal_p1'/condition),
                                                       root=ROOT, pin_path=self.pin)
            self.caches[key] = p6.load_claims_cache(str(ROOT/'corpus/indexes/formal_p1'/condition),
                                                   root=ROOT, pin_path=self.pin, reading_mode=mode)
        hits = self.backends[key].search(f'{subject} {goal}', 10)
        claims = [c for h in hits for c in self.caches[key].get(str(h['chunk_id']))]
        mini = public_bundle(dict(search_rows=hits, claims=claims))
        return {'search_rows': mini['search_rows'], 'claims': mini['claims']}, True

    def run(self, task, bundle, world, mode, condition, phase, episode_id):
        initial = Counter({r['name']: int(r['count']) for r in
                           task['standard_items'] + task.get('task_supplements', [])})
        log_path = OUT / phase / 'actions' / f'{episode_id}.jsonl'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            raise RuntimeError('refusing to overwrite an episode action log')
        handle = create_simulated_agent(version=world, log_path=log_path, initial_inventory=initial,
                                         search_condition=condition if not condition.startswith('cds_') else 'matched',
                                         search_index_dir='')
        api = PublicSemanticAdapter(handle['api'])
        request = {'goal': dict(task['target']),
                   'kit': [{'name': k, 'count': v} for k, v in sorted(initial.items())],
                   'bundle': public_bundle(bundle)}
        research_calls = []
        try:
            self.send(request)
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError('isolated worker exited; see sanitized worker stderr')
                message = json.loads(line)
                if message['type'] == 'error':
                    raise RuntimeError('worker ' + message['error_type'] + ': ' + message['message'])
                if message['type'] == 'done':
                    result = message['result']
                    break
                method, params, intent = message['method'], message['params'], message['intent']
                if method == 'research':
                    reply, executed = self.research(params['subject'], params['goal'], mode, condition, bundle)
                    research_calls.append(dict(query=params['subject']+' '+params['goal'],
                                               executed=executed, returned_chunks=len(reply['search_rows'])))
                elif method == 'inventory':
                    reply = api.inventory(intent=intent)
                elif method == 'mine':
                    reply = api.mine(params['node_type'], intent=intent)
                elif method == 'craft_grid':
                    reply = api.craft_grid(params['layout'], intent=intent)
                elif method == 'smelt':
                    reply = api.smelt(params['input'], params['fuel'], params['n'], intent=intent)
                elif method == 'check_name':
                    reply = api.check_name(params['identifier'], intent=intent)
                elif method == 'submit':
                    reply = api.submit(params['result'], intent=intent)
                else:
                    raise RuntimeError('SAFETY_STOP: unauthorized runtime RPC')
                self.send({'result': reply})
            final_inventory = api.inventory()
            handle['evaluator'].finish_episode()
        finally:
            handle['shutdown']()
        events = list(jsonl(log_path))
        feasible = bool(task['feasible'][world])
        count = sum(r['count'] for r in final_inventory if r['name'] == task['target']['item'])
        success = ((feasible and result['feasible'] is True and count >= task['target']['count']) or
                   (not feasible and result['feasible'] is False))
        return dict(schema_version=2, record_type='episode', method='vb_rag',
                    reading_mode=mode, condition=condition, world=world, task_id=task['id'], seed=SEED,
                    true_feasible=feasible, reported_feasible=result['feasible'], events=events,
                    used_mechanism_ids=result['used_mechanism_ids'], policy_trace=result['policy_trace'],
                    temporal_belief=result['temporal_belief'], runtime_guard=result['runtime_guard'],
                    reader={**bundle.get('reader', {}),
                            'search_count': int(bundle.get('reader', {}).get('search_count', 0)) +
                                            sum(r['executed'] for r in research_calls),
                            'vb_research': research_calls}, metrics={'SR': int(success)})

    def close(self):
        if self.proc.poll() is None:
            self.send({'shutdown': True})
            self.proc.wait(timeout=20)
        self.stderr.close()


def key(r):
    return (r['reading_mode'], r['condition'], r['world'], r['task_id'])


def actions(r, intent=False):
    return canonical([{'method': e['method'], 'params': e['params'],
                       **({'intent': e.get('intent')} if intent else {})} for e in r['events']])


def develop(pin):
    tasks = [r for r in jsonl(ROOT/'data/tasks.jsonl') if r['split'] == 'dev']
    if len(tasks) != 8:
        raise RuntimeError('dev set not eight tasks; no held-out execution permitted')
    cells = [('matched_1.16.5', '1.16.5'), ('matched_1.19', '1.19'),
             ('matched_1.21.11', '1.21.11'), ('mismatched_old2new', '1.21.11'),
             ('mismatched_new2old', '1.16.5')]
    output, problems = [], []
    worker = Worker('dev', pin)
    try:
        for i, (condition, world) in enumerate(cells):
            cache = p6.load_claims_cache(str(ROOT/'corpus/indexes/formal_p1'/condition),
                                        root=ROOT, pin_path=pin, reading_mode='all_public')
            for j, task in enumerate(tasks):
                bundle = p6.build_evidence(task, condition, index_dir=str(ROOT/'corpus/indexes/formal_p1'),
                                           root=ROOT, pin_path=pin, claims_cache=cache, fallback=None,
                                           fallback_dir=OUT/'forbidden_fallback', top_k=10)
                result = worker.run(task, bundle, world, 'all_public', condition, 'dev', f'dev_{i}_{j}')
                output.append(result)
                print(f'dev {len(output)}/40', flush=True)
    finally:
        worker.close()
    with (OUT/'dev/episodes.jsonl').open('w') as f:
        for record in output:
            f.write(canonical(record)+'\n')
    csvout('dev/summary.csv', [dict(task_id=r['task_id'], condition=r['condition'], world=r['world'],
                                   SR=r['metrics']['SR'], source_years=r['temporal_belief']['source_year_count'],
                                   formed_year=r['temporal_belief']['formed_year'],
                                   evaluation_asset_reads=r['runtime_guard']['evaluation_asset_reads']) for r in output])
    files = [ROOT/'scripts/w13_runtime.py', Path(__file__), ROOT/'tests/test_w13_runtime.py']
    (OUT/'CODE_FREEZE.json').write_text(json.dumps(dict(
        phase='after_dev8_before_held_out', prereg_commit='388488d',
        dev_tasks=sorted(r['id'] for r in tasks), dev_episodes=len(output),
        policy_parameter_search=False, all_runtime_guards_passed=True,
        source_hashes={str(p.relative_to(ROOT)): sha(p) for p in files}), indent=2)+'\n')


def scope(r):
    return 'cds' if r['condition'].startswith('cds_') else 'ext_v2' if r['task_id'].startswith('extv2_') else 'main'


def evaluate(pin):
    freeze = json.loads((OUT/'CODE_FREEZE.json').read_text())
    if any(sha(ROOT/name) != digest for name, digest in freeze['source_hashes'].items()):
        raise RuntimeError('SAFETY_STOP: policy/evaluation source differs from pre-held-out freeze')
    if not (OUT/'CODE_FREEZE_COMMIT.txt').exists():
        raise RuntimeError('code freeze must be committed first')
    tasks = {r['id']: r for path in (ROOT/'data/tasks.jsonl', ROOT/'data/ext_v2/ext_v2_tasks_20261016.jsonl')
             for r in jsonl(path)}
    standard = json.loads((ROOT/'config/resource_model.json').read_text())['standard_inventory']
    for t in tasks.values():
        t.setdefault('standard_items', standard)
    evidence = {s: {(r['mode'], r['condition'], r['task_id']): r['bundle'] for r in
                    jsonl(ROOT/f'release/package/assets/evidence/{s}.jsonl.gz')}
                for s in ('main', 'ext_v2', 'cds')}
    peers = {}
    for s in ('main', 'ext_v2', 'cds'):
        for r in jsonl(ROOT/f'release/package/assets/frozen/{s}.jsonl.gz'):
            if r['method'] == 'naive_rag':
                peers[(s, *key(r), 'naive_rag')] = r
    cp = list(jsonl(ROOT/'release/package/assets/frozen/cp_rag.jsonl.gz'))
    for r in cp:
        peers[(scope(r), *key(r), 'cp_rag')] = r
    worker = Worker('eval', pin)
    records, failures = [], []
    try:
        with (OUT/'episodes.jsonl').open('w') as f:
            for n, old in enumerate(cp):
                s, (mode, condition, world, task_id) = scope(old), key(old)
                bundle = evidence[s].get((mode, condition, task_id))
                try:
                    if bundle is None:
                        raise ValueError('missing frozen evidence')
                    rec = worker.run(tasks[task_id], bundle, world, mode, condition, 'eval', f'{n:04}')
                    rec['scope'] = s
                    records.append(rec)
                    f.write(canonical(rec)+'\n')
                    f.flush()
                except Exception as exc:
                    if 'SAFETY_STOP' in str(exc) or 'PermissionError' in str(exc):
                        raise
                    failures.append(dict(scope=s, reading_mode=mode, condition=condition, world=world,
                                         task_id=task_id, status='not_reported', reason=str(exc)))
                if (n+1) % 40 == 0:
                    print(f'eval {n+1}/{len(cp)}; complete={len(records)}, not_reported={len(failures)}', flush=True)
    finally:
        worker.close()
    csvout('not_reported.csv', failures)
    score_and_report(records, peers, tasks)


def score_and_report(records, peers, tasks):
    catalog = {r['id']: r for r in jsonl(ROOT/'data/conflicts.jsonl')}
    nodes = load_resource_nodes(ROOT/'harness/config/arena.json')
    rows, disagreements = [], []
    for vb in records:
        s = vb['scope']
        for method, rec in [('vb_rag', vb)] + [(m, peers.get((s, *key(vb), m)))
                                             for m in ('naive_rag', 'cp_rag')]:
            if rec is None:
                continue
            vectors = {mid: r['truth_vector'] for mid, r in catalog.items()}
            cc = catalog
            if s == 'cds':
                instance = rec['condition'].rsplit('__', 1)[0]
                private = json.loads((ROOT/f'reports/paperA/ext_v2/cds_evaluator/{instance}.json').read_text())
                cc = copy.deepcopy(catalog)
                for c in private['boundary_truth']:
                    if c['mechanism_id'] in cc:
                        cc[c['mechanism_id']]['truth_vector'] = dict.fromkeys(('1.16.5', '1.19', '1.21.11'), bool(c['truth']))
                        cc[c['mechanism_id']]['truth_vector'][private['comparison_world']] = not bool(c['truth'])
                vectors = {mid: r['truth_vector'] for mid, r in cc.items()}
            role = p6._action_role_metrics(rec['events'], rec['used_mechanism_ids'], rec['world'], vectors)
            rec['metrics'].update(role)
            feasible = bool(rec['true_feasible'])
            try:
                _, query, action, prop = w8.v3d(rec, cc, nodes, tasks[rec['task_id']]['target']['item'])
            except Exception as exc:
                query = action = prop = ''
                disagreements.append(dict(scope=s, task_id=rec['task_id'], reason='v3d_unavailable:'+str(exc)))
            terminal = w8.terminal(rec)
            row = dict(scope=s, reading_mode=rec['reading_mode'], condition=rec['condition'],
                       condition_group='matched' if rec['condition'].startswith('matched_') else
                       rec['condition'].rsplit('__',1)[-1] if s == 'cds' else rec['condition'],
                       world=rec['world'], task_id=rec['task_id'], method=method,
                       family_id=tasks[rec['task_id']].get('independent_family_id', ''),
                       true_feasible=int(feasible), SR=int(rec['metrics']['SR']),
                       feasible_SR=int(rec['metrics']['SR']) if feasible else '',
                       overconservative=int(feasible and terminal == 'overconservative') if feasible else '',
                       execution_failure=int(feasible and terminal == 'execution_failure') if feasible else '',
                       terminal=terminal, plan_acceptance=int(role['HTR_commit']),
                       specific_query=query, specific_action=action,
                       specific_contact=int(bool(query or action)) if query != '' else '',
                       v3d_propagation=prop, semantic_actions=len(rec['used_mechanism_ids']),
                       semantic_cost=role['primary_cost'], search_calls=int(rec['reader'].get('search_count',0)))
            if method == 'vb_rag':
                row.update(formed_year=rec['temporal_belief']['formed_year'],
                           source_year_count=rec['temporal_belief']['source_year_count'])
                for peer in ('naive_rag', 'cp_rag'):
                    other = peers.get((s, *key(rec), peer))
                    row['action_equal_'+peer] = int(actions(rec) == actions(other)) if other else ''
                    row['intent_inclusive_equal_'+peer] = int(actions(rec,True) == actions(other,True)) if other else ''
            rows.append(row)
    csvout('episode_metrics.csv', rows)
    csvout('scoring_discrepancies.csv', disagreements)
    groups = defaultdict(list)
    for row in rows:
        for grouping in ('overall', 'condition_group', 'condition_world'):
            cond = row['condition_group'] if grouping == 'condition_group' else row['condition'] if grouping == 'condition_world' else 'all'
            world = row['world'] if grouping == 'condition_world' else 'all'
            groups[(row['scope'], row['reading_mode'], row['method'], grouping, cond, world)].append(row)
    aggregate = []
    for k, rr in sorted(groups.items()):
        result = dict(zip(('scope','reading_mode','method','grouping','condition','world'), k))
        result['n'] = len(rr)
        result['feasible_n'] = sum(r['true_feasible'] for r in rr)
        for metric in METRICS:
            vs = [r[metric] for r in rr if r[metric] != '']
            result[metric+'_denominator'] = len(vs)
            result[metric] = sum(vs)/len(vs) if vs else ''
        aggregate.append(result)
    csvout('results_by_condition.csv', aggregate)
    effects = []
    for mode in MODES:
        selected = [r for r in rows if r['scope']=='main' and r['reading_mode']==mode and r['condition']=='mixed']
        keyed = {(r['method'],r['task_id'],r['world']):r for r in selected}
        tids = sorted({r['task_id'] for r in selected})
        draws = np.random.default_rng(SEED).integers(0,len(tids),size=(10000,len(tids)))
        for peer in ('naive_rag','cp_rag'):
            for metric in METRICS:
                arrays = []
                for method in ('vb_rag',peer):
                    numerator, denominator = [], []
                    for tid in tids:
                        vals = [r[metric] for (m,t,w),r in keyed.items() if m==method and t==tid and r[metric]!='']
                        numerator.append(sum(vals));denominator.append(len(vals))
                    arrays.append((np.array(numerator),np.array(denominator)))
                a,b = arrays
                if min(a[1].sum(),b[1].sum()) == 0:
                    continue
                raw = a[0].sum()/a[1].sum()-b[0].sum()/b[1].sum()
                with np.errstate(invalid='ignore',divide='ignore'):
                    boot = a[0][draws].sum(1)/a[1][draws].sum(1)-b[0][draws].sum(1)/b[1][draws].sum(1)
                lo,hi = np.nanquantile(boot,[.025,.975])
                effects.append(dict(scope='main',condition='mixed',reading_mode=mode,peer=peer,metric=metric,
                                    VB_minus_peer=raw,ci95_low=float(lo),ci95_high=float(hi),paired_tasks=len(tids),
                                    bootstrap_draws=10000,seed=SEED))
    csvout('PRIMARY_mixed_paired_effects.csv',effects)
    time_groups=defaultdict(list)
    release_year={'1.16.5':2021,'1.19':2022,'1.21.11':2025}
    for r in rows:
        if r['method']=='vb_rag':
            time_groups[(r['scope'],r['reading_mode'],r['condition_group'])].append(r)
    time_rows=[]
    for k,rr in sorted(time_groups.items()):
        formed=[r for r in rr if r['formed_year'] is not None]
        correct=sum(r['formed_year']==release_year[r['world']] for r in formed)
        time_rows.append(dict(scope=k[0],reading_mode=k[1],condition=k[2],episodes=len(rr),
                              formed=len(formed),correct=correct,accuracy_formed=correct/len(formed) if formed else '',
                              formation_coverage=len(formed)/len(rr),accuracy_all=correct/len(rr),
                              single_year_episodes=sum(r['source_year_count']<2 for r in rr),
                              single_year_formed=sum(r['source_year_count']<2 and r['formed_year'] is not None for r in rr)))
    csvout('temporal_belief_accuracy.csv', time_rows)
    (OUT/'RUN_SUMMARY.json').write_text(json.dumps(dict(episodes=len(records),peer_metric_rows=len(rows),
        scope_counts=dict(Counter(r['scope'] for r in records)),LLM_calls=0,
        runtime_evaluation_asset_reads=sum(r['runtime_guard']['evaluation_asset_reads'] for r in records)),indent=2)+'\n')
    print('W13 held-out evaluation and paired metrics complete',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=('dev','eval','score'))
    args=parser.parse_args()
    pin=setup(args.phase)
    if args.phase=='dev':
        develop(pin)
    elif args.phase=='eval':
        evaluate(pin)
    else:
        raise ValueError('score is reserved; use frozen completed outputs')


if __name__=='__main__':
    main()
