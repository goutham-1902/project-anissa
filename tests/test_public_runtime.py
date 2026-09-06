from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from openpyxl import load_workbook

from anissa.core import AnissaCore
from logic.workbook_io import WorkbookGateway
from project.environment import RELEASE_ROOT, resolve_environment
from project.dispatch import DispatchGate
from project.discovery_brief import validate_discovery_brief
from project.discovery_contract import DiscoveryContractError, validate_discovery_publication
from project.governance import Governance
from project.telemetry_contract import read_publication
from soldiers.thula import cli as thula_cli
from soldiers.thula.src import accounting as thula_accounting
from soldiers.thula.src.dashboard_server import dashboard_build_id, dashboard_health
from soldiers.lucan.prompt_adapter import build_prompt


class PublicRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = resolve_environment(RELEASE_ROOT)

    def test_synthetic_instance_is_setup_and_schema_complete(self):
        settings = json.loads(self.environment.runtime_settings_path.read_text())
        self.assertEqual(settings["mode"], "SETUP")
        self.assertFalse(settings["go_live_authorized"])
        workbook = load_workbook(self.environment.brain_path, read_only=True)
        schema = json.loads(self.environment.schema_path.read_text())
        self.assertEqual(set(workbook.sheetnames), set(schema["sheets"]))
        workbook.close()

    def test_core_is_blocked_before_go_live_and_mutation_fails_closed(self):
        gateway = WorkbookGateway(environment=self.environment)
        before = sha256(self.environment.brain_path.read_bytes()).digest()
        snapshot = AnissaCore(self.environment, gateway=gateway).snapshot("status")
        self.assertTrue(snapshot["blocked"])
        with self.assertRaisesRegex(RuntimeError, "mutation blocked"):
            gateway.set_control("probe", "blocked")
        self.assertEqual(before, sha256(self.environment.brain_path.read_bytes()).digest())

    def test_empty_telemetry_is_checksum_coherent_not_false_activity(self):
        publication = read_publication(
            self.environment.worker("thula").publication_root / "worklog.csv",
            self.environment.worker("thula").publication_root / "status.json",
        )
        self.assertEqual(publication.rows, [])
        self.assertEqual(publication.status["state"], "SETUP")

    def test_dispatch_receipts_are_private_and_do_not_mutate_campaign_state(self):
        gateway = WorkbookGateway(environment=self.environment)
        before = sha256(self.environment.brain_path.read_bytes()).digest()
        claim = DispatchGate(self.environment).claim("weekday-morning")
        self.assertIn(claim["action"], {"acquired", "busy", "existing"})
        self.assertTrue(self.environment.dispatch_slots_path.is_relative_to(
            self.environment.instance_root
        ))
        self.assertEqual(before, sha256(self.environment.brain_path.read_bytes()).digest())

    def test_worker_source_cannot_import_campaign_gateway(self):
        sources = tuple(
            path for path in (RELEASE_ROOT / "soldiers").glob("*/src")
            if path.is_dir()
        )
        text = "\n".join(
            path.read_text()
            for source in sources
            for path in source.glob("*.py")
        )
        self.assertNotIn("WorkbookGateway", text)
        self.assertNotIn("anissa.agendas", text)

    def test_worker_interfaces_include_live_capability_and_shadow_discovery(self):
        self.assertTrue(callable(thula_cli.build_parser))
        self.assertTrue(callable(thula_cli.main))
        self.assertTrue(callable(thula_accounting.build_worklog))
        lucan = self.environment.worker("lucan")
        settings = json.loads(lucan.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["mode"], "SETUP")
        self.assertEqual(settings["publication"]["state"], "SHADOW")
        self.assertIsNone(settings["thread_id"])
        self.assertIsNone(settings["automation_id"])
        self.assertFalse((RELEASE_ROOT / "worker1").exists())

    def test_lucan_publication_cannot_smuggle_campaign_decisions(self):
        payload = {
            "schema_version": 1,
            "worker_id": "lucan",
            "agenda_id": "graduate_applications",
            "assignment_id": "public-test",
            "run_kind": "SCHEDULED_SWEEP",
            "status": "COMPLETE",
            "started_at": "2026-09-06T10:00:00+05:30",
            "completed_at": "2026-09-06T10:01:00+05:30",
            "candidates": [],
            "source_failures": [],
            "recommendation": "Apply",
        }
        with self.assertRaisesRegex(DiscoveryContractError, "unexpected recommendation"):
            validate_discovery_publication(payload)

    def test_lucan_shadow_adapter_is_offline_and_bounded(self):
        brief = {
            "schema_version": 1,
            "worker_id": "lucan",
            "agenda_id": "synthetic_campaign",
            "assignment_id": "public_shadow_test",
            "run_kind": "SCHEDULED_SWEEP",
            "created_at": "2026-09-06T18:00:00+05:30",
            "search_since": None,
            "objective": "Find new funded scientific research opportunities.",
            "max_candidates": 4,
            "profile_facts": ["Applicant has a relevant bachelor's degree."],
            "hard_rules": ["Do not infer missing funding or eligibility."],
            "tracks": [{
                "track_id": "RESEARCH_DEGREES",
                "allocation": 1.0,
                "objective": "Find funded research degrees.",
                "directives": ["Prefer primary institutional sources."],
                "source_priorities": ["Official programme pages"],
            }],
            "known_candidates": [],
        }
        validated = validate_discovery_brief(brief)
        settings = json.loads(
            self.environment.worker("lucan").settings_path.read_text(encoding="utf-8")
        )
        package = build_prompt(brief, settings)
        self.assertEqual(validated.assignment_id, package.assignment_id)
        self.assertLess(package.approximate_input_tokens, 4500)
        self.assertFalse((RELEASE_ROOT / "soldiers" / "lucan" / "cli.py").exists())

    def test_dashboard_includes_bounded_weekly_history_without_private_assets(self):
        dashboard = RELEASE_ROOT / "soldiers" / "thula" / "dashboard"
        index = (dashboard / "index.html").read_text()
        app = (dashboard / "app.js").read_text()
        self.assertIn('id="weeklyHistory"', index)
        self.assertIn("weekly_history", app)
        self.assertNotIn("anissa-verdict.png", index)

    def test_dashboard_health_identifies_the_current_release_build(self):
        build_id = dashboard_build_id(RELEASE_ROOT)
        health = dashboard_health(build_id)
        self.assertEqual(health["service"], "thula-dashboard")
        self.assertEqual(health["build_id"], build_id)
        self.assertEqual(len(build_id), 16)

    def test_private_instance_and_clean_presentation_are_separate(self):
        self.assertFalse((RELEASE_ROOT / "brain").exists())
        self.assertFalse((RELEASE_ROOT / "profile").exists())
        self.assertFalse((RELEASE_ROOT / "persona").exists())
        self.assertTrue(self.environment.public_persona_root.is_dir())
        self.assertTrue(self.environment.instance_root.is_relative_to(
            Path(os.environ["PROJECT_ANISSA_INSTANCE"]).resolve()
        ))

    def test_maintainer_scope_defers_before_execution(self):
        governance = Governance(self.environment)
        governance.initialize_ledgers("2.5.0-dev.8")
        decision = governance.evaluate_scope(
            "SOLDIERS_MAINTAINER",
            ["soldiers/thula/src/sync.py", "project/projections.py"],
        )
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["defer_to"], "GENERAL")

        route = governance.publication_route(
            "SOLDIERS_MAINTAINER", ["soldiers/thula/src/sync.py"]
        )
        self.assertTrue(route["accepted"])
        self.assertEqual(route["publisher"], "GENERAL")
        self.assertEqual(route["action"], "HANDOFF_VERIFIED_CHANGE_TO_GENERAL")


if __name__ == "__main__":
    unittest.main()
