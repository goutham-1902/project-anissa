from __future__ import annotations

from anissa.core import AnissaCore
from logic.workbook_io import WorkbookGateway
from project.environment import ProjectEnvironment
from soldiers.thula.src.sync import run_csv_sync as worker_csv_sync
from soldiers.thula.src.sync import run_sync as worker_sync


def completed_task_credit(environment: ProjectEnvironment) -> tuple:
    """Publish the only agenda data Thula is allowed to consume."""
    core = AnissaCore(
        environment,
        gateway=WorkbookGateway(environment=environment),
    )
    return core.projection(
        "thula-completed-task-credit",
        agenda_id=core.portfolio.default_agenda_id,
    ).completed_task_credit


def run_thula_sync(*, environment: ProjectEnvironment, **worker_args) -> dict:
    return worker_sync(
        completed_task_credit=completed_task_credit(environment),
        **worker_args,
    )


def run_thula_csv_sync(*, environment: ProjectEnvironment, **worker_args) -> dict:
    return worker_csv_sync(
        completed_task_credit=completed_task_credit(environment),
        **worker_args,
    )
