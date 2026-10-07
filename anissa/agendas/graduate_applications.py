from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Callable

from logic.ids import weekly_audit_id
from logic.workbook_io import WorkbookGateway
from logic.workload import date_value, replan_preview, task_remaining_minutes
from project.environment import ProjectEnvironment
from project.projections import (
    AgendaProjection,
    ApplicationProjection,
    AuditProjection,
    CompletedTaskCreditProjection,
    DeadlineProjection,
    TaskProjection,
    WeeklyAuditRecordProjection,
    WorkloadEventProjection,
)
from project.reporting_week import (
    ReportingWeek,
    reporting_week_for,
    resolve_closed_reporting_week,
)
from project.telemetry_contract import IST


AGENDA_ID = "graduate_applications"
OPEN_TASKS = {"Not Started", "Started", "Blocked"}
ACTIVE_APPLICATIONS = {"Researching", "Preparing", "Applying", "Submitted", "Interview", "Offer"}
PRIORITY = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}
WEEKLY_AUDIT_HISTORY_LIMIT = 7
RECOVERY_HISTORY_DAYS = 56
RECOVERY_ROW_LIMIT = 6


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_number(value: object) -> int | float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    return float(value)


def _datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def _clip(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _task_sort(row: dict) -> tuple:
    due = date_value(row.get("Due Date")) or date.max
    return (PRIORITY.get(str(row.get("Priority") or ""), 9), due, str(row.get("Task ID") or ""))


def _compact_task(row: dict, *, include_definition: bool = False) -> dict:
    result = {
        "task_id": row.get("Task ID"),
        "title": row.get("Task Title"),
        "status": row.get("Status"),
        "campaign": row.get("Campaign"),
        "assigned": _iso(row.get("Assigned Date")),
        "due": _iso(row.get("Due Date")),
        "estimated_minutes": row.get("Estimated Minutes"),
        "remaining_minutes": task_remaining_minutes(row),
        "priority": row.get("Priority"),
        "blocker": row.get("Blocker"),
        "unblock_action": row.get("Unblock Action"),
        "owner_chat": row.get("Owner Chat"),
        "workload_event_id": row.get("Workload Event ID"),
        "depends_on_task_id": row.get("Depends On Task ID"),
        "reminder_count": row.get("Reminder Count"),
        "last_reminder": _iso(row.get("Last Reminder")),
    }
    if include_definition:
        result["definition_of_done"] = row.get("Definition of Done")
    return result


def _weekly_audit_projection(row: dict) -> WeeklyAuditRecordProjection:
    return WeeklyAuditRecordProjection(
        agenda_id=AGENDA_ID,
        audit_id=str(row.get("Audit ID") or ""),
        week_start=date_value(row.get("Week Start")),
        week_end=date_value(row.get("Week End")),
        generated_at=_datetime_value(row.get("Generated At")),
        summary=str(row.get("Summary") or ""),
        strongest_achievement=str(row.get("Strongest Achievement") or ""),
        main_failure_pattern=str(row.get("Main Failure Pattern") or ""),
        next_priorities=str(row.get("Next Priorities") or ""),
        exact_next_action=str(row.get("Exact Next Action") or ""),
        effort_basis=str(row.get("Effort Basis") or ""),
        tasks_done=int(float(row.get("Tasks Done") or 0)),
        tasks_assigned=int(float(row.get("Tasks Assigned") or 0)),
    )


def _recovery_moment(value: datetime) -> datetime:
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def _recovery_completion(row: dict) -> dict | None:
    """Use recorded completion time and evidence, never a current status as history."""

    if (
        str(row.get("Status") or "") != "Done"
        or not str(row.get("Task ID") or "").strip()
        or not str(row.get("Evidence") or "").strip()
    ):
        return None
    try:
        completed = _datetime_value(row.get("Completed At"))
    except ValueError:
        return None
    if completed is None:
        return None
    try:
        actual = _optional_float(row.get("Actual Minutes"))
        estimated = _optional_float(row.get("Estimated Minutes"))
    except (TypeError, ValueError):
        actual = estimated = None
    actual = actual if actual is not None and isfinite(actual) and actual >= 0 else None
    estimated = (
        estimated if estimated is not None and isfinite(estimated) and estimated >= 0
        else None
    )
    basis = "actual" if actual is not None else "estimated_proxy" if estimated is not None else "unavailable"
    minutes = actual if actual is not None else estimated if estimated is not None else 0.0
    return {
        "task_id": str(row.get("Task ID") or ""),
        "title": _clip(row.get("Task Title"), 120),
        "completed_at": _recovery_moment(completed),
        "evidence": _clip(row.get("Evidence"), 160),
        "minutes": round(minutes, 2),
        "basis": basis,
    }


def _recovery_credit(rows: list[dict], start: datetime, end: datetime) -> dict:
    matching = sorted(
        (row for row in rows if start < row["completed_at"] <= end),
        key=lambda row: (row["completed_at"], row["task_id"]),
        reverse=True,
    )
    return {
        "completion_count": len(matching),
        "actual_minutes": round(sum(
            row["minutes"] for row in matching if row["basis"] == "actual"
        ), 2),
        "estimated_proxy_minutes": round(sum(
            row["minutes"] for row in matching if row["basis"] == "estimated_proxy"
        ), 2),
        "completions": [{
            **row,
            "completed_at": row["completed_at"].isoformat(),
        } for row in matching[:RECOVERY_ROW_LIMIT]],
        "completions_truncated": len(matching) > RECOVERY_ROW_LIMIT,
    }


def _recovery_open_task(row: dict) -> dict:
    return {
        "task_id": str(row.get("Task ID") or ""),
        "title": _clip(row.get("Task Title"), 120),
        "status": str(row.get("Status") or ""),
        "due": _iso(row.get("Due Date")),
        "priority": str(row.get("Priority") or ""),
        "owner_chat": str(row.get("Owner Chat") or ""),
        "exact_next_action": _clip(row.get("Definition of Done"), 180),
    }


class GraduateApplicationsAgenda:
    """Own graduate-application workbook vocabulary behind one agenda interface."""

    agenda_id = AGENDA_ID

    def __init__(
        self,
        gateway: WorkbookGateway,
        environment: ProjectEnvironment,
        *,
        today_provider: Callable[[], date] = date.today,
    ):
        if gateway.path.resolve() != environment.brain_path.resolve() and gateway.is_canonical:
            raise RuntimeError("Agenda gateway and Project Environment disagree")
        self.gateway = gateway
        self.environment = environment
        self._today = today_provider

    def control(self) -> dict:
        return self.gateway.read_control()

    def recovery_state(
        self,
        role: str,
        *,
        as_of: datetime,
        since: datetime | None = None,
        week_ending: date | None = None,
    ) -> dict:
        """Read one workbook generation for bounded recovery evidence."""

        moment = _recovery_moment(as_of)
        delivered = _recovery_moment(since) if since is not None else None
        target = resolve_closed_reporting_week(week_ending, as_of=moment)
        state = self.gateway.read_bundle(
            "CONTROL", "TASKS", "APPLICATIONS", "WORKLOAD_EVENTS", "WEEKLY_AUDITS"
        )
        tasks = state["TASKS"]
        today = moment.date()
        audit_ends = {
            end for row in state["WEEKLY_AUDITS"]
            if (end := date_value(row.get("Week End"))) is not None and end <= target.end
        }
        latest_audit_end = max(audit_ends, default=None)
        recorded_row = next((
            row for row in state["WEEKLY_AUDITS"]
            if date_value(row.get("Week End")) == target.end
        ), None)
        audit_recorded = recorded_row is not None
        completed = [
            item for row in tasks if (item := _recovery_completion(row)) is not None
        ]
        open_rows = sorted(
            (row for row in tasks if row.get("Status") in OPEN_TASKS),
            key=_task_sort,
        )
        blockers = [row for row in open_rows if row.get("Status") == "Blocked"]
        overdue = [
            row for row in open_rows
            if (due := date_value(row.get("Due Date"))) is not None and due < today
        ]
        deadlines = sorted((
            row for row in state["APPLICATIONS"]
            if str(row.get("Status") or "") in ACTIVE_APPLICATIONS
            and (deadline := date_value(row.get("Deadline"))) is not None
            and deadline >= today
        ), key=lambda row: (
            date_value(row.get("Deadline")) or date.max,
            str(row.get("Application ID") or ""),
        ))
        current = {
            "as_of": moment.isoformat(),
            "blockers": [{
                "task_id": row.get("Task ID"),
                "title": _clip(row.get("Task Title"), 120),
                "blocker": _clip(row.get("Blocker"), 140),
                "unblock_action": _clip(row.get("Unblock Action"), 140),
            } for row in blockers[:RECOVERY_ROW_LIMIT]],
            "blocker_count": len(blockers),
            "overdue": [
                _recovery_open_task(row) for row in overdue[:RECOVERY_ROW_LIMIT]
            ],
            "overdue_count": len(overdue),
            "deadlines": [{
                "application_id": row.get("Application ID"),
                "programme": _clip(row.get("Programme / Position"), 120),
                "deadline": _iso(row.get("Deadline")),
                "next_action": _clip(row.get("Next Action"), 160),
            } for row in deadlines[:RECOVERY_ROW_LIMIT]],
            "deadline_count": len(deadlines),
        }
        weekday_reminder = self._snapshot(
            "weekday-reminder", state=state, today=today
        )["reminder_due"] if today.weekday() < 5 else False
        weekend_reminder = self._snapshot(
            "weekend-reminder", state=state, today=today
        )["reminder_due"] if today.weekday() >= 5 else False

        gap_summary = None
        prior_audit_end = max(
            (end for end in audit_ends if end < target.end), default=None
        ) if audit_recorded else latest_audit_end
        if not audit_recorded or prior_audit_end is not None or delivered is not None:
            requested_start = min((
                prior_audit_end + timedelta(days=1)
                if prior_audit_end is not None else
                reporting_week_for(delivered).start if delivered is not None else target.start
            ), target.start)
            bounded_start = max(
                requested_start,
                target.end - timedelta(days=RECOVERY_HISTORY_DAYS - 1),
            )
            period_start = reporting_week_for(bounded_start).start
            period_end = target.end
            missed_weeks = []
            cursor = period_start
            while cursor <= period_end:
                end = cursor + timedelta(days=6)
                if end not in audit_ends and end <= period_end:
                    missed_weeks.append({
                        "week_start": cursor.isoformat(),
                        "week_end": end.isoformat(),
                    })
                cursor += timedelta(days=7)
            start_moment = datetime.combine(
                period_start - timedelta(days=1), datetime.max.time(), tzinfo=IST
            )
            end_moment = datetime.combine(period_end, datetime.max.time(), tzinfo=IST)
            if missed_weeks or not audit_recorded:
                gap_summary = {
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "period_truncated": period_start > requested_start,
                    "missed_weeks": missed_weeks,
                    "historical_completions": _recovery_credit(completed, start_moment, end_moment),
                    "current": current,
                }

        progress_since = None
        if delivered is not None:
            cutoff = max(delivered, moment - timedelta(days=RECOVERY_HISTORY_DAYS))
            progress_since = {
                "since": cutoff.isoformat(),
                "through": moment.isoformat(),
                "period_truncated": cutoff > delivered,
                **_recovery_credit(completed, cutoff, moment),
            }
        return {
            "_control": state["CONTROL"],
            "agenda_id": self.agenda_id,
            "role": role,
            "as_of": moment.isoformat(),
            "audit_target_week_end": target.end.isoformat(),
            "audit_recorded": audit_recorded,
            "recorded_audit": (
                asdict(_weekly_audit_projection(recorded_row))
                if recorded_row is not None else None
            ),
            "latest_audit_end": latest_audit_end.isoformat() if latest_audit_end else None,
            "reminder_due": weekday_reminder,
            "weekend_reminder_due": weekend_reminder,
            "recovery_constraints": {
                "allow_new_tasks_today": today.weekday() != 6,
                "backdate_tasks": False,
                "stack_daily_search_quotas": False,
            },
            "gap_summary": gap_summary,
            "progress_since": progress_since,
            "current_work": {
                "as_of": moment.isoformat(),
                "priority_open_tasks": [
                    _recovery_open_task(row) for row in open_rows[:4]
                ],
                "active_workload_events": [{
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "title": _clip(event["title"], 120),
                    "end": event["end"],
                    "estimated_minutes": event["estimated_minutes"],
                } for event in self._active_events(today, state["WORKLOAD_EVENTS"])[:4]],
                "deadlines": current["deadlines"],
            },
        }

    def list_tasks(self) -> list[dict]:
        return self.gateway.rows("TASKS")

    def task_detail(self, task_id: str) -> dict | None:
        return next(
            (row for row in self.list_tasks() if str(row.get("Task ID") or "") == task_id),
            None,
        )

    def set_task_status(self, task_id: str, status: str, **evidence) -> None:
        self.gateway.set_task_status(task_id, status, **evidence)

    def set_control(self, key: str, value: object) -> None:
        self.gateway.set_control(key, value)

    def record_reminders(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            self.gateway.record_reminder(task_id)

    def preview_replan(self, event: dict, changes: list[dict]) -> dict:
        return replan_preview(self.list_tasks(), event, changes)

    def apply_replan(self, event: dict, changes: list[dict]) -> dict:
        return self.gateway.apply_workload_replan(event, changes)

    def _active_events(self, today: date, rows: list[dict]) -> list[dict]:
        result = []
        for row in rows:
            if str(row.get("Status") or "").upper() not in {"PLANNED", "ACTIVE"}:
                continue
            start = date_value(row.get("Start Date")) or date.min
            end = date_value(row.get("End Date")) or date.max
            if start <= today <= end:
                result.append({
                    "event_id": row.get("Event ID"),
                    "event_type": row.get("Event Type"),
                    "title": row.get("Title"),
                    "start": _iso(start),
                    "end": _iso(end),
                    "estimated_minutes": row.get("Estimated Minutes"),
                    "status": row.get("Status"),
                    "affected_task_ids": row.get("Affected Task IDs"),
                })
        return result

    def weekly_metrics(self, today: date | None = None, *, state: dict | None = None) -> dict:
        today = today or self._today()
        reporting_week = reporting_week_for(today)
        week_start = reporting_week.start
        week_end = reporting_week.end
        state = state or self.gateway.read_bundle("TASKS", "APPLICATIONS", "BUDGET")
        tasks = state["TASKS"]
        applications = state["APPLICATIONS"]
        budget = state["BUDGET"]
        assigned = [row for row in tasks if (value := date_value(row.get("Assigned Date"))) and week_start <= value <= week_end]
        started = [row for row in tasks if (value := date_value(row.get("Started At"))) and week_start <= value <= week_end]
        done = [row for row in tasks if (value := date_value(row.get("Completed At"))) and week_start <= value <= week_end]
        blocked = [row for row in tasks if str(row.get("Status") or "") == "Blocked"]
        overdue = [
            row for row in tasks
            if str(row.get("Status") or "") in OPEN_TASKS
            and (value := date_value(row.get("Due Date"))) and value < today
        ]
        stale = [
            row for row in blocked
            if (value := date_value(row.get("Blocked Since"))) and value <= today - timedelta(days=3)
        ]

        effort = {"Long-term": 0, "India": 0, "External": 0}
        actual_count = 0
        for row in done:
            actual = row.get("Actual Minutes")
            if actual not in (None, ""):
                minutes = max(0, int(float(actual)))
                actual_count += 1
            else:
                minutes = max(0, int(float(row.get("Estimated Minutes") or 0)))
            campaign = str(row.get("Campaign") or "")
            bucket = (
                "India" if campaign.lower().startswith("india")
                else "External" if campaign.lower().startswith("external")
                else "Long-term"
            )
            effort[bucket] += minutes
        campaign_total = effort["Long-term"] + effort["India"]
        basis = (
            "actual" if done and actual_count == len(done)
            else "mixed actual/estimated" if actual_count
            else "estimated proxy"
        )

        active_applications = [
            row for row in applications if str(row.get("Status") or "") in ACTIVE_APPLICATIONS
        ]
        deadlines = []
        for row in active_applications:
            deadline = date_value(row.get("Deadline"))
            if deadline and today <= deadline <= today + timedelta(days=60):
                deadlines.append({
                    "application_id": row.get("Application ID"),
                    "programme": row.get("Programme / Position"),
                    "institution": row.get("Institution"),
                    "deadline": deadline.isoformat(),
                    "days": (deadline - today).days,
                    "next_action": row.get("Next Action"),
                })
        deadlines.sort(key=lambda row: row["days"])
        planned = sum(float(row.get("Planned INR") or 0) for row in budget)
        paid = sum(float(row.get("Paid INR") or 0) for row in budget)
        achievements = [{
            "task_id": row.get("Task ID"),
            "title": row.get("Task Title"),
            "evidence": row.get("Evidence"),
            "minutes": row.get("Actual Minutes") or row.get("Estimated Minutes"),
        } for row in sorted(
            done,
            key=lambda row: (-(float(row.get("Actual Minutes") or row.get("Estimated Minutes") or 0)), _task_sort(row)),
        )[:3]]
        return {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "tasks_assigned": len(assigned),
            "tasks_started": len(started),
            "tasks_done": len(done),
            "tasks_blocked": len(blocked),
            "tasks_overdue": len(overdue),
            "long_term_minutes": effort["Long-term"],
            "india_minutes": effort["India"],
            "external_minutes": effort["External"],
            "long_term_ratio": round(effort["Long-term"] / campaign_total, 3) if campaign_total else None,
            "india_ratio": round(effort["India"] / campaign_total, 3) if campaign_total else None,
            "effort_basis": basis,
            "active_applications": len(active_applications),
            "budget_planned_inr": planned,
            "budget_paid_inr": paid,
            "stale_blockers": [{
                "task_id": row.get("Task ID"),
                "title": row.get("Task Title"),
                "blocked_since": _iso(row.get("Blocked Since")),
                "blocker": row.get("Blocker"),
                "unblock_action": row.get("Unblock Action"),
            } for row in stale],
            "overdue_tasks": [_compact_task(row) for row in sorted(overdue, key=_task_sort)[:5]],
            "deadlines_60_days": deadlines[:8],
            "achievement_candidates": achievements,
        }

    def projection(
        self,
        workflow: str,
        *,
        expected_control: dict | None = None,
        audit_week: ReportingWeek | None = None,
    ) -> AgendaProjection:
        state = self.gateway.read_bundle(
            "CONTROL", "TASKS", "APPLICATIONS", "WORKLOAD_EVENTS", "BUDGET",
            "SEARCH_HISTORY", "SOURCE_HEALTH", "WEEKLY_AUDITS",
        )
        if expected_control is not None and state["CONTROL"] != expected_control:
            raise RuntimeError("Agenda state changed while preparing its projection")
        today = self._today()
        target_week = (
            audit_week or resolve_closed_reporting_week()
            if workflow == "weekly-audit"
            else None
        )
        metrics = self.weekly_metrics(
            target_week.end if target_week is not None else today,
            state=state,
        )
        compatibility = self._snapshot(
            workflow,
            state=state,
            today=today,
            audit_metrics=metrics,
            audit_week=target_week,
        )
        tasks = tuple(TaskProjection(
            agenda_id=self.agenda_id,
            task_id=str(row.get("Task ID") or ""),
            title=str(row.get("Task Title") or ""),
            status=str(row.get("Status") or ""),
            campaign=str(row.get("Campaign") or ""),
            category=str(row.get("Category") or ""),
            assigned=date_value(row.get("Assigned Date")),
            due=date_value(row.get("Due Date")),
            estimated_minutes=_optional_number(row.get("Estimated Minutes")),
            actual_minutes=_optional_number(row.get("Actual Minutes")),
            remaining_minutes=float(task_remaining_minutes(row)),
            recorded_remaining_minutes=_optional_number(row.get("Remaining Minutes")),
            priority=str(row.get("Priority") or ""),
            owner_role=str(row.get("Owner Chat") or ""),
            evidence=str(row.get("Evidence") or ""),
            blocker=row.get("Blocker") or None,
            depends_on_task_id=row.get("Depends On Task ID") or None,
        ) for row in state["TASKS"] if row.get("Task ID"))
        applications = tuple(ApplicationProjection(
            agenda_id=self.agenda_id,
            application_id=str(row.get("Application ID") or ""),
            programme=str(row.get("Programme / Position") or ""),
            institution=str(row.get("Institution") or ""),
            route=str(row.get("Route") or ""),
            cycle=str(row.get("Cycle") or ""),
            status=str(row.get("Status") or ""),
            deadline=date_value(row.get("Deadline")),
            funding_gate=str(row.get("Funding Gate") or ""),
            funding_status=str(row.get("Funding Status") or ""),
            next_action=str(row.get("Next Action") or ""),
            priority=str(row.get("Priority Tier") or ""),
        ) for row in state["APPLICATIONS"] if row.get("Application ID"))
        deadlines = tuple(sorted((
            DeadlineProjection(
                agenda_id=self.agenda_id,
                application_id=row.application_id,
                deadline=row.deadline,
                days_remaining=(row.deadline - today).days,
                next_action=row.next_action,
            )
            for row in applications
            if row.status in ACTIVE_APPLICATIONS and row.deadline is not None
        ), key=lambda row: (row.deadline, row.application_id)))
        active_events = self._active_events(today, state["WORKLOAD_EVENTS"])
        workload_events = tuple(WorkloadEventProjection(
            agenda_id=self.agenda_id,
            event_id=str(row.get("event_id") or ""),
            event_type=str(row.get("event_type") or ""),
            title=str(row.get("title") or ""),
            start=date_value(row.get("start")),
            end=date_value(row.get("end")),
            estimated_minutes=float(row.get("estimated_minutes") or 0),
            status=str(row.get("status") or ""),
            affected_task_ids=tuple(
                value.strip() for value in str(row.get("affected_task_ids") or "").split(";")
                if value.strip()
            ),
        ) for row in active_events)
        audit_rows = sorted(
            state["WEEKLY_AUDITS"],
            key=lambda row: (
                date_value(row.get("Week Start")) or date.min,
                str(row.get("Generated At") or ""),
            ),
            reverse=True,
        )
        weekly_audits = tuple(
            _weekly_audit_projection(row)
            for row in audit_rows[:WEEKLY_AUDIT_HISTORY_LIMIT]
        )
        latest_audit = weekly_audits[0] if weekly_audits else None
        completed_credit = []
        for row in state["TASKS"]:
            if str(row.get("Status") or "") != "Done" or not row.get("Task ID"):
                continue
            completed_at = _datetime_value(row.get("Completed At"))
            actual = _optional_float(row.get("Actual Minutes"))
            estimated = _optional_float(row.get("Estimated Minutes"))
            minutes = actual if actual is not None else estimated
            if completed_at is None or minutes is None or minutes <= 0:
                continue
            completed_credit.append(CompletedTaskCreditProjection(
                agenda_id=self.agenda_id,
                task_id=str(row["Task ID"]),
                completed_at=completed_at,
                minutes=minutes,
                basis="actual" if actual is not None else "estimated_proxy",
            ))
        completed_credit.sort(key=lambda row: (row.completed_at.isoformat(), row.task_id))
        return AgendaProjection.create(
            agenda_id=self.agenda_id,
            projection_type=workflow,
            on=today,
            tasks=tasks,
            applications=applications,
            deadlines=deadlines,
            workload_events=workload_events,
            audit=AuditProjection(
                agenda_id=self.agenda_id,
                week_start=date.fromisoformat(metrics["week_start"]),
                week_end=date.fromisoformat(metrics["week_end"]),
                tasks_assigned=metrics["tasks_assigned"],
                tasks_started=metrics["tasks_started"],
                tasks_done=metrics["tasks_done"],
                tasks_blocked=metrics["tasks_blocked"],
                tasks_overdue=metrics["tasks_overdue"],
                long_term_minutes=metrics["long_term_minutes"],
                india_minutes=metrics["india_minutes"],
                external_minutes=metrics["external_minutes"],
                effort_basis=metrics["effort_basis"],
            ),
            latest_audit=latest_audit,
            weekly_audits=weekly_audits,
            completed_task_credit=tuple(completed_credit),
            compatibility_payload=compatibility,
        )

    def _snapshot(
        self,
        workflow: str,
        *,
        state: dict,
        today: date,
        audit_metrics: dict | None = None,
        audit_week: ReportingWeek | None = None,
    ) -> dict:
        control = state["CONTROL"]
        tasks = state["TASKS"]
        applications = state["APPLICATIONS"]
        open_rows = sorted(
            (row for row in tasks if row.get("Status") in OPEN_TASKS), key=_task_sort
        )
        active_events = self._active_events(today, state["WORKLOAD_EVENTS"])
        base = {
            "india_campaign_status": control.get("india_campaign_status"),
            "long_term_ratio": control.get("long_term_ratio"),
            "india_ratio": control.get("india_ratio"),
        }

        if workflow == "weekday-reminder":
            due = [
                row for row in open_rows
                if row.get("Status") == "Not Started" and date_value(row.get("Assigned Date")) == today
            ]
            result = {
                **base,
                "reminder_due": bool(due),
                "due_reminders": [_compact_task(row, include_definition=True) for row in due],
            }
            return result

        if workflow == "weekend-reminder":
            saturday = today - timedelta(days=(today.weekday() - 5) % 7)
            sunday = saturday + timedelta(days=1)
            weekend = []
            for row in tasks:
                assigned = date_value(row.get("Assigned Date"))
                linked = bool(row.get("Workload Event ID"))
                weekend_owner = str(row.get("Owner Chat") or "") == "WEEKEND"
                if assigned and saturday <= assigned <= sunday and (linked or weekend_owner):
                    weekend.append(row)
            reminder_due = bool(weekend) and all(
                str(row.get("Status") or "") == "Not Started" for row in weekend
            )
            result = {
                **base,
                "weekend_start": saturday.isoformat(),
                "weekend_end": sunday.isoformat(),
                "reminder_due": reminder_due,
                "bundle_tasks": [
                    _compact_task(row, include_definition=reminder_due)
                    for row in sorted(weekend, key=_task_sort)
                ],
            }
            return result

        if workflow == "weekday-close":
            today_rows = sorted(
                (
                    row for row in tasks
                    if date_value(row.get("Assigned Date")) == today
                ),
                key=_task_sort,
            )
            completed_today = sorted(
                (
                    row for row in tasks
                    if date_value(row.get("Completed At")) == today
                ),
                key=_task_sort,
            )
            carryover = sorted((
                row for row in open_rows
                if (date_value(row.get("Due Date")) or date.max) < today
            ), key=_task_sort)
            unfinished = [
                row for row in today_rows if row.get("Status") in OPEN_TASKS
            ]
            next_row = next(iter(unfinished or carryover), None)
            statuses = {
                status: sum(
                    str(row.get("Status") or "") == status for row in today_rows
                )
                for status in ("Not Started", "Started", "Blocked", "Done", "Cancelled")
            }
            return {
                **base,
                "closing_due": bool(today_rows or completed_today),
                "active_workload_events": active_events,
                "today_plan": {
                    "scheduled_minutes": sum(
                        int(float(row.get("Estimated Minutes") or 0))
                        for row in today_rows
                    ),
                    "remaining_minutes": sum(
                        task_remaining_minutes(row) for row in unfinished
                    ),
                    "status_counts": statuses,
                },
                "today_tasks": [{
                    **_compact_task(row, include_definition=True),
                    "completed_at": _iso(row.get("Completed At")),
                    "evidence": _clip(row.get("Evidence"), 180),
                } for row in today_rows[:6]],
                "completed_today": [{
                    "task_id": row.get("Task ID"),
                    "title": row.get("Task Title"),
                    "completed_at": _iso(row.get("Completed At")),
                    "evidence": _clip(row.get("Evidence"), 180),
                    "minutes": row.get("Actual Minutes") or row.get("Estimated Minutes"),
                } for row in completed_today[:4]],
                "overdue_carryover": [
                    _compact_task(row, include_definition=True) for row in carryover[:2]
                ],
                "exact_next_action": (
                    _clip(next_row.get("Definition of Done"), 180)
                    if next_row else ""
                ),
            }

        if workflow == "weekly-audit":
            metrics = audit_metrics or self.weekly_metrics(today, state=state)
            target = audit_week or reporting_week_for(
                date.fromisoformat(metrics["week_end"])
            )
            existing = next((
                row for row in state["WEEKLY_AUDITS"]
                if date_value(row.get("Week Start")) == target.start
            ), None)
            return {
                **base,
                "audit_target": {
                    "week_start": target.start.isoformat(),
                    "week_end": target.end.isoformat(),
                    "audit_id": weekly_audit_id(target.start),
                },
                "audit_recorded": existing is not None,
                "metrics": metrics,
                "active_workload_events": active_events,
            }

        if workflow == "weekend-morning":
            saturday = today - timedelta(days=(today.weekday() - 5) % 7)
            sunday = saturday + timedelta(days=1)
            active_task_ids = {
                task_id.strip()
                for event in active_events
                for task_id in str(event.get("affected_task_ids") or "").split(";")
                if task_id.strip()
            }
            weekend_rows = []
            for row in open_rows:
                assigned = date_value(row.get("Assigned Date"))
                in_window = bool(
                    assigned and saturday <= assigned <= sunday
                    and (row.get("Workload Event ID") or str(row.get("Owner Chat") or "") == "WEEKEND")
                )
                active_link = (
                    str(row.get("Task ID") or "") in active_task_ids
                    and task_remaining_minutes(row) > 0
                )
                if in_window or active_link:
                    weekend_rows.append(row)
            scheduled = sum(task_remaining_minutes(row) for row in weekend_rows)
            minimum = int(control.get("weekend_minutes_min") or 240)
            maximum = int(control.get("weekend_minutes_max") or 360)
            external = sum(
                int(float(event.get("estimated_minutes") or 0))
                for event in active_events if event["event_type"] == "EXTERNAL_CAPACITY"
            )
            effective_minimum = max(0, minimum - external)
            effective_maximum = max(0, maximum - external)
            return {
                **base,
                "weekend_start": saturday.isoformat(),
                "weekend_end": sunday.isoformat(),
                "active_workload_events": active_events,
                "workload": {
                    "scheduled_minutes": scheduled,
                    "minimum_minutes": minimum,
                    "maximum_minutes": maximum,
                    "external_capacity_minutes": external,
                    "remaining_to_minimum": max(0, effective_minimum - scheduled),
                    "remaining_to_maximum": max(0, effective_maximum - scheduled),
                    "suppress_new_bundle": scheduled >= effective_minimum,
                },
                "weekend_tasks": [
                    _compact_task(row, include_definition=True)
                    for row in sorted(weekend_rows, key=_task_sort)[:6]
                ],
            }

        active_applications = sorted(
            (row for row in applications if row.get("Status") in ACTIVE_APPLICATIONS),
            key=lambda row: (
                date_value(row.get("Deadline")) or date.max,
                str(row.get("Application ID") or ""),
            ),
        )
        scheduled = sum(
            task_remaining_minutes(row)
            for row in open_rows if date_value(row.get("Assigned Date")) == today
        )
        capacity = int(control.get("weekday_minutes") or 90)
        if any(
            event["event_type"] in {"USER_INITIATED_SURGE", "ANISSA_RECOMMENDED_SURGE"}
            for event in active_events
        ):
            capacity += int(control.get("surge_extra_minutes_max") or 0)
        external = sum(
            int(float(event.get("estimated_minutes") or 0))
            for event in active_events if event["event_type"] == "EXTERNAL_CAPACITY"
        )
        capacity = max(0, capacity - external)
        result = {
            **base,
            "active_workload_events": active_events,
            "workload": {
                "scheduled_minutes_today": scheduled,
                "planning_capacity_minutes": capacity,
                "remaining_capacity_minutes": max(0, capacity - scheduled),
                "external_capacity_minutes": external,
                "suppress_new_task_creation": scheduled >= capacity,
            },
            "priority_open_tasks": [_compact_task(row) for row in open_rows[:4]],
            "active_applications": [{
                "application_id": row.get("Application ID"),
                "programme": row.get("Programme / Position"),
                "institution": row.get("Institution"),
                "status": row.get("Status"),
                "deadline": _iso(row.get("Deadline")),
                "funding_status": _clip(row.get("Funding Status"), 120),
                "next_action": _clip(row.get("Next Action"), 180),
            } for row in active_applications[:3]],
        }
        if workflow == "weekday-morning":
            result["recent_searches"] = [{
                "search_id": row.get("Search ID"),
                "rotation": row.get("Rotation"),
                "campaign": row.get("Campaign"),
                "scope": _clip(row.get("Query / Scope"), 140),
                "source": _clip(row.get("Source"), 100),
                "added": row.get("Added"),
                "updated": row.get("Updated"),
                "notes": _clip(row.get("Notes"), 140),
            } for row in state["SEARCH_HISTORY"][-2:]]
            result["source_health"] = [
                row for row in state["SOURCE_HEALTH"]
                if str(row.get("Status") or "").lower() not in {"", "ok", "healthy"}
            ]
        return result

    def record_weekly_audit(
        self,
        *,
        week_ending: date,
        as_of: datetime | None = None,
        strongest_achievement: str = "",
        failure_pattern: str = "",
        next_priorities: str = "",
        exact_next_action: str,
        summary: str = "",
    ) -> tuple[str, dict]:
        target = resolve_closed_reporting_week(week_ending, as_of=as_of)
        state = self.gateway.read_bundle(
            "TASKS", "APPLICATIONS", "BUDGET", "WEEKLY_AUDITS"
        )
        metrics = self.weekly_metrics(target.end, state=state)
        if any(
            date_value(row.get("Week Start")) == target.start
            for row in state["WEEKLY_AUDITS"]
        ):
            return "existing", metrics
        row = {
            "Audit ID": weekly_audit_id(target.start),
            "Week Start": metrics["week_start"],
            "Week End": metrics["week_end"],
            "Tasks Assigned": metrics["tasks_assigned"],
            "Tasks Started": metrics["tasks_started"],
            "Tasks Done": metrics["tasks_done"],
            "Tasks Blocked": metrics["tasks_blocked"],
            "Tasks Overdue": metrics["tasks_overdue"],
            "Long-term Minutes": metrics["long_term_minutes"],
            "India Minutes": metrics["india_minutes"],
            "External Minutes": metrics["external_minutes"],
            "Long-term Ratio": metrics["long_term_ratio"],
            "India Ratio": metrics["india_ratio"],
            "Effort Basis": metrics["effort_basis"],
            "Active Applications": metrics["active_applications"],
            "Budget Planned INR": metrics["budget_planned_inr"],
            "Budget Paid INR": metrics["budget_paid_inr"],
            "Stale Blocker IDs": "; ".join(row["task_id"] for row in metrics["stale_blockers"]),
            "Strongest Achievement": strongest_achievement,
            "Main Failure Pattern": failure_pattern,
            "Next Priorities": next_priorities,
            "Exact Next Action": exact_next_action,
            "Owner Chat": "WEEKEND",
            "Summary": summary,
        }
        action, _ = self.gateway.record_weekly_audit(row)
        return action, metrics
