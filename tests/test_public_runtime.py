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
from project.governance import Governance
from project.telemetry_contract import read_publication
from soldiers.thula import cli as thula_cli
from soldiers.thula.src import accounting as thula_accounting
from soldiers.thula.src.dashboard_server import dashboard_build_id, dashboard_health


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

    def test_portfolio_metadata_is_lazy_and_agenda_paths_are_explicit(self):
        agenda_paths = self.environment.agenda("graduate_applications")
        self.assertEqual(agenda_paths.brain_path, self.environment.brain_path)
        self.assertEqual(agenda_paths.profile_root, self.environment.profile_root)
        projection = AnissaCore(self.environment).portfolio_projection()
        self.assertEqual(projection.default_agenda_id, "graduate_applications")
        self.assertEqual(projection.selected_agenda_id, "graduate_applications")
        self.assertEqual(len(projection.agendas), 1)

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

    def test_worker_interface_includes_live_telemetry_capability(self):
        self.assertTrue(callable(thula_cli.build_parser))
        self.assertTrue(callable(thula_cli.main))
        self.assertTrue(callable(thula_accounting.build_worklog))
        self.assertFalse((RELEASE_ROOT / "worker1").exists())

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
        general_kreg = governance.roles["SOLDIERS_MAINTAINER"]
        self.assertEqual(general_kreg["name"], "General Kreg")
        self.assertEqual(general_kreg["ledger"], "soldiers_maintainer.json")
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
        coordination = governance.policy["coordination"]
        self.assertFalse(coordination["silence_is_acceptance"])
        self.assertTrue(coordination["single_writer_per_file_scope"])


if __name__ == "__main__":
    unittest.main()
