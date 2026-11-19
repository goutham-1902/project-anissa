from datetime import date
import json
import unittest

from logic.working_links import task_working_link, validate_task_link_write


class WorkingLinkTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 19)
        self.row = {
            "Working Link URL": "https://example.edu/apply",
            "Working Link Verified At": "2026-11-18",
            "Working Link Verification Evidence": json.dumps({
                "authority": "Official university admissions source",
                "relevance": "Current cycle and the assigned application task",
                "entrypoint": "Admissions page checked; expected login reachable",
            }),
        }

    def test_only_fresh_attested_urls_are_exposed(self):
        self.assertEqual(task_working_link(self.row, as_of=self.today)["url"], self.row["Working Link URL"])
        for changes in ({"Working Link Verified At": "2026-11-01"},
                        {"Working Link Verified At": "2026-11-20"},
                        {"Working Link Verification Evidence": "HTTP 200"},
                        {"Working Link URL": "https://example.edu/apply?session=private"},
                        {"Working Link URL": "https://127.0.0.1/apply"}):
            with self.subTest(changes=changes):
                link = task_working_link({**self.row, **changes}, as_of=self.today)
                self.assertIsNone(link["url"])
                self.assertIn("Verified link missing", link["blocker"])
                self.assertTrue(link["unblock_action"])

    def test_missing_links_need_recovery_action_on_write_and_legacy_is_unchanged(self):
        with self.assertRaises(ValueError):
            validate_task_link_write({}, as_of=self.today, required=True)
        row = {"Working Link Unblock Action": "Verify the current official entrypoint."}
        validate_task_link_write(row, as_of=self.today, required=True)
        self.assertIsNone(task_working_link({}, as_of=self.today, required=False))


if __name__ == "__main__":
    unittest.main()
