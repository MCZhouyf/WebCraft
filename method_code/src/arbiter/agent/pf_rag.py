"""A22 public-evidence-only probe/filter policy; no file or network access."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Sequence

from .aggregation import SearchDerivedClaim, _timestamp, naive_rag
from .core import ArbiterAgent, ExecutionPolicy
from .planning import BeliefPlanner, CandidatePlan, MechanismOperator, action_signature


def relation_key(op: MechanismOperator) -> tuple[str, str]:
    if op.method == 'mine':
        return 'drop', str(op.params['node_type'])
    if op.method == 'check_name':
        return 'name', str(op.params['identifier'])
    return op.method, op.output


def payload_key(op: MechanismOperator) -> str:
    return json.dumps({
        'method': op.method, 'params': op.params, 'inputs': op.input_options,
        'output': op.output, 'count': op.output_count, 'requires': op.requires,
        'layout_slots': op.layout_slots,
    }, sort_keys=True, separators=(',', ':'))


def identifiers(op: MechanismOperator) -> frozenset[str]:
    values = [op.output, *op.requires]
    values.extend(item for options in op.input_options for item in options)
    values.extend(op.params.get(key) for key in ('node_type', 'fuel', 'station', 'identifier'))
    return frozenset(value.removeprefix('minecraft:') for value in values
                     if isinstance(value, str) and value)


@dataclass(frozen=True)
class Candidate:
    operator: MechanismOperator
    provenance: tuple[SearchDerivedClaim, ...]
    index: int

    @property
    def revisions(self) -> frozenset[tuple[str, str, str]]:
        return frozenset(tuple(str(p.hit[k]) for k in ('source_site', 'page_title', 'revid'))
                         for p in self.provenance)


class FilterState:
    def __init__(self, operators: Sequence[MechanismOperator], claims: Sequence[SearchDerivedClaim]):
        grouped = defaultdict(list)
        for claim in claims:
            grouped[claim.mechanism_id].append(claim)
        if len({op.mechanism_id for op in operators}) != len(operators):
            raise ValueError('compiled operator IDs must be unique')
        if any(not grouped[op.mechanism_id] for op in operators):
            raise ValueError('each candidate requires public provenance')
        self.candidates = tuple(Candidate(op, tuple(grouped[op.mechanism_id]), index)
                                for index, op in enumerate(operators))
        self.removed: set[str] = set()
        self.observations: dict[str, bool | None] = {}
        self.retirements: list[dict[str, Any]] = []

    def active(self) -> list[Candidate]:
        return [c for c in self.candidates if c.operator.mechanism_id not in self.removed]

    def conflict_pairs(self) -> list[tuple[Candidate, Candidate]]:
        groups: dict[tuple[str, str], dict[str, Candidate]] = defaultdict(dict)
        for c in self.active():
            groups[relation_key(c.operator)].setdefault(payload_key(c.operator), c)
        return [pair for key in sorted(groups)
                for pair in combinations(groups[key].values(), 2)]

    def scores(self, *, fallback: bool = False) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for left, right in self.conflict_pairs():
            refs = ({left.operator.output, right.operator.output} if fallback else
                    identifiers(left.operator) ^ identifiers(right.operator))
            counts.update(name for name in refs if name not in self.observations)
        return dict(counts)

    def next_probe(self, *, fallback: bool = False) -> tuple[str, int] | None:
        scores = self.scores(fallback=fallback)
        return min(scores.items(), key=lambda pair: (-pair[1], pair[0])) if scores else None

    def observe_name(self, name: str, response: dict[str, Any]) -> None:
        value = response.get('resolvable')
        self.observations[name] = value if isinstance(value, bool) else None
        if value is False:
            for candidate in self.active():
                if name in identifiers(candidate.operator):
                    self.retire(candidate.operator.mechanism_id, 'absent_identifier', name)

    def retire(self, candidate_id: str, reason: str, identifier: str | None = None) -> None:
        if candidate_id not in {c.operator.mechanism_id for c in self.candidates}:
            raise ValueError('cannot retire an unknown candidate')
        if candidate_id not in self.removed:
            self.removed.add(candidate_id)
            self.retirements.append({'candidate_id': candidate_id, 'reason': reason,
                                     'identifier': identifier})

    def suspect_revisions(self) -> frozenset[tuple[str, str, str]]:
        rejected, supported = set(), set()
        for c in self.candidates:
            if c.operator.mechanism_id in self.removed:
                rejected.update(c.revisions)
            if any(self.observations.get(name) is True for name in identifiers(c.operator)):
                supported.update(c.revisions)
        return frozenset(rejected - supported)

    def priority(self, candidate: Candidate) -> tuple[int, float, int]:
        suspect = self.suspect_revisions()
        return min((
            int(tuple(str(p.hit[k]) for k in ('source_site', 'page_title', 'revid')) in suspect),
            -_timestamp(p.hit['timestamp']), candidate.index,
        ) for p in candidate.provenance)

    def choose_plan(self, plans: Sequence[CandidatePlan]) -> CandidatePlan:
        if not plans:
            raise ValueError('cannot choose from an empty plan set')
        groups = defaultdict(set)
        lookup = {}
        for c in self.active():
            groups[relation_key(c.operator)].add(self.priority(c)[0])
            lookup[c.operator.mechanism_id] = c
        affected = {key for key, priorities in groups.items() if priorities == {0, 1}}
        if not affected:
            return plans[0]
        tied = [p for p in plans if p.objective == plans[0].objective]
        def rank(plan):
            return tuple(self.priority(lookup[s.mechanism_id])
                         if relation_key(lookup[s.mechanism_id].operator) in affected
                         else (0, 0.0, 0) for s in plan.steps)
        return min(tied, key=rank)


RESOURCE_ERRORS = frozenset({
    'missing ingredient', 'insufficient fuel', 'missing tool',
    'insufficient tool tier', 'node depleted', 'unknown node',
    'unknown resource node', 'resource node is empty',
    'missing smelting input or fuel', 'crafting table unavailable',
    'furnace unavailable',
})


def is_refutation(op: MechanismOperator, step: Any, inventory: Counter,
                  response: dict[str, Any]) -> bool:
    """Only deny a candidate when observable preconditions and feedback suffice."""
    if response.get('error') in RESOURCE_ERRORS:
        return False
    if any(inventory[name] < 1 for name in op.requires):
        return False
    if step.method == 'craft_grid':
        needed = Counter(name for name in step.params.get('layout', []) if name)
        if any(inventory[name] < count for name, count in needed.items()):
            return False
    elif step.method == 'smelt':
        n = int(step.params.get('n', 1))
        if (inventory[step.params['input']] < n
                or inventory[step.params['fuel']] < math.ceil(n / 8)):
            return False
    outputs = []
    if isinstance(response.get('product'), dict):
        outputs.append(response['product'])
    if isinstance(response.get('items'), list):
        outputs.extend(response['items'])
    actual = sum(int(row.get('count', 0)) for row in outputs
                 if isinstance(row, dict) and row.get('name') == step.expected_output)
    if response.get('success') is True:
        return actual < step.expected_count
    if (step.method == 'craft_grid' and response.get('success') is False
            and 'product' in response and response['product'] is None
            and 'error' not in response):
        return True
    # Unknown errors may conceal a prerequisite failure. Do not infer falsity.
    return response.get('success') is False and response.get('error') in {
        'no matching recipe', 'unknown recipe', 'not smeltable',
    }


class PFRAGAgent(ArbiterAgent):
    def configure_public(self, operators, claims, *, budget: int, fallback: bool = False):
        if budget not in {1, 3, 5}:
            raise ValueError('B must come from the registered development grid')
        self.filter_state = FilterState(operators, claims)
        self.opening_budget = budget
        self.fallback = bool(fallback)
        if claims:
            self.configure(operators, naive_rag(claims).belief)
        else:
            if operators:
                raise ValueError('operators without claims')
            self.configure((), naive_rag([]).belief)

    def solve(self, *, policy: ExecutionPolicy | None = None, strategy=None):
        policy = policy or ExecutionPolicy()
        state = self.filter_state
        traces, probe_trace, used = [], [], []
        blocked = set()
        spent = 0
        probes = 0
        for _ in range(min(self.opening_budget, policy.max_actions)):
            if spent >= policy.max_actions:
                break
            selected = state.next_probe(fallback=self.fallback)
            if selected is None:
                break
            name, score = selected
            result = self.api.check_name(name, intent='probe')
            probes += 1
            spent += int(result.get('cost', {}).get('primary', 1))
            state.observe_name(name, result)
            probe_trace.append({'identifier': name, 'score': score,
                                'resolvable': state.observations[name],
                                'fallback_enabled': self.fallback})

        def finish(success, feasible):
            self.api.submit({'feasible': feasible})
            return {'success': success, 'feasible': feasible,
                    'used_mechanism_ids': used, 'policy_trace': traces,
                    'opening_probes': probe_trace, 'probe_count': probes,
                    'retirements': state.retirements,
                    'suspect_revisions': sorted(state.suspect_revisions())}

        while len(used) + probes < policy.max_actions and spent < policy.max_actions:
            self.kit = Counter({x['name']: x['count'] for x in self.api.inventory()})
            if self.kit[self.goal.item] >= self.goal.count:
                return finish(True, True)
            operators = [replace(c.operator, probability=self._belief.mechanism_probability(
                c.operator.mechanism_id)) for c in state.active()]
            plans = BeliefPlanner(
                operators, self.kit, uncertainty_weight=policy.uncertainty_weight,
                action_limit=policy.max_actions - spent,
                inventory_aware=policy.inventory_aware,
                blocked_actions=frozenset(blocked),
            ).candidates(self.goal.item, self.goal.count, policy.n_plans)
            if not plans:
                return finish(False, False)
            plan = state.choose_plan(plans)
            step = plan.steps[0]
            signature = action_signature(step.method, step.params, step.expected_output)
            if signature in blocked or spent + step.cost > policy.max_actions:
                return finish(False, None)
            op = next(c.operator for c in state.active()
                      if c.operator.mechanism_id == step.mechanism_id)
            result = self._call(step.method, step.params, intent='commit')
            used.append(step.mechanism_id)
            spent += int(result.get('cost', {}).get('primary', step.cost))
            observed = (result.get('success') is not False
                        and step.expected_output in self._produced_names(result))
            refuted = is_refutation(op, step, self.kit, result)
            if not observed or refuted:
                blocked.add(signature)
            if refuted:
                state.retire(step.mechanism_id, 'execution_refutation')
            traces.append({'intent': 'commit', 'mechanism_id': step.mechanism_id,
                           'action_signature': signature, 'observed': observed,
                           'refuted': refuted, 'plan_mechanism_ids': list(plan.mechanism_ids),
                           'suspect_revisions': sorted(state.suspect_revisions())})
        return finish(False, None)
