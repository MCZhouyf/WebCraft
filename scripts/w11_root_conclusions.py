#!/usr/bin/env python3
"""Freeze source-level conclusions before population annotation sensitivity."""
from w11_common import *
import subprocess


def main():
    resources('root_conclusions')
    cases=json.loads((OUT/'source_cases_second_pass.json').read_text())
    actual=json.loads((OUT/'actual_input_provenance.json').read_text())
    tests=json.loads((OUT/'source_test_summary.json').read_text())
    assert len(cases)==len(actual)==len(tests)==9
    assert all(c['both_historical_selections_reproduced'] for c in cases)
    assert all(c['actual_fixture_equal'] for c in actual)
    assert all(c['same_matrix_ignoring_row_order'] and c['same_seed_repeat_equal'] for c in tests)
    sources=[]
    for rel in ('agent/planning.py','agent/core.py','agent/decision.py'):
        path=ARB/'src/arbiter'/rel
        historical=subprocess.check_output(['git','show',f'ff3bf4a:src/arbiter/{rel}'],cwd=ARB)
        package=ROOT/'release/package/method_code/src/arbiter'/rel
        sources.append({'source':'src/arbiter/'+rel,'historical_ff3bf4a_sha256':hashlib.sha256(historical).hexdigest(),
                        'working_sha256':sha(path),'W9_package_sha256':sha(package),
                        'all_bytes_equal':historical==path.read_bytes()==package.read_bytes()})
    assert all(s['all_bytes_equal'] for s in sources),'STOP: selection source version mismatch'
    write('selection_source_identity.csv',sources)
    origins=[]
    for path in (ARB/'artifacts/main/all_public/run_manifest.json',
                 ARB/'artifacts/post_run_probe_then_rag/all_public/run_manifest.json',
                 ROOT/'reports/paperA/w9_release_fix/replay/main_result.json'):
        raw=json.loads(path.read_text())
        origins.append({'source':str(path.relative_to(ROOT.parent)),
                        'contains_PYTHONHASHSEED': 'PYTHONHASHSEED' in raw,
                        'recorded_PYTHONHASHSEED':raw.get('PYTHONHASHSEED','not recorded'),
                        'sha256':sha(path)})
    write('hash_seed_provenance.csv',origins)
    causes=[]
    for case in cases:
        causes.append({'case':case['case'],**dict(zip(('reading_mode','method','condition','world','task_id'),case['key'])),
            'classification':1,'classification_text':'Established source-level unfixed tie-break',
            'location':'src/arbiter/agent/planning.py:170-173 and :186-195; candidates :125-140; core.py :163-185 and :193-218',
            'cause':'Unordered set of material-balance rows changes CBC alternative-optimum selection; no lexicographic tie objective. The first five support-excluded plans inherit this selection; Naive/Verify-All use plans[0], ARBITER VOI uses their first-step set.',
            'evidence':'Actual old/W9 captured inputs and operator order equal; numeric first-MIP matrix/objective equal modulo row order; changing only process hash seed reproduces BOTH observed selections with unchanged objective. Same-seed repeated pure tests agree.',
            'historical_limit':'Original process hash seed, original ordered MPS and solver trace were not retained; no claim to recover their exact values or to prove which low-level CBC pivot occurred. Fixed seed does not define a canonical optimum across runtime/build/row-layout changes.'})
    write('root_cause_cases.csv',causes)
    checks=[
        {'source':'equivalent LP optima/operators','answer':'yes','evidence':'Identical objective/matrix; exact competing first steps recovered from seeds 0/1/2/3; pla_010 two operators have equal resolved inputs, output, quantity, cost, probability but different input_options/catalogue identity.'},
        {'source':'dict insertion order','answer':'no (as observed input difference)','evidence':'Original/W9 compiled operator lists and captured weighted fixtures are identical in all 9 cases. _claims retains frozen claim-list insertion order; no unordered dictionary traversal independently demonstrated.'},
        {'source':'set iteration order','answer':'yes','evidence':'BeliefPlanner._solve_once builds items with set unions then for item in items. Four row-order fingerprints across four seeds, invariant numerical matrix, recovered choices.'},
        {'source':'filesystem enumeration order','answer':'no (on selection path)','evidence':'Exact evidence filenames and in-memory operator sequences; no listdir/iterdir/glob call in planner/core/decision selection functions; actual bundle/operator identity verified.'},
        {'source':'floating-point difference between the compared fixture inputs','answer':'no','evidence':'All captured weighted inputs equal; first-solve objective and numeric matrix fingerprints invariant; equal coefficients are represented as identical doubles. This is not a proof that CBC arithmetic is independent of row order.'},
        {'source':'missing full ordering key','answer':'yes','evidence':'Objective is only sum((action_cost-.35*log(p))*x); no operator-ID/lexicographic term. CBC count vector is used directly; plans[0]/np.argmax select enumeration-first on ties.'},
        {'source':'sources not controlled by PYTHONHASHSEED','answer':'yes (not established as additional historical cause)','evidence':'CBC native solver build, tolerances, random settings and wall-clock timeLimit are outside Python hash control. Source fixes threads=1/timeLimit=20 but not canonical optimal-solution selection; original MPS/build trace not archived. Pure repeated seed-0 tests were equal, not evidence of a second observed source.'}]
    write('ordering_source_checklist.csv',checks)
    (OUT/'root_cause_conclusions.json').write_text(json.dumps({'cases':9,'classification_1':9,'classification_2':0,'classification_3':0,
        'classification_scope':'source-level reproducible sufficient cause, not recovery of unlogged historical hash seed or CBC pivot',
        'new_world_actions':0,'repair_authorized_by_step0_classification':True},indent=2)+'\n')
    print('Source conclusions recorded: 9 source-level category-1 cases; historical hash/MPS caveat retained.')


if __name__=='__main__':main()
