from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Protocol

from anissa.agendas.graduate_applications import GraduateApplicationsAgenda
from anissa.portfolio import AgendaRegistration, Portfolio
from logic.workbook_io import WorkbookGateway
from project.environment import ProjectEnvironment


class AgendaRuntime(Protocol):
    """Runtime surface Core needs without depending on one agenda class."""

    agenda_id: str

    def control(self) -> dict: ...

    def projection(self, workflow: str, **options): ...


AgendaBuilder = Callable[[AgendaRegistration], AgendaRuntime]


class AgendaCatalog:
    """Resolve, validate, and lazily construct registered agenda runtimes."""

    def __init__(
        self,
        portfolio: Portfolio,
        environment: ProjectEnvironment,
        *,
        gateway: WorkbookGateway | None = None,
        today_provider: Callable[[], date] = date.today,
        builders: Mapping[str, AgendaBuilder] | None = None,
    ):
        self._portfolio = portfolio
        self._environment = environment
        self._gateway = gateway
        self._today = today_provider
        self._builders = dict(builders or {})
        self._builders.setdefault(
            GraduateApplicationsAgenda.agenda_id,
            self._build_graduate_applications,
        )
        self._instances: dict[str, AgendaRuntime] = {}

    @property
    def default_agenda_id(self) -> str:
        return self._portfolio.default_agenda_id

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    def open(
        self,
        agenda_id: str | None = None,
        *,
        for_mutation: bool = False,
    ) -> AgendaRuntime:
        """Open one readable agenda; mutations require an ACTIVE lifecycle."""

        registration = self._portfolio.registration(agenda_id)
        if registration.lifecycle == "PAUSED":
            raise RuntimeError(
                f"Agenda {registration.agenda_id} is unavailable "
                f"({registration.lifecycle})"
            )
        if for_mutation and registration.lifecycle != "ACTIVE":
            raise RuntimeError(
                f"Agenda {registration.agenda_id} is read-only "
                f"({registration.lifecycle})"
            )
        try:
            builder = self._builders[registration.agenda_id]
        except KeyError as exc:
            raise RuntimeError(
                f"Agenda {registration.agenda_id} has no registered implementation"
            ) from exc
        runtime = self._instances.get(registration.agenda_id)
        if runtime is None:
            runtime = builder(registration)
            if runtime.agenda_id != registration.agenda_id:
                raise RuntimeError(
                    "Agenda implementation identity does not match its registration"
                )
            self._instances[registration.agenda_id] = runtime
        return runtime

    def _build_graduate_applications(
        self,
        registration: AgendaRegistration,
    ) -> GraduateApplicationsAgenda:
        if registration.state_locator != "canonical_brain":
            raise RuntimeError(
                f"Agenda {registration.agenda_id} has unsupported state locator"
            )
        agenda_paths = self._environment.agenda(registration.agenda_id)
        gateway = self._gateway or WorkbookGateway(
            agenda_paths.brain_path,
            environment=self._environment,
        )
        return GraduateApplicationsAgenda(
            gateway,
            self._environment,
            today_provider=self._today,
        )
