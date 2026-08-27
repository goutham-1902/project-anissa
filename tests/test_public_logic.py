from datetime import datetime
import unittest

from logic.dedupe import equivalent_opportunity, equivalent_task
from logic.ids import opportunity_id, stable_id
from logic.runtime import resolve_effective_mode
from logic.task_state import validate_transition
from project.projections import CompletedTaskCreditProjection
from worker1.src.accounting import IST, build_worklog, split_forest_session
from worker1.src.sync import validate_extraction


class PublicLogicTests(unittest.TestCase):
    def test_ids_and_cross_source_deduplication_are_deterministic(self):
        self.assertEqual(
            stable_id("x", " Example  University "),
            stable_id("x", "example university"),
        )
        self.assertEqual(
            opportunity_id("Example University", "Role", "PhD", "https://feed.example/a"),
            opportunity_id("Example University", "Role", "PhD", "https://official.example/a"),
        )
        self.assertTrue(equivalent_opportunity(
            {"Institution": "Example University", "Route": "PhD", "Opportunity": "Scientific ML"},
            {"Institution": "Example University", "Route": "PhD", "Opportunity": "Scientific-ML"},
        ))
        self.assertTrue(equivalent_task(
            {"Application ID": "A1", "Campaign": "Degree", "Category": "Research", "Task Title": "Map two example research groups"},
            {"Application ID": "A1", "Campaign": "Degree", "Category": "Research", "Task Title": "Map 2 example research groups"},
        ))

    def test_completion_and_blocking_require_evidence(self):
        with self.assertRaises(ValueError):
            validate_transition("Done", evidence="", meaningful=True)
        with self.assertRaises(ValueError):
            validate_transition("Blocked", blocker="Unavailable", unblock_action="")

    def test_mode_requires_runtime_workbook_agreement(self):
        gate = resolve_effective_mode(
            {"mode": "LIVE", "go_live_authorized": True},
            {"setup_mode": "SETUP"},
        )
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["effective_mode"], "SETUP")

    def test_worker_accounting_splits_boundaries_and_deduplicates(self):
        session = {
            "id": "S1",
            "tag": "work",
            "start_at": "2026-08-25T19:30:00+05:30",
            "end_at": "2026-08-25T20:30:00+05:30",
        }
        self.assertEqual(
            [row["minutes"] for row in split_forest_session(session)],
            [30.0, 30.0],
        )
        credit = CompletedTaskCreditProjection(
            agenda_id="graduate_applications",
            task_id="T1",
            completed_at=datetime(2026, 8, 26, 18, 0, tzinfo=IST),
            minutes=45,
            basis="estimated_proxy",
        )
        self.assertEqual(len(build_worklog([session, session], [credit, credit])), 3)

    def test_incomplete_worker_input_cannot_manufacture_zero(self):
        with self.assertRaisesRegex(ValueError, "refusing to record zero"):
            validate_extraction({
                "schema_version": 1,
                "extraction_status": "FAILED",
                "range_start": "2026-08-25T00:00:00+05:30",
                "range_end": "2026-08-26T20:00:00+05:30",
                "sessions": [],
            })


if __name__ == "__main__":
    unittest.main()
