from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Mapping


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class TaskProjection:
    agenda_id: str
    task_id: str
    title: str
    status: str
    campaign: str
    category: str
    assigned: date | None
    due: date | None
    estimated_minutes: int | float | None
    actual_minutes: int | float | None
    remaining_minutes: float
    recorded_remaining_minutes: int | float | None
    priority: str
    owner_role: str
    evidence: str
    blocker: str | None
    depends_on_task_id: str | None


@dataclass(frozen=True)
class ApplicationProjection:
    agenda_id: str
    application_id: str
    programme: str
    institution: str
    route: str
    cycle: str
    status: str
    deadline: date | None
    funding_gate: str
    funding_status: str
    next_action: str
    priority: str


@dataclass(frozen=True)
class DeadlineProjection:
    agenda_id: str
    application_id: str
    deadline: date
    days_remaining: int
    next_action: str


@dataclass(frozen=True)
class WorkloadEventProjection:
    agenda_id: str
    event_id: str
    event_type: str
    title: str
    start: date | None
    end: date | None
    estimated_minutes: float
    status: str
    affected_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class AuditProjection:
    agenda_id: str
    week_start: date
    week_end: date
    tasks_assigned: int
    tasks_started: int
    tasks_done: int
    tasks_blocked: int
    tasks_overdue: int
    long_term_minutes: int
    india_minutes: int
    external_minutes: int
    effort_basis: str


@dataclass(frozen=True)
class WeeklyAuditRecordProjection:
    agenda_id: str
    audit_id: str
    week_start: date | None
    week_end: date | None
    generated_at: datetime | None
    summary: str
    strongest_achievement: str
    main_failure_pattern: str
    next_priorities: str
    exact_next_action: str
    effort_basis: str
    tasks_done: int
    tasks_assigned: int


@dataclass(frozen=True)
class CompletedTaskCreditProjection:
    agenda_id: str
    task_id: str
    completed_at: datetime
    minutes: float
    basis: str


@dataclass(frozen=True)
class AgendaProjection:
    agenda_id: str
    projection_type: str
    on: date
    tasks: tuple[TaskProjection, ...]
    applications: tuple[ApplicationProjection, ...]
    deadlines: tuple[DeadlineProjection, ...]
    workload_events: tuple[WorkloadEventProjection, ...]
    audit: AuditProjection
    latest_audit: WeeklyAuditRecordProjection | None
    completed_task_credit: tuple[CompletedTaskCreditProjection, ...]
    _compatibility_payload: Mapping[str, object]

    @classmethod
    def create(cls, *, compatibility_payload: dict, **values) -> "AgendaProjection":
        return cls(_compatibility_payload=_freeze(compatibility_payload), **values)

    def compatibility_payload(self) -> dict:
        return _thaw(self._compatibility_payload)
