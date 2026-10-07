from datetime import date, datetime, timedelta
import contextlib
import io
import json
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from project.dispatch import DispatchGate
from project.environment import ProjectEnvironment, RELEASE_ROOT
from project.recovery import latest_audit_due_week_end, plan_recovery
from project.telemetry_contract import IST


def moment(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=IST)


def receipt(slot, day, **fields):
    return {"slot": slot, "run_date": day, **fields}


class RecoveryPlanTests(unittest.TestCase):
    def plan(self, role, day="2026-10-06", clock="10:00", slots=None, **state):
        return plan_recovery(role, now=moment(day, clock), receipts={"slots": slots or {}}, state=state)

    def test_tuesday_return_selects_current_allocation_and_latest_audit_only(self):
        daily = self.plan("WEEKDAY_OPS")
        weekly = self.plan("WEEKEND")
        self.assertEqual([(r["slot"], r["run_date"]) for r in daily["actions"]], [("weekday-morning", "2026-10-06")])
        self.assertEqual([(r["slot"], r["run_date"]) for r in weekly["actions"]], [("weekly-audit", "2026-10-04")])
        self.assertEqual(weekly["message_limit"], 1)

    def test_sunday_audit_does_not_close_before_2130(self):
        self.assertEqual(latest_audit_due_week_end(moment("2026-10-04", "21:29")), date(2026, 9, 27))
        self.assertEqual(latest_audit_due_week_end(moment("2026-10-04", "21:30")), date(2026, 10, 4))

    def test_saturday_coalesces_audit_and_opening_not_reminder(self):
        plan = self.plan("WEEKEND", "2026-10-03", "18:00")
        self.assertEqual([r["workflow"] for r in plan["actions"]], ["weekly-audit", "weekend-morning"])

    def test_sunday_never_allocates_new_tasks(self):
        plan = self.plan("WEEKEND", "2026-10-04", "16:00")
        self.assertEqual([r["slot"] for r in plan["actions"]], ["weekly-audit", "weekend-reminder-sun-15"])
        self.assertTrue(all(not r["allow_new_tasks"] for r in plan["actions"]))

    def test_latest_ingestion_only(self):
        self.assertEqual(self.plan("THULA", clock="20:59")["actions"][0]["run_date"], "2026-10-05")
        self.assertEqual(self.plan("THULA", clock="21:00")["actions"][0]["run_date"], "2026-10-06")

    def test_late_allocation_respects_remaining_day(self):
        row = self.plan("WEEKDAY_OPS", clock="21:20")["actions"][0]
        self.assertEqual(row["remaining_window_minutes"], 10)
        self.assertFalse(row["allow_new_tasks"])

    def test_late_opening_suppresses_already_elapsed_reminder(self):
        slots = {"opening": receipt("weekday-morning", "2026-10-06", status="completed", completed_at=moment("2026-10-06", "16:00").isoformat(), delivery_status="delivered")}
        self.assertEqual(self.plan("WEEKDAY_OPS", clock="16:01", slots=slots)["actions"], [])

    def test_pending_delivery_retries_without_work_and_supersedes_old_only(self):
        slots = {
            "current": receipt("weekday-morning", "2026-10-06", status="completed", delivery_status="pending"),
            "old": receipt("weekday-close", "2026-10-05", status="failed"),
            "future": receipt("weekday-close", "2026-10-06", status="failed"),
        }
        plan = self.plan("WEEKDAY_OPS", slots=slots)
        self.assertEqual(plan["actions"][0]["action"], "delivery")
        self.assertFalse(plan["actions"][0]["allow_new_tasks"])
        self.assertEqual(plan["superseded_ids"], ["old"])

    def test_recorded_audit_silent_unless_report_pending(self):
        self.assertEqual(self.plan("WEEKEND", audit_recorded=True)["actions"], [])
        slots = {"audit": receipt("weekly-audit", "2026-10-04", status="completed", delivery_status="pending")}
        self.assertEqual(self.plan("WEEKEND", slots=slots, audit_recorded=True)["actions"][0]["action"], "delivery")

    def test_live_lease_and_mode_fail_closed(self):
        slots = {"opening": receipt("weekday-morning", "2026-10-06", status="running", lease_expires_at=moment("2026-10-06", "11:00").isoformat())}
        self.assertTrue(self.plan("WEEKDAY_OPS", slots=slots)["busy"])
        self.assertEqual(self.plan("WEEKDAY_OPS", blocked=True)["actions"], [])


class RecoveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = ProjectEnvironment.external_instance(RELEASE_ROOT, Path(self.temp.name) / "instance")
        self.now = moment("2026-10-06", "10:00")
        self.gate = DispatchGate(self.environment, now_provider=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_delivery_retry_preserves_durable_work(self):
        claim = self.gate.claim("weekday-morning")
        self.gate.complete(claim["dispatch_id"], claim["claim_token"], message_expected=True)
        self.assertEqual(self.gate.claim("weekday-morning")["action"], "busy")
        self.assertTrue(plan_recovery("WEEKDAY_OPS", now=self.now, receipts=self.gate.state())["busy"])
        self.now += timedelta(minutes=3)
        retry = self.gate.claim("weekday-morning")
        self.assertEqual(retry["action"], "delivery")
        with self.assertRaises(PermissionError):
            self.gate.deliver(retry["dispatch_id"], claim["claim_token"])
        self.gate.deliver(retry["dispatch_id"], retry["claim_token"])
        self.assertEqual(self.gate.claim("weekday-morning")["action"], "existing")
        self.assertEqual(self.gate.state()["slots"][retry["dispatch_id"]]["attempt"], 1)

    def test_coverage_survives_retention_pruning(self):
        claim = self.gate.claim("weekday-close")
        self.gate.complete(claim["dispatch_id"], claim["claim_token"], message_expected=True)
        self.gate.deliver(claim["dispatch_id"], claim["claim_token"])
        covered = self.now.isoformat(timespec="seconds")
        self.now += timedelta(days=60)
        self.gate.claim("weekday-close")
        plan = plan_recovery("WEEKDAY_OPS", now=self.now, receipts=self.gate.state())
        self.assertEqual(plan["since"], covered)
        self.assertNotIn(claim["dispatch_id"], self.gate.state()["slots"])

    def test_supersede_cannot_retire_live_delivery(self):
        claim = self.gate.claim("weekly-audit", on=date(2026, 10, 4))
        self.gate.complete(claim["dispatch_id"], claim["claim_token"], message_expected=True)
        self.assertEqual(self.gate.supersede([claim["dispatch_id"]])["superseded"], [])
        self.now += timedelta(minutes=3)
        self.assertEqual(self.gate.supersede([claim["dispatch_id"]])["superseded"], [claim["dispatch_id"]])
        self.assertEqual(self.gate.claim("weekly-audit", on=date(2026, 10, 4))["action"], "existing")

    def test_failed_work_retries_and_concurrent_role_work_is_blocked(self):
        claim = self.gate.claim("weekday-morning")
        self.assertEqual(self.gate.claim("weekday-close")["action"], "busy")
        self.gate.fail(claim["dispatch_id"], claim["claim_token"])
        self.assertEqual(self.gate.claim("weekday-morning")["action"], "acquired")

    def test_earth_can_finish_two_actions_before_one_coalesced_delivery(self):
        self.now = moment("2026-10-03", "10:00")
        plan = plan_recovery("WEEKEND", now=self.now, receipts=self.gate.state())
        claims = []
        for action in plan["actions"]:
            claim = self.gate.claim(action["slot"], on=date.fromisoformat(action["run_date"]))
            self.assertEqual(claim["action"], "acquired")
            self.gate.complete(claim["dispatch_id"], claim["claim_token"], message_expected=True)
            claims.append(claim)
        self.assertEqual(len(claims), 2)
        for claim in claims:
            self.gate.deliver(claim["dispatch_id"], claim["claim_token"])
        self.assertEqual(plan_recovery("WEEKEND", now=self.now, receipts=self.gate.state(), state={"audit_recorded": True})["actions"], [])

    def test_technical_cli_does_not_import_workbook(self):
        from project.instance import initialize_instance
        initialize_instance(RELEASE_ROOT, self.environment.instance_root)
        script = "import sys; from tools import anissa_cli as c; c.main(['claim-dispatch','thula-ingestion']); assert 'logic.workbook_io' not in sys.modules"
        import os
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=RELEASE_ROOT,
                                env={**os.environ, "PROJECT_ANISSA_INSTANCE": str(self.environment.instance_root)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_command_routes_bounded_plans_without_worker_data(self):
        from tools import anissa_cli as cli
        self.environment.runtime_settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.environment.runtime_settings_path.write_text(json.dumps({"chat_bindings": {"WEEKDAY_OPS": "ops", "WEEKEND": "earth"}, "worker_chat_bindings": {"THULA": "worker"}}))
        class Core:
            def open_agenda(self, *args, **kwargs):
                return None
            def recovery_state(self, *args, **kwargs):
                return {"audit_recorded": False}
            def effective_gate(self, **kwargs):
                return {"effective_mode": "LIVE"}
        output = io.StringIO()
        with patch.object(cli, "ENVIRONMENT", self.environment), patch.object(cli, "_new_gateway"), patch.object(cli, "_core", return_value=Core()), contextlib.redirect_stdout(output):
            cli.main(["recovery-plan", "--role", "COMMAND", "--as-of", "2026-10-06T10:00:00+05:30"])
        payload = json.loads(output.getvalue())
        self.assertEqual([row["target_thread_id"] for row in payload["recoveries"]], ["ops", "earth"])
        self.assertEqual(payload["worker_recovery"], {"target_thread_id": "worker", "role": "THULA"})

    def test_real_cli_audit_delivery_retry_does_not_repeat_workbook_write(self):
        import os
        from logic.workbook_io import WorkbookGateway
        from project.instance import initialize_instance
        initialize_instance(RELEASE_ROOT, self.environment.instance_root)
        settings_path = self.environment.runtime_settings_path
        settings = json.loads(settings_path.read_text())
        settings_path.write_text(json.dumps({**settings, "mode": "LIVE", "go_live_authorized": True}))
        gateway = WorkbookGateway(environment=self.environment, allow_offline_migration=True)
        gateway.set_controls({"setup_mode": "LIVE"})
        env = {**os.environ, "PROJECT_ANISSA_INSTANCE": str(self.environment.instance_root)}
        def cli(*args):
            result = subprocess.run([sys.executable, "-B", "tools/anissa_cli.py", *args], cwd=RELEASE_ROOT,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        plan = cli("recovery-plan", "--role", "WEEKEND")
        action = next(row for row in plan["actions"] if row["workflow"] == "weekly-audit")
        claim = cli("claim-dispatch", action["slot"], "--date", action["run_date"])
        audit = cli("record-weekly-audit", "--week-ending", action["run_date"],
                    "--exact-next-action", "Review fixture progress", "--summary", "Acceptance fixture verdict")
        self.assertEqual(audit["action"], "insert")
        cli("complete-dispatch", claim["dispatch_id"], claim["claim_token"], "--message-expected")
        saved = sha256(self.environment.brain_path.read_bytes()).hexdigest()
        later = datetime.now(IST) + timedelta(minutes=3)
        report = cli("recovery-plan", "--role", "WEEKEND", "--as-of", later.isoformat())
        self.assertEqual(report["actions"][0]["action"], "delivery")
        self.assertEqual(report["context"]["recorded_audit"]["summary"], "Acceptance fixture verdict")
        retry_gate = DispatchGate(self.environment, now_provider=lambda: later)
        retry = retry_gate.claim(action["slot"], on=date.fromisoformat(action["run_date"]))
        self.assertEqual(retry["action"], "delivery")
        cli("deliver-dispatch", retry["dispatch_id"], retry["claim_token"])
        repeated = cli("recovery-plan", "--role", "WEEKEND", "--as-of", later.isoformat())
        self.assertFalse(any(row["workflow"] == "weekly-audit" for row in repeated["actions"]))
        self.assertEqual(sha256(self.environment.brain_path.read_bytes()).hexdigest(), saved)
        self.assertEqual(len(gateway.rows("WEEKLY_AUDITS")), 1)
        self.assertEqual(gateway.rows("TASKS"), [])


if __name__ == "__main__":
    unittest.main()
