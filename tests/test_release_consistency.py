import json
from pathlib import Path
import tempfile
import unittest

from project.governance import automation_contract_problems, validate_release_evidence, verification_identity


class ReleaseConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "mode": "LIVE", "go_live_authorized": True, "package_version": "1.0.0",
            "automation_bindings": {"WEEKDAY_OPS_DISPATCHER": "daily"},
            "chat_bindings": {"WEEKDAY_OPS": "operations"},
        }
        self.worker = {"mode": "LIVE", "project_version": "1.0.0",
                       "automation_id": "worker", "automation_status": "ACTIVE",
                       "thread_id": "telemetry"}
        self.tasks = {
            "daily": {"status": "ACTIVE", "target_thread_id": "operations"},
            "worker": {"status": "ACTIVE", "target_thread_id": "telemetry"},
        }

    def test_manual_or_setup_workers_cannot_have_active_schedules(self):
        for mode in ("SHADOW", "SETUP"):
            with self.subTest(mode=mode):
                worker = {**self.worker, "mode": mode}
                self.assertTrue(automation_contract_problems(self.settings, self.tasks, (worker,)))
        self.assertEqual(automation_contract_problems(self.settings, self.tasks, (self.worker,)), [])

    def test_authorization_task_target_version_and_missing_schedule_fail_closed(self):
        for settings, tasks, worker in (
            ({**self.settings, "go_live_authorized": False}, self.tasks, self.worker),
            (self.settings, {**self.tasks, "daily": {"status": "ACTIVE", "target_thread_id": "wrong"}}, self.worker),
            (self.settings, self.tasks, {**self.worker, "project_version": "0.9.0"}),
            (self.settings, {"daily": self.tasks["daily"]}, self.worker),
        ):
            with self.subTest(settings=settings, worker=worker):
                self.assertTrue(automation_contract_problems(settings, tasks, (worker,)))

    def test_source_identity_tracks_edits_but_ignores_campaign_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"version": "1.0.0"}))
            source = root / "example.py"
            source.write_text("VALUE = 1\n")
            before = verification_identity(root)
            (root / "runtime").mkdir()
            (root / "runtime/settings.json").write_text('{"status":"changed"}')
            self.assertEqual(before, verification_identity(root))
            source.write_text("VALUE = 2\n")
            self.assertNotEqual(before, verification_identity(root))

    def test_unregistered_project_task_is_not_mistaken_for_an_authorized_worker(self):
        settings = {**self.settings, "project_binding": {"path": "/synthetic/project"}}
        tasks = {**self.tasks, "unexpected": {
            "status": "ACTIVE", "target_thread_id": "unknown",
            "prompt": "Work in /synthetic/project",
        }}
        self.assertTrue(automation_contract_problems(settings, tasks, (self.worker,)))

    def test_changed_workflows_require_separate_revision_matched_acceptance(self):
        identity = {"version": "1.0.0", "source_revision": "current", "source_digest": "source"}
        record = {**identity, "status": "PASSED", "checks": [{"status": "PASSED"}]}
        for evidence in (None, {"status": "PASSED", "source_revision": "old", "evidence": "trial"}):
            with self.assertRaisesRegex(ValueError, "operational acceptance"):
                validate_release_evidence(record, identity, implementation_only=False, operational_evidence=evidence)
        accepted = {"status": "PASSED", "source_revision": "current", "evidence": "observed trial run"}
        self.assertEqual(validate_release_evidence(record, identity, implementation_only=False,
                                                 operational_evidence=accepted), accepted)
        self.assertEqual(validate_release_evidence(record, identity, implementation_only=True)["status"],
                         "UNCHANGED_RUNTIME")

    def test_stale_source_or_failed_tests_cannot_close_a_release(self):
        identity = {"version": "1.0.0", "source_revision": "current", "source_digest": "source"}
        good = {**identity, "status": "PASSED", "checks": [{"status": "PASSED"}]}
        for record in ({**good, "source_digest": "stale"},
                       {**good, "checks": [{"status": "FAILED"}]},
                       {**good, "checks": []}):
            with self.assertRaises(ValueError):
                validate_release_evidence(record, identity, implementation_only=True)


if __name__ == "__main__":
    unittest.main()
