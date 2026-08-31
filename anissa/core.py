from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Callable

from anissa.agendas.graduate_applications import GraduateApplicationsAgenda
from anissa.portfolio import Portfolio
from logic.runtime import resolve_effective_mode
from logic.telemetry import telemetry_context
from logic.workbook_io import WorkbookGateway
from project.environment import ProjectEnvironment
from project.projections import AgendaProjection
from project.reporting_week import resolve_closed_reporting_week
from project.telemetry_contract import IST


PERMANENT_ROLE_IDS = ("COMMAND", "WEEKDAY_OPS", "WEEKEND")


@dataclass(frozen=True)
class Role:
    role_id: str
    name: str


class AnissaCore:
    """Coordinate stable roles and the active agenda through one small interface."""

    def __init__(
        self,
        environment: ProjectEnvironment,
        *,
        gateway: WorkbookGateway | None = None,
        today_provider: Callable[[], date] = date.today,
        telemetry_loader: Callable[..., dict] = telemetry_context,
    ):
        self.environment = environment
        self.portfolio = Portfolio.load(environment.portfolio_path)
        if self.portfolio.default_agenda_id != GraduateApplicationsAgenda.agenda_id:
            raise RuntimeError(
                f"Unsupported default agenda: {self.portfolio.default_agenda_id}"
            )
        role_payload = json.loads(
            environment.role_registry_path.read_text(encoding="utf-8")
        )
        if set(role_payload) != set(PERMANENT_ROLE_IDS):
            raise RuntimeError("Permanent Anissa role topology is invalid")
        self.roles = tuple(
            Role(role_id=role_id, name=str(role_payload[role_id]["name"]))
            for role_id in PERMANENT_ROLE_IDS
        )
        self._today = today_provider
        self._telemetry = telemetry_loader
        self.agenda = GraduateApplicationsAgenda(
            gateway or WorkbookGateway(environment=environment),
            environment,
            today_provider=today_provider,
        )

    def effective_gate(self, control: dict | None = None) -> dict:
        settings = json.loads(
            self.environment.runtime_settings_path.read_text(encoding="utf-8")
        )
        return resolve_effective_mode(settings, control or self.agenda.control())

    def projection(self, workflow: str) -> AgendaProjection:
        control = self.agenda.control()
        gate = self.effective_gate(control)
        if not gate["ok"] or gate["effective_mode"] != "LIVE":
            raise RuntimeError("Anissa Core cannot project campaign state outside effective LIVE mode")
        return self.agenda.projection(workflow, expected_control=control)

    def snapshot(
        self,
        workflow: str,
        *,
        week_ending: date | None = None,
        as_of: datetime | None = None,
    ) -> dict:
        """Compatibility view for existing role prompts and automations."""
        if week_ending is not None and workflow != "weekly-audit":
            raise ValueError("A week-ending target is valid only for weekly audits.")
        moment = as_of or datetime.now(IST)
        moment = (
            moment.replace(tzinfo=IST)
            if moment.tzinfo is None
            else moment.astimezone(IST)
        )
        audit_week = (
            resolve_closed_reporting_week(week_ending, as_of=moment)
            if workflow == "weekly-audit"
            else None
        )
        control = self.agenda.control()
        gate = self.effective_gate(control)
        base = {
            "workflow": workflow,
            "date": self._today().isoformat(),
            "live_gate": gate,
        }
        if not gate["ok"] or gate["effective_mode"] != "LIVE":
            return {**base, "blocked": True}
        projection = self.agenda.projection(
            workflow,
            expected_control=control,
            audit_week=audit_week,
        )
        result = {
            **base,
            "mode": gate["effective_mode"],
            **projection.compatibility_payload(),
        }
        needs_telemetry = (
            workflow not in {"weekday-reminder", "weekend-reminder"}
            or bool(result.get("reminder_due"))
        )
        if needs_telemetry:
            result["telemetry"] = (
                self._telemetry(workflow, reporting_week=audit_week, now=moment)
                if audit_week is not None
                else self._telemetry(workflow)
            )
        return result
