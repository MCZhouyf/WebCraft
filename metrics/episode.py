from collections import Counter, deque


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def item_contributors(events, goal, initial_inventory=None):
    """FIFO item provenance. Initial items have no producing action."""
    stock = {name:deque([(count,None)]) for name,count in (initial_inventory or {}).items() if count>0}
    edges = {}
    for index, event in enumerate(events):
        flow = event.get('item_flow', {})
        dependencies = set()
        for item in flow.get('consumed', []):
            count = item['count']
            queue = stock.setdefault(item['name'], deque())
            while count and queue:
                amount, producer = queue.popleft()
                taken = min(amount, count)
                if producer is not None: dependencies.add(producer)
                count -= taken
                if amount > taken: queue.appendleft((amount-taken, producer))
        edges[index] = dependencies
        for item in flow.get('produced', []):
            stock.setdefault(item['name'], deque()).append((item['count'], index))
    required = goal['count']
    ancestors = set()
    for amount, producer in stock.get(goal['item'], []):
        if required <= 0: break
        required -= amount
        if producer is not None: ancestors.add(producer)
    pending = list(ancestors)
    while pending:
        index = pending.pop()
        for parent in edges.get(index, set()):
            if parent not in ancestors: ancestors.add(parent); pending.append(parent)
    return ancestors


def episode_metrics(events, *, goal, world, true_feasible, success, reported_feasible,
                    version_hashes, mechanism_vectors, oracle_cost=None, task_type='planning',
                    claim_answers=None, claim_truth=None, initial_inventory=None):
    if any(e.get('schema_version') != 2 or 'primary' not in e.get('cost', {}) for e in events):
        raise ValueError('metrics require schema-v2 costs; legacy logs cannot be silently interpreted')
    primary = sum(e['cost']['primary'] for e in events)
    probe_indices = [index for index, event in enumerate(events)
                     if event.get('intent') == 'probe']
    probe_cost = sum(events[index]['cost']['primary'] for index in probe_indices)
    reports = []
    cumulative = 0
    stale = []
    unknown = []
    for index, event in enumerate(events):
        cumulative += event['cost']['primary']
        if event['method'] == 'report_version':
            decoded = version_hashes.get(event['params'].get('guess_sha256'))
            reports.append((decoded, cumulative))
        vector = mechanism_vectors.get(index)
        if vector is not None:
            if vector[world] is None: unknown.append(index)
            elif vector[world] is False and any(value is True for key,value in vector.items() if key!=world): stale.append(index)
    ic = None
    if reports:
        # Once the final report is wrong or unknown, no stable correct estimate exists.
        if reports[-1][0] == world:
            suffix = len(reports)-1
            while suffix>0 and reports[suffix-1][0] == world: suffix-=1
            ic = reports[suffix][1]
    if initial_inventory is None and events and events[0]['method']=='inventory':
        initial_inventory=Counter()
        for item in events[0]['result']: initial_inventory[item['name']]+=item['count']
    contributed = item_contributors(events, goal, initial_inventory)
    ov_candidates = [i for i,e in enumerate(events) if e['method'] in ('craft_grid','check_name')]
    excessive = []
    unclassified = []
    for index in ov_candidates:
        vector = mechanism_vectors.get(index)
        if vector is None or any(value is None for value in vector.values()):
            unclassified.append(index)
        elif index not in contributed and len(set(vector.values())) == 1:
            excessive.append(index)
    answers = [] if claim_answers is None else claim_answers
    truths = [] if claim_truth is None else claim_truth
    if len(answers)!=len(truths): raise ValueError('claim answer/truth length mismatch')
    if any(x not in (True,False,'unknown') for x in answers): raise ValueError('invalid claim answer')
    iaa_correct = sum(a!='unknown' and a==t for a,t in zip(answers,truths))
    over = primary>1.5*oracle_cost if task_type=='efficiency' and oracle_cost is not None else None
    stale_commit = [index for index in stale if events[index].get('intent') != 'probe']
    htr_all = int(bool(stale))
    htr_commit = int(bool(stale_commit))
    return {
        'SR':int(success),
        # HTR remains a compatibility alias for the original all-action definition.
        'HTR':htr_all, 'HTR_all':htr_all, 'HTR_commit':htr_commit,
        'stale_event_indices':stale, 'stale_commit_event_indices':stale_commit,
        'probe_count':len(probe_indices), 'probe_cost':probe_cost,
        'unclassified_truth_indices':unknown, 'primary_cost':primary, 'IC':ic,
        'IC_status':'NA_no_report' if not reports else ('never_stably_correct' if ic is None else 'identified'),
        'IAA':ratio(iaa_correct,len(answers)), 'IAA_unknown_rate':ratio(answers.count('unknown'),len(answers)),
        'true_feasible':bool(true_feasible), 'reported_feasible':reported_feasible,
        'false_abstention':bool(true_feasible and reported_feasible is False),
        'OPR':None if over is None else int(over), 'OPR_with_stale':None if over is None else int(over and bool(stale)),
        'OVR':None if unclassified else ratio(len(excessive),len(ov_candidates)),
        'OVR_numerator':len(excessive),'OVR_denominator':len(ov_candidates),
        'OVR_unclassified_indices':unclassified,'contributing_events':sorted(contributed)
    }


def aggregate(rows):
    tp=sum(r['true_feasible'] and r['reported_feasible'] is True for r in rows)
    fp=sum(not r['true_feasible'] and r['reported_feasible'] is True for r in rows)
    fn=sum(r['true_feasible'] and r['reported_feasible'] is not True for r in rows)
    precision=ratio(tp,tp+fp); recall=ratio(tp,tp+fn)
    result={'episodes':len(rows),'feasibility_precision':precision,'feasibility_recall':recall,
            'feasibility_F1':ratio(2*tp,2*tp+fp+fn),
            'false_abstention_rate':ratio(sum(r['false_abstention'] for r in rows),sum(r['true_feasible'] for r in rows))}
    for metric in ['SR','HTR_all','HTR_commit','IC','IAA','IAA_unknown_rate',
                   'OPR','OPR_with_stale','OVR']:
        values=[r[metric] for r in rows if r[metric] is not None]
        result[metric]=ratio(sum(values),len(values))
        result[metric+'_n']=len(values)
    # Preserve the legacy aggregate key while formal tables use both HTR columns.
    result['HTR']=result['HTR_all']
    result['HTR_n']=result['HTR_all_n']
    result['probe_count']=sum(r['probe_count'] for r in rows)
    result['probe_cost']=sum(r['probe_cost'] for r in rows)
    return result
