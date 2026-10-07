import unittest
from logic.ids import stable_id, norm_url, opportunity_id, workload_event_id
from logic.dedupe import equivalent_opportunity, equivalent_task
from logic.task_state import validate_transition
from logic.funding import FundingInput, FundingGrade, classify_funding
from logic.workload import discovery_ratios, surge_allowed, validate_workload_event, replan_preview
from logic.budget import fee_band_research_master, budget_state
from logic.runtime import resolve_effective_mode


class LogicTests(unittest.TestCase):
    def test_stable_id_normalization(self):
        self.assertEqual(stable_id('x',' ETH   Zurich ','Direct Doctorate'),stable_id('x','eth zurich','direct doctorate'))

    def test_url_tracking_removed(self):
        self.assertEqual(norm_url('https://x.org/a/?utm_source=z&k=1'),'https://x.org/a?k=1')

    def test_opportunity_id_ignores_discovery_url(self):
        a=opportunity_id('ETH Zurich','Direct Doctorate','Direct Doctorate','https://aggregator.example/job')
        b=opportunity_id('ETH Zurich','Direct Doctorate','Direct Doctorate','https://ethz.ch/official')
        self.assertEqual(a,b)

    def test_cross_source_opportunity_equivalence(self):
        a={'Institution':'Example University','Route':'PhD','Opportunity':'PhD in Scientific Machine Learning','Official Source':''}
        b={'Institution':'Example University','Route':'PhD','Opportunity':'PhD in Scientific Machine-Learning','Official Source':'https://example.edu/phd'}
        self.assertTrue(equivalent_opportunity(a,b))

    def test_generic_official_url_does_not_merge_distinct_opportunities(self):
        a={'Institution':'Example University','Route':'PhD','Opportunity':'PhD in Biology','Official Source':'https://example.edu/jobs'}
        b={'Institution':'Different Institute','Route':'Internship','Opportunity':'Materials internship','Official Source':'https://example.edu/jobs'}
        self.assertFalse(equivalent_opportunity(a,b))

    def test_task_wording_equivalence(self):
        a={'Application ID':'APP1','Campaign':'Long-term','Category':'Research','Task Title':'Map two ETH research groups'}
        b={'Application ID':'APP1','Campaign':'Long-term','Category':'Research','Task Title':'Map 2 ETH research groups'}
        self.assertTrue(equivalent_task(a,b))

    def test_done_requires_evidence(self):
        with self.assertRaises(ValueError): validate_transition('Done',evidence='',meaningful=True)
        validate_transition('Done',evidence='Portal account created',meaningful=True)

    def test_blocked_requires_plan(self):
        with self.assertRaises(ValueError): validate_transition('Blocked',blocker='Portal down',unblock_action='')

    def test_funding(self):
        a=classify_funding(FundingInput(True,True,True,False,5000))
        b=classify_funding(FundingInput(True,True,True,False,0))
        reject=classify_funding(FundingInput(True,True,True,True,10000))
        self.assertEqual(a,FundingGrade.A)
        self.assertEqual(b,FundingGrade.B)
        self.assertEqual(reject,FundingGrade.REJECT)

    def test_india_switch(self):
        self.assertEqual(discovery_ratios('SECURED'),(1.0,0.0))
        self.assertEqual(discovery_ratios('SEARCHING'),(0.6,0.4))

    def test_surge(self):
        self.assertTrue(surge_allowed(meaningful_deadline=True,high_value=True,materially_lowers_risk=True))
        self.assertFalse(surge_allowed(meaningful_deadline=True,high_value=False,materially_lowers_risk=True))

    def test_user_initiated_surge_requires_authorization(self):
        event={'Event Type':'USER_INITIATED_SURGE','Title':'External deadline','Estimated Minutes':120}
        with self.assertRaises(ValueError):
            validate_workload_event(event)
        validate_workload_event({**event,'User Authorized':'YES'})

    def test_workload_event_id_is_deterministic(self):
        a=workload_event_id('USER_INITIATED_SURGE',' RMIT  work ','2026-08-21','2026-08-22')
        b=workload_event_id('user_initiated_surge','rmit work','2026-08-21','2026-08-22')
        self.assertEqual(a,b)

    def test_replan_flags_started_task_date_change(self):
        tasks=[{'Task ID':'T1','Status':'Started','Estimated Minutes':90,'Due Date':'2026-08-22'}]
        event={'Event Type':'USER_INITIATED_SURGE','Title':'External deadline','Estimated Minutes':120,'User Authorized':'YES'}
        preview=replan_preview(tasks,event,[{'Task ID':'T1','Due Date':'2026-08-23'}])
        self.assertTrue(preview['requires_consequential_approval'])
        self.assertEqual(preview['consequential_task_ids'],['T1'])

    def test_budget(self):
        self.assertEqual(fee_band_research_master(5000),'NORMAL')
        self.assertEqual(fee_band_research_master(7500),'ASK')
        self.assertEqual(fee_band_research_master(12000),'REJECT_UNLESS_PROMOTED')
        self.assertEqual(budget_state(50000,10000),'ABOVE_SOFT_LIMIT')
        self.assertEqual(budget_state(50000,10001),'HARD_LIMIT_EXCEEDED')

    def test_effective_mode_requires_runtime_workbook_agreement(self):
        live=resolve_effective_mode({'mode':'LIVE','go_live_authorized':True},{'setup_mode':'LIVE'})
        mismatch=resolve_effective_mode({'mode':'LIVE','go_live_authorized':True},{'setup_mode':'SETUP'})
        self.assertEqual(live['effective_mode'],'LIVE')
        self.assertTrue(live['ok'])
        self.assertEqual(mismatch['effective_mode'],'SETUP')
        self.assertFalse(mismatch['ok'])


if __name__=='__main__': unittest.main()
