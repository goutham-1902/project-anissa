from __future__ import annotations

from anissa.core import AnissaCore
from logic.workbook_io import WorkbookGateway
from project.environment import ProjectEnvironment
from worker1.src.sync import run_csv_sync as worker_csv_sync
from worker1.src.sync import run_sync as worker_sync


def completed_task_credit(environment: ProjectEnvironment) -> tuple:
    """Publish the only agenda data A2A is allowed to consume."""
    core = AnissaCore(
        environment,
        gateway=WorkbookGateway(environment=environment),
    )
    return core.projection("a2a-completed-task-credit").completed_task_credit


def run_a2a_sync(*, environment: ProjectEnvironment, **worker_args) -> dict:
    return worker_sync(
        completed_task_credit=completed_task_credit(environment),
        **worker_args,
    )


def run_a2a_csv_sync(*, environment: ProjectEnvironment, **worker_args) -> dict:
    return worker_csv_sync(
        completed_task_credit=completed_task_credit(environment),
        **worker_args,
    )
