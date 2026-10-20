import unittest
from .episode import episode_metrics, aggregate

WORLDS=('1.16.5','1.19','1.21.11')
TRUE=dict.fromkeys(WORLDS,True)
OLD=dict(zip(WORLDS,[True,True,False]))

def event(method,cost=1,consumed=(),produced=(),params=None,intent=None):
    row={'schema_version':2,'method':method,'params':params or {},'cost':{'primary':cost},'item_flow':{'consumed':[{'name':n,'count':c} for n,c in consumed],'produced':[{'name':n,'count':c} for n,c in produced]}}
    if intent is not None: row['intent']=intent
    return row

def score(events,vectors,**kwargs):
    return episode_metrics(events,goal={'item':'crafting_table','count':1},world=WORLDS[-1],true_feasible=True,success=True,reported_feasible=True,version_hashes={'correct':WORLDS[-1],'wrong':WORLDS[0]},mechanism_vectors=vectors,**kwargs)

class MetricsTests(unittest.TestCase):
    def test_oracle_flow_and_no_stale(self):
        events=[event('mine',produced=[('oak_log',1)]),event('craft_grid',consumed=[('oak_log',1)],produced=[('oak_planks',4)]),event('craft_grid',consumed=[('oak_planks',4)],produced=[('crafting_table',1)])]
        row=score(events,{i:TRUE for i in range(3)})
        self.assertEqual(row['HTR'],0); self.assertEqual(row['OVR'],0)
        self.assertEqual(row['contributing_events'],[0,1,2])
        self.assertEqual(aggregate([row])['feasibility_F1'],1)

    def test_old_recipe_stale(self):
        row=score([event('craft_grid')],{0:OLD})
        self.assertEqual(row['HTR'],1); self.assertEqual(row['HTR_all'],1)
        self.assertEqual(row['HTR_commit'],1)

    def test_all_actions_marked_probe_do_not_hide_cost_or_htr_all(self):
        events=[event('mine',2,intent='probe'),event('craft_grid',3,intent='probe')]
        row=score(events,{0:OLD,1:OLD})
        self.assertEqual(row['HTR_all'],1)
        self.assertEqual(row['HTR'],1)
        self.assertEqual(row['HTR_commit'],0)
        self.assertEqual(row['probe_count'],2)
        self.assertEqual(row['probe_cost'],row['primary_cost'])
        summary=aggregate([row])
        self.assertEqual(summary['HTR_all'],1)
        self.assertEqual(summary['HTR_commit'],0)
        self.assertEqual(summary['probe_count'],2)
        self.assertEqual(summary['probe_cost'],5)

    def test_only_exact_probe_intent_is_excluded_from_commit_htr(self):
        row=score([event('craft_grid',intent='explore')],{0:OLD})
        self.assertEqual(row['HTR_commit'],1)
        self.assertEqual(row['probe_count'],0)

    def test_exhaustive_invariant_checks(self):
        row=score([event('check_name'),event('check_name'),event('craft_grid',produced=[('crafting_table',1)])],{i:TRUE for i in range(3)})
        self.assertAlmostEqual(row['OVR'],2/3)

    def test_identification_after_last_wrong(self):
        row=score([event('mine',2),event('report_version',0,params={'guess_sha256':'correct'}),event('mine',3),event('report_version',0,params={'guess_sha256':'wrong'}),event('mine',4),event('report_version',0,params={'guess_sha256':'correct'})],{})
        self.assertEqual(row['IC'],9)

    def test_no_report_is_NA(self):
        self.assertEqual(score([], {})['IC_status'],'NA_no_report')

    def test_unknown_counts_wrong(self):
        row=score([],{},claim_answers=[True,'unknown',False],claim_truth=[True,True,False])
        self.assertAlmostEqual(row['IAA'],2/3); self.assertAlmostEqual(row['IAA_unknown_rate'],1/3)

    def test_efficiency_and_stale_overlap(self):
        row=score([event('craft_grid',4)],{0:OLD},task_type='efficiency',oracle_cost=2)
        self.assertEqual(row['OPR'],1); self.assertEqual(row['OPR_with_stale'],1)

    def test_unclassified_not_silently_zero(self):
        self.assertIsNone(score([event('craft_grid')],{})['OVR'])

    def test_legacy_logs_rejected(self):
        with self.assertRaises(ValueError): score([{'method':'craft_grid','cost':{}}],{})

    def test_leftovers_not_all_credited_to_goal(self):
        events=[event('craft_grid',produced=[('stick',4)]),event('craft_grid',produced=[('crafting_table',1)])]
        row=score(events,{0:TRUE,1:TRUE})
        self.assertEqual(row['OVR'],0.5)

    def test_initial_inventory_has_no_action_producer(self):
        events=[event('craft_grid',produced=[('oak_planks',4)]),event('craft_grid',consumed=[('oak_planks',4)],produced=[('crafting_table',1)])]
        row=score(events,{0:TRUE,1:TRUE},initial_inventory={'oak_planks':4})
        self.assertEqual(row['contributing_events'],[1])
        self.assertEqual(row['OVR'],0.5)

if __name__=='__main__': unittest.main()
