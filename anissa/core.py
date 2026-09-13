from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Callable

from anissa.agenda_catalog import AgendaCatalog, AgendaRuntime
from anissa.portfolio import Portfolio
from logic.runtime import resolve_effective_mode
from logic.telemetry import telemetry_context
from logic.workbook_io import WorkbookGateway
from project.environment import ProjectEnvironment
from project.projections import (
    AgendaProjection,
    AgendaSummaryProjection,
    PortfolioProjection,
)
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
        catalog: AgendaCatalog | None = None,
    ):
        self.environment = environment
        self.portfolio = (
            catalog.portfolio
            if catalog is not None
            else Portfolio.load(environment.portfolio_path)
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
        self.catalog = catalog or AgendaCatalog(
            self.portfolio,
            environment,
            gateway=gateway,
            today_provider=today_provider,
        )

    @property
    def agenda(self) -> AgendaRuntime:
        """Temporary compatibility alias for the default agenda runtime."""

        return self.open_agenda()

    def open_agenda(
        self,
        agenda_id: str | None = None,
        *,
        for_mutation: bool = False,
    ) -> AgendaRuntime:
        return self.catalog.open(agenda_id, for_mutation=for_mutation)

    def portfolio_projection(
        self,
        agenda_id: str | None = None,
    ) -> PortfolioProjection:
        """Project bounded portfolio metadata without opening any agenda runtime."""

        selected = self.portfolio.registration(agenda_id)
        summaries = tuple(
            AgendaSummaryProjection(
                agenda_id=row.agenda_id,
                name=row.name,
                lifecycle=row.lifecycle,
                allocation_weight=row.allocation_weight,
                selected=row.agenda_id == selected.agenda_id,
            )
            for row in self.portfolio.agendas
        )
        return PortfolioProjection(
            default_agenda_id=self.portfolio.default_agenda_id,
            selected_agenda_id=selected.agenda_id,
            active_allocation_weight=sum(
                row.allocation_weight
                for row in self.portfolio.agendas
                if row.lifecycle == "ACTIVE"
            ),
            agendas=summaries,
        )

    def effective_gate(
        self,
        control: dict | None = None,
        *,
        agenda_id: str | None = None,
    ) -> dict:
        agenda = self.open_agenda(agenda_id)
        settings = json.loads(
            self.environment.runtime_settings_path.read_text(encoding="utf-8")
        )
        return resolve_effective_mode(settings, control or agenda.control())

    def projection(
        self,
        workflow: str,
        agenda_id: str | None = None,
    ) -> AgendaProjection:
        agenda = self.open_agenda(agenda_id)
        control = agenda.control()
        gate = self.effective_gate(control, agenda_id=agenda.agenda_id)
        if not gate["ok"] or gate["effective_mode"] != "LIVE":
            raise RuntimeError("Anissa Core cannot project campaign state outside effective LIVE mode")
        return agenda.projection(workflow, expected_control=control)

    def snapshot(
        self,
        workflow: str,
        *,
        agenda_id: str | None = None,
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
        agenda = self.open_agenda(agenda_id)
        control = agenda.control()
        gate = self.effective_gate(control, agenda_id=agenda.agenda_id)
        base = {
            "workflow": workflow,
            "date": self._today().isoformat(),
            "live_gate": gate,
        }
        if not gate["ok"] or gate["effective_mode"] != "LIVE":
            return {**base, "blocked": True}
        projection = agenda.projection(
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
