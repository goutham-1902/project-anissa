from datetime import date, datetime, timedelta
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from project.dispatch import DispatchGate, dispatch_id
from project.environment import ProjectEnvironment, RELEASE_ROOT
from project.telemetry_contract import IST


class DispatchGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.environment = ProjectEnvironment.external_instance(
            RELEASE_ROOT, self.root / "instance"
        )
        self.now = [datetime(2026, 9, 3, 8, 11, tzinfo=IST)]
        self.gate = DispatchGate(self.environment, now_provider=lambda: self.now[0])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_id_is_deterministic_and_slot_specific(self):
        on = date(2026, 9, 3)
        self.assertEqual(
            dispatch_id("weekday-morning", on),
            dispatch_id("weekday-morning", on),
        )
        self.assertNotEqual(
            dispatch_id("weekday-morning", on),
            dispatch_id("weekday-reminder", on),
        )

    def test_claim_is_leased_then_completed_exactly_once(self):
        first = self.gate.claim("weekday-morning", lease_seconds=600)
        self.assertEqual(first["action"], "acquired")
        self.assertIn("claim_token", first)

        busy = self.gate.claim("weekday-morning", lease_seconds=600)
        self.assertEqual(busy["action"], "busy")
        self.assertNotIn("claim_token", busy)

        completed = self.gate.complete(first["dispatch_id"], first["claim_token"])
        self.assertEqual(completed["action"], "completed")
        existing = self.gate.claim("weekday-morning", lease_seconds=600)
        self.assertEqual(existing["action"], "existing")
        self.assertFalse(self.environment.dispatch_slots_path.with_suffix(".json.tmp").exists())

    def test_expired_or_failed_claim_can_be_reacquired(self):
        first = self.gate.claim("weekday-close", lease_seconds=60)
        self.now[0] += timedelta(seconds=61)
        second = self.gate.claim("weekday-close", lease_seconds=60)
        self.assertEqual(second["action"], "acquired")
        self.assertNotEqual(first["claim_token"], second["claim_token"])

        failed = self.gate.fail(second["dispatch_id"], second["claim_token"])
        self.assertEqual(failed["action"], "failed")
        third = self.gate.claim("weekday-close", lease_seconds=60)
        self.assertEqual(third["action"], "acquired")
        payload = json.loads(self.environment.dispatch_slots_path.read_text())
        self.assertEqual(payload["slots"][third["dispatch_id"]]["attempt"], 3)

    def test_invalid_store_fails_closed(self):
        self.environment.dispatch_slots_path.parent.mkdir(parents=True)
        self.environment.dispatch_slots_path.write_text("not-json")
        with self.assertRaisesRegex(RuntimeError, "receipt store is invalid"):
            self.gate.claim("weekday-morning")


if __name__ == "__main__":
    unittest.main()
