"""A17 offline statistics; synthetic, for calibration only."""
from scripts import analyze_a17_ext_agents as analysis


def test_family_weighting_is_not_episode_weighting():
    result = analysis.bootstrap_family([('a', 1)] * 9 + [('b', 0)])
    assert result['estimate'] == .5
    assert result['families'] == 2
    assert result['n'] == 10
    assert (result['low'], result['high']) == (0, 1)
    assert result == analysis.bootstrap_family([('a', 1)] * 9 + [('b', 0)])


def test_sparse_and_empty_intervals_are_not_fabricated():
    single = analysis.bootstrap_family([('a', 1), ('a', 0)])
    assert single['estimate'] == .5
    assert single['low'] is single['high'] is None
    empty = analysis.bootstrap_family([])
    assert empty['estimate'] is None
    assert empty['zero_denominator_draws'] == analysis.DRAWS


def test_infeasible_is_excluded_only_from_feasible_denominators():
    row = {'true_feasible': 0}
    assert not analysis.eligible(row, 'feasible_SR')
    assert not analysis.eligible(row, 'overconservatism')
    assert analysis.eligible(row, 'SR')


def test_pairs_use_same_task_and_world_without_imputing_missing():
    data = []
    for task_id, family, world, mismatch, matched in [
        ('a', 'f1', '1.21.11', 'mismatched_old2new', 'matched_1.21.11'),
        ('b', 'f2', '1.16.5', 'mismatched_new2old', 'matched_1.16.5'),
    ]:
        for condition, value in ((mismatch, 1), (matched, 0)):
            data.append(dict(method='react', task_id=task_id, family_id=family, world=world,
                             condition=condition, true_feasible=1,
                             **{m: value for m in analysis.METRICS}))
    output, missing = analysis.paired_contrasts(data)
    combined = [r for r in output if r['contrast'] == 'combined_directions' and r['metric'] == 'SR'][0]
    assert combined['estimate'] == 1
    assert combined['paired_episodes'] == 2
    assert combined['families'] == 2
    assert len(missing) == 2


def test_distribution_retains_zero_but_not_infeasible_na():
    result = analysis.distribution([0, 2, 4])
    assert result['minimum'] == 0
    assert result['mean'] == 2
    assert analysis.distribution([])['mean'] is None


def test_completed_llm_record_replay_matches_online_gold():
    from scripts.run_a17_ext_agents import OUT, ROOT, WEB
    paths = sorted(OUT.glob('worker_*/episodes.jsonl'))
    if not paths:
        import pytest
        pytest.skip('no local A17 episode artifact')
    record = next(analysis.rows(paths[0]))
    task = next(t for t in analysis.rows(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl') if t['id'] == record['task_id'])
    conflicts = {r['id']: r for r in analysis.load_conflicts(WEB / 'data/conflicts.jsonl')}
    scored, _ = analysis.score_record(record, list(analysis.rows(ROOT / record['action_log'])), task,
                                      conflicts, analysis.load_resource_nodes(WEB / 'harness/config/arena.json'), 'test')
    assert scored['SR'] == record['gold_score']['SR']
    assert scored['terminal'] == record['gold_score']['terminal']
    assert scored['semantic_actions'] == record['gold_score']['semantic_actions']
    empty, annotations = analysis.score_record(record, list(analysis.rows(ROOT / record['action_log'])), task,
                                              conflicts, analysis.load_resource_nodes(WEB / 'harness/config/arena.json'),
                                              'test', stale_override={})
    assert annotations == []
    assert empty['HTR_all'] == 0
    assert empty['SR'] == scored['SR']


def test_canonical_source_is_attribution_not_terminal_alignment():
    import csv
    assert analysis.CANONICAL_SOURCE.name == 'episode_attribution_sensitivity.csv'
    with analysis.CANONICAL_SOURCE.open() as handle:
        fields = csv.DictReader(handle).fieldnames
    assert {'query_repaired', 'action_repaired'} <= set(fields)
