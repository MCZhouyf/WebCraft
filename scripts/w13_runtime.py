#!/usr/bin/env python3
"""Isolated VB-RAG policy: public evidence and observations, never world truth."""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARB = ROOT / 'method_code'
sys.path[:0] = [str(ARB / 'src')]
from arbiter.agent.aggregation import SearchDerivedClaim, naive_rag, operator_from_assertion
from arbiter.agent.planning import BeliefPlanner, PlanStep, action_signature, resolved_params
from arbiter.evaluation.claim_compiler import CorpusItemNormalizer, compile_claim_assertions

POLICY = dict(max_actions=90, max_probes=12, n_plans=5, uncertainty_weight=.35,
              inventory_aware=True, eta=math.log(3), threshold=.80, odds=3, clip=20)
PUBLIC_FIELDS = ('text', 'source_site', 'page_title', 'revid', 'timestamp', 'chunk_id')
RESOURCE_ERRORS = {'missing ingredient', 'missing smelting input or fuel',
                   'insufficient fuel', 'mining tool is insufficient', 'unknown resource node',
                   'resource node is empty', 'crafting table unavailable', 'furnace unavailable',
                   'action could not be completed', 'smelting timeout'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def year(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).year
    except (ValueError, TypeError, OverflowError):
        return None


def relation(op):
    if op.method == 'mine':
        return ('drop', str(op.params['node_type']))
    if op.method == 'check_name':
        return ('name', str(op.params['identifier']))
    return (op.method, str(op.output))


def payload_signature(op):
    return canonical(dict(method=op.method, params=op.params, inputs=op.input_options,
                          output=op.output, count=op.output_count))


def compile_bundle(bundle, normalizer):
    """Same compilation/parent-family rule as p6_execution._claims, no evaluator import."""
    hits = {str(r['chunk_id']): {k: r[k] for k in PUBLIC_FIELDS} for r in bundle['search_rows']}
    by_id = {str(c.get('claim_id')): c for c in bundle['claims']}
    derived, ops = [], {}
    for raw in bundle['claims']:
        if str(raw['chunk_id']) not in hits:
            continue
        parent = by_id.get(str(raw.get('parent_claim_id')))
        pp = parent.get('payload', {}) if parent else {}
        if ((isinstance(pp.get('layout_options'), list) and len(pp['layout_options']) == 9)
                or (parent and parent.get('claim_type') == 'smelting'
                    and isinstance(pp.get('ingredients'), list) and len(pp['ingredients']) > 1)):
            continue
        hit = hits[str(raw['chunk_id'])]
        for assertion in compile_claim_assertions(raw, hit['text'],
                set(bundle.get('mineable_nodes', [])) or None, normalizer):
            try:
                op = operator_from_assertion(assertion)
            except ValueError:
                continue
            ops[op.mechanism_id] = op
            derived.append(SearchDerivedClaim(hit, op.mechanism_id, raw['polarity'] == 'positive'))
    return derived, list(ops.values())


class TemporalBelief:
    def __init__(self):
        self.weights = {}
        self.contrasted = False
        self.updates = 0

    def add_hits(self, hits):
        for hit in hits:
            y = year(hit.get('timestamp'))
            if y is not None:
                self.weights.setdefault(y, 0.)

    def posterior(self):
        if not self.weights:
            return {}
        offset = max(self.weights.values())
        values = {y: math.exp(w - offset) for y, w in self.weights.items()}
        return {y: v / sum(values.values()) for y, v in values.items()}

    def estimate(self):
        posterior = self.posterior()
        order = sorted(posterior, key=lambda y: (posterior[y], y), reverse=True)
        if len(order) < 2:
            return None
        first, second = order[:2]
        if (self.contrasted and posterior[first] >= POLICY['threshold'] and
                posterior[first] / posterior[second] >= POLICY['odds']):
            return first
        return None

    def update(self, evidence):
        grouped = defaultdict(set)
        for y, source, sign in evidence:
            if y is not None:
                self.weights.setdefault(y, 0.)
                grouped[(y, source)].add(sign)
        by_year = defaultdict(list)
        for (y, source), signs in grouped.items():
            by_year[y].append(next(iter(signs)) if len(signs) == 1 else 0)
        increments = {y: sum(signs) / len(signs) for y, signs in by_year.items()}
        all_increments = [increments.get(y, 0.) for y in self.weights]
        if all_increments and max(all_increments) != min(all_increments):
            self.contrasted = True
        for y, value in increments.items():
            self.weights[y] = max(-POLICY['clip'], min(POLICY['clip'],
                                 self.weights[y] + POLICY['eta'] * value))
        self.updates += int(bool(evidence))
        return increments


def produced(result):
    rows = list(result.get('items', [])) if isinstance(result, dict) else []
    if isinstance(result.get('product'), dict):
        rows.append(result['product'])
    return Counter({r['name'].removeprefix('minecraft:'): int(r.get('count', 1))
                    for r in rows if isinstance(r, dict) and isinstance(r.get('name'), str)})


class VBRAG:
    def __init__(self, public, call, normalizer):
        if set(public) != {'goal', 'kit', 'bundle'}:
            raise ValueError('non-public runtime envelope')
        if set(public['bundle']) - {'search_rows', 'claims', 'mineable_nodes'}:
            raise ValueError('non-public evidence envelope')
        self.goal = public['goal']
        self.bundle = public['bundle']
        self.call = call
        self.normalizer = normalizer
        self.time = TemporalBelief()
        self.time.add_hits(self.bundle['search_rows'])
        self.local = {}
        self.used, self.probes, self.trace = [], [], []
        self.researched, self.blocked = set(), set()
        self.denied = None
        self.spent = 0

    def compile(self, filtered=False):
        estimate = self.time.estimate() if filtered else None
        if estimate is None:
            return compile_bundle(self.bundle, self.normalizer)
        hits = [r for r in self.bundle['search_rows'] if year(r.get('timestamp')) == estimate]
        ids = {r['chunk_id'] for r in hits}
        return compile_bundle({**self.bundle, 'search_rows': hits,
                               'claims': [c for c in self.bundle['claims'] if c['chunk_id'] in ids]},
                              self.normalizer)

    def research(self, key):
        if key is None or key in self.researched:
            return False
        self.researched.add(key)
        result = self.call('research', {'subject': key[1], 'goal': self.goal['item']})
        existing = {r['chunk_id'] for r in self.bundle['search_rows']}
        new_hits = [r for r in result['search_rows'] if r['chunk_id'] not in existing]
        existing_claims = {c['claim_id'] for c in self.bundle['claims']}
        new_claims = [c for c in result['claims'] if c['claim_id'] not in existing_claims]
        self.bundle['search_rows'].extend(new_hits)
        self.bundle['claims'].extend(new_claims)
        self.time.add_hits(new_hits)
        self.trace.append({'decision': 'research', 'relation': list(key),
                           'new_chunks': len(new_hits), 'new_claims': len(new_claims)})
        return bool(new_hits or new_claims)

    def finish(self, success, feasible):
        self.call('submit', {'result': {'feasible': feasible}})
        return dict(success=success, feasible=feasible, used_mechanism_ids=self.used,
                    probe_mechanism_ids=self.probes, policy_trace=self.trace,
                    temporal_belief={'formed_year': self.time.estimate(),
                                     'posterior': self.time.posterior(),
                                     'updates': self.time.updates,
                                     'source_year_count': len(self.time.weights)})

    def solve(self):
        while len(self.used) < POLICY['max_actions'] and self.spent < POLICY['max_actions']:
            kit = Counter({r['name']: int(r['count']) for r in self.call('inventory', {})})
            if kit[self.goal['item']] >= self.goal['count']:
                return self.finish(True, True)
            derived, operators = self.compile(filtered=True)
            if derived:
                belief = naive_rag(derived).belief
                operators = [replace(op, probability=float(self.local.get(
                    op.mechanism_id, belief.mechanism_probability(op.mechanism_id)))) for op in operators]
            planner = BeliefPlanner(operators, kit, uncertainty_weight=POLICY['uncertainty_weight'],
                                   action_limit=POLICY['max_actions'] - self.spent,
                                   inventory_aware=True, blocked_actions=frozenset(self.blocked))
            plans = planner.candidates(self.goal['item'], self.goal['count'], POLICY['n_plans'])
            if not plans:
                key = self.denied
                if key is None and self.time.estimate() is not None:
                    _, unfiltered = self.compile()
                    remaining = {op.mechanism_id for op in operators}
                    missing = [relation(op) for op in unfiltered if op.mechanism_id not in remaining]
                    key = min(missing, default=None)
                if self.research(key):
                    continue
                return self.finish(False, False)
            step = plans[0].steps[0]
            selected = next(op for op in operators if op.mechanism_id == step.mechanism_id)
            key = relation(selected)
            group = [op for op in operators if relation(op) == key]
            signatures = {payload_signature(op) for op in group}
            probe = (len(signatures) >= 2 and step.mechanism_id not in self.local
                     and len(self.probes) < POLICY['max_probes'])
            resolved = planner._resolved()
            if probe:
                candidates = []
                for op, choices in resolved:
                    if relation(op) != key or op.probability <= 0:
                        continue
                    required = Counter(choices)
                    if op.method == 'smelt':
                        required[op.params.get('fuel', 'coal')] += 1
                    if any(kit[x] < n for x, n in required.items()) or any(kit[x] < 1 for x in op.requires):
                        continue
                    params = resolved_params(op, choices)
                    candidates.append(PlanStep(op.mechanism_id, op.method, params,
                                               op.output, op.output_count, op.action_cost))
                distinguishable = {(s.method, canonical(s.params), s.expected_output, s.expected_count)
                                   for s in candidates}
                if len(distinguishable) >= 2:
                    step = min(candidates, key=lambda s: (s.cost, s.method,
                               canonical(s.params), s.mechanism_id))
                else:
                    probe = False
            signature = action_signature(step.method, step.params, step.expected_output)
            if signature in self.blocked or self.spent + step.cost > POLICY['max_actions']:
                return self.finish(False, None)
            result = self.call(step.method, step.params, 'probe' if probe else 'commit')
            self.used.append(step.mechanism_id)
            if probe:
                self.probes.append(step.mechanism_id)
            self.spent += int(result.get('cost', {}).get('primary', step.cost))
            observed = (result.get('success') is not False and
                        (bool(result.get('resolvable')) if step.method == 'check_name' else
                         produced(result) == Counter({step.expected_output: step.expected_count})))
            error = result.get('error')
            informative = not error and error not in RESOURCE_ERRORS
            evidence = []
            all_derived, all_ops = self.compile()
            if informative:
                full = BeliefPlanner(all_ops, kit, inventory_aware=True)
                for op, choices in full._resolved():
                    if relation(op) != key:
                        continue
                    params = resolved_params(op, choices,
                                             int(step.params.get('n', 1)) if step.method == 'smelt' else 1)
                    if step.method != 'mine' and canonical(params) != canonical(step.params):
                        continue
                    match = (bool(result.get('resolvable')) if step.method == 'check_name' else
                             result.get('success') is not False and
                             produced(result) == Counter({op.output: op.output_count * (
                                 int(step.params.get('n', 1)) if step.method == 'smelt' else 1)}))
                    self.local[op.mechanism_id] = match
                    for claim in all_derived:
                        if claim.mechanism_id == op.mechanism_id:
                            evidence.append((year(claim.hit['timestamp']), claim.hit['source_site'],
                                             1 if match == claim.value else -1))
            increments = self.time.update(evidence)
            if not observed:
                self.blocked.add(signature)
                self.denied = key
            self.trace.append({'decision': 'probe_conflict' if probe else 'execute_naive',
                               'mechanism_id': step.mechanism_id, 'intent': 'probe' if probe else 'commit',
                               'relation_key': list(key), 'observed': observed, 'informative': informative,
                               'epoch_increments': increments, 'formed_year': self.time.estimate(),
                               'posterior': self.time.posterior()})
        return self.finish(False, None)


def filesystem_guard():
    forbidden = ('/truth/', '/data/', '/oracle/', '/reports/', '/artifacts/',
                 '/evaluator/', '/assets/', 'separator_truth', 'separator_empirical')
    tmp = Path(os.environ['TMPDIR']).resolve()
    opened = set()

    def guard(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.bind'):
            raise PermissionError('runtime network denied')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.is_relative_to(tmp):
                return
            value = str(path)
            if any(token in value for token in forbidden):
                raise PermissionError('runtime forbidden evaluation asset')
            if path.is_relative_to(ROOT):
                allowed = (path == Path(__file__).resolve() or
                           path in {ROOT / 'corpus/gold_claims.py', ROOT / 'corpus/normalize_claims_cache.py',
                                    ROOT / 'corpus/CLAIM_PAYLOAD_SCHEMA.md'})
                if not allowed:
                    raise PermissionError('runtime non-public project asset')
            if any(part in value for part in ('/env/', '/.ssh/', 'auth.json', 'llm.env')):
                raise PermissionError('runtime credentials denied')
            opened.add(value)
    sys.addaudithook(guard)
    return opened


def main():
    opened = filesystem_guard()
    config = json.loads((ARB / 'config/arbiter_hparams.json').read_text())
    normalizer = CorpusItemNormalizer.from_config(ROOT, config['compiler']['normalizer_sources'])

    def rpc(method, params, intent=None):
        print(canonical({'type': 'call', 'method': method, 'params': params, 'intent': intent}), flush=True)
        reply = json.loads(sys.stdin.readline())
        if 'rpc_error' in reply:
            raise RuntimeError(reply['rpc_error'])
        return reply['result']

    for line in sys.stdin:
        request = json.loads(line)
        if request.get('shutdown'):
            return
        try:
            result = VBRAG(request, rpc, normalizer).solve()
            result['runtime_guard'] = {'evaluation_asset_reads': 0, 'network_calls': 0,
                                       'public_compiler_source_reads': len(opened)}
            print(canonical({'type': 'done', 'result': result}), flush=True)
        except Exception as exc:
            print(canonical({'type': 'error', 'error_type': type(exc).__name__, 'message': str(exc)}), flush=True)


if __name__ == '__main__':
    main()
