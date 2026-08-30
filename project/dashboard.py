from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from anissa.core import AnissaCore
from logic.workbook_io import WorkbookGateway
from project.environment import ProjectEnvironment
from project.projections import AgendaProjection
from project.reporting_week import ReportingWeek, previous_reporting_week, reporting_week_for
from project.telemetry_contract import IST, operational_date, parse_moment, read_publication


ACTIVE_APPLICATIONS = {"Researching", "Preparing", "Applying", "Submitted", "Interview", "Offer"}
OPEN_TASKS = {"Not Started", "Started", "Blocked"}
PRIORITY = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}


def _freshness(status: dict, now: datetime) -> dict:
    last_success = status.get("last_success")
    if not last_success:
        return {"freshness": "unknown", "age_hours": None, "usable_for_judgment": False}
    try:
        age = max(0.0, (now - parse_moment(last_success)).total_seconds() / 3600)
    except Exception:
        return {"freshness": "invalid", "age_hours": None, "usable_for_judgment": False}
    state = status.get("state")
    if state == "COMPLETE" and age <= 28:
        freshness = "fresh"
    elif state == "STALE" and age <= 52:
        freshness = "stale_one_day"
    elif state == "STALE":
        freshness = "stale"
    elif state != "COMPLETE":
        freshness = "failed"
    elif age <= 52:
        freshness = "stale_one_day"
    else:
        freshness = "stale"
    return {
        "freshness": freshness,
        "age_hours": round(age, 1),
        "usable_for_judgment": state == "COMPLETE" and freshness == "fresh",
    }


def _series(rows: list[dict], today: date, days: int) -> list[dict]:
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        day = str(row.get("operational_date") or "")
        category = str(row.get("category") or "Other")
        minutes = float(row.get("minutes") or 0)
        by_day[day][category] += minutes
        if category == "Applications":
            basis = "actual" if row.get("basis") == "actual" else "estimated_proxy"
            by_day[day][f"Applications:{basis}"] += minutes
    output = []
    start = today - timedelta(days=days - 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        buckets = by_day[day.isoformat()]
        observed = (
            buckets.get("Research", 0) + buckets.get("Study", 0)
            + buckets.get("Other", 0) + buckets.get("Applications:actual", 0)
        )
        workload_credit = (
            buckets.get("Research", 0) + buckets.get("Study", 0)
            + buckets.get("Applications", 0) + buckets.get("Other", 0)
        )
        output.append({
            "date": day.isoformat(),
            "Research": round(buckets.get("Research", 0), 2),
            "Study": round(buckets.get("Study", 0), 2),
            "Applications": round(buckets.get("Applications", 0), 2),
            "ApplicationsActual": round(buckets.get("Applications:actual", 0), 2),
            "ApplicationsProxy": round(buckets.get("Applications:estimated_proxy", 0), 2),
            "Other": round(buckets.get("Other", 0), 2),
            "observed_total": round(observed, 2),
            "total": round(workload_credit, 2),
        })
    return output


def _totals(rows: list[dict], start: date, end: date) -> dict:
    totals = defaultdict(float)
    active_days = set()
    application_actual = 0.0
    application_proxy = 0.0
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("operational_date")))
        except Exception:
            continue
        if not start <= day <= end:
            continue
        minutes = float(row.get("minutes") or 0)
        category = str(row.get("category") or "Other")
        totals[category] += minutes
        if minutes > 0:
            active_days.add(day)
        if category == "Applications":
            if row.get("basis") == "actual":
                application_actual += minutes
            else:
                application_proxy += minutes
    return {
        "research_minutes": round(totals["Research"], 2),
        "study_minutes": round(totals["Study"], 2),
        "applications_minutes": round(totals["Applications"], 2),
        "applications_actual_minutes": round(application_actual, 2),
        "applications_estimated_proxy_minutes": round(application_proxy, 2),
        "other_minutes": round(totals["Other"], 2),
        "total_minutes": round(sum(totals.values()), 2),
        "active_days": len(active_days),
    }


def _series_window(rows: list[dict], fallback: date) -> tuple[date, date]:
    forest_days = []
    all_days = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("operational_date")))
        except Exception:
            continue
        all_days.append(day)
        if str(row.get("source") or "") == "Forest":
            forest_days.append(day)
    bounded = forest_days or all_days
    return (min(bounded), max(bounded)) if bounded else (fallback, fallback)


def _weekly_distribution(totals: dict) -> dict:
    values = {
        "Research": float(totals.get("research_minutes") or 0),
        "Study": float(totals.get("study_minutes") or 0),
        "Applications": float(totals.get("applications_minutes") or 0),
    }
    denominator = sum(values.values())
    return {
        "minutes": {key: round(value, 2) for key, value in values.items()},
        "shares": {
            key: round(value / denominator, 4) if denominator else 0
            for key, value in values.items()
        },
        "denominator_minutes": round(denominator, 2),
        "basis": "Research/Study observed focus plus Applications completion credit",
    }


def _coverage_intervals(status: dict) -> list[tuple[datetime, datetime]]:
    raw = status.get("coverage_intervals") or []
    if not raw and status.get("coverage_start") and status.get("coverage_through"):
        raw = [{"start": status["coverage_start"], "end": status["coverage_through"]}]
    intervals = []
    for row in raw:
        try:
            start = parse_moment(row.get("start"))
            end = parse_moment(row.get("end"))
        except Exception:
            continue
        if end > start:
            intervals.append((start, end))
    return sorted(intervals)


def _week_coverage(status: dict, week: ReportingWeek) -> str:
    cursor = week.telemetry_start
    overlap = False
    for start, end in _coverage_intervals(status):
        if end <= week.telemetry_start or start >= week.telemetry_end:
            continue
        overlap = True
        if start > cursor:
            return "partial"
        cursor = max(cursor, end)
        if cursor >= week.telemetry_end:
            return "complete"
    return "partial" if overlap else "pending"


def _weekly_history(projection: AgendaProjection, rows: list[dict], status: dict) -> list[dict]:
    audits = tuple(getattr(projection, "weekly_audits", ()))
    if not audits and projection.latest_audit:
        audits = (projection.latest_audit,)
    history = []
    for audit in audits[1:7]:
        if not audit.week_start or not audit.week_end:
            continue
        week = reporting_week_for(audit.week_start)
        if audit.week_end != week.end:
            continue
        totals = _totals(rows, week.start, week.end)
        observed = round(
            float(totals["research_minutes"]) + float(totals["study_minutes"]), 2
        )
        application_credit = float(totals["applications_minutes"])
        history.append({
            "week_start": week.start.isoformat(),
            "week_end": week.end.isoformat(),
            "audit": {
                "audit_id": audit.audit_id,
                "generated_at": str(audit.generated_at or "") or None,
                "summary": audit.summary,
                "strongest_achievement": audit.strongest_achievement,
                "main_failure_pattern": audit.main_failure_pattern,
                "next_priorities": audit.next_priorities,
                "exact_next_action": audit.exact_next_action,
                "effort_basis": audit.effort_basis,
                "tasks_done": audit.tasks_done,
                "tasks_assigned": audit.tasks_assigned,
            },
            "workload": {
                **totals,
                "observed_focus_minutes": observed,
                "combined_workload_credit_minutes": round(observed + application_credit, 2),
            },
            "telemetry": {
                "coverage": _week_coverage(status, week),
                "coverage_through": status.get("coverage_through"),
            },
        })
    return history


def _weekly_tasks(projection: AgendaProjection, today: date) -> list[dict]:
    """Return the visible execution plan in scheduled order, not priority order."""
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    tasks = []
    for row in projection.tasks:
        status = row.status
        due = row.due
        assigned = row.assigned
        scheduled = due or assigned
        scheduled_this_week = bool(scheduled and week_start <= scheduled <= week_end)
        visible_open = status in OPEN_TASKS and (scheduled is None or scheduled <= week_end)
        if not (visible_open or (status == "Done" and scheduled_this_week)):
            continue
        tasks.append({
            "task_id": row.task_id,
            "title": row.title,
            "status": status,
            "campaign": row.campaign,
            "category": row.category,
            "priority": row.priority,
            "assigned": assigned.isoformat() if assigned else None,
            "due": due.isoformat() if due else None,
            "task_date": scheduled.isoformat() if scheduled else None,
            "days_to_due": (due - today).days if due else None,
            "estimated_minutes": row.estimated_minutes,
            "remaining_minutes": row.recorded_remaining_minutes,
            "blocker": row.blocker,
            "depends_on_task_id": row.depends_on_task_id,
        })
    tasks.sort(key=lambda row: (
        row["task_date"] is None,
        row["task_date"] or date.max.isoformat(),
        PRIORITY.get(str(row.get("priority") or ""), 9),
        str(row.get("task_id") or ""),
    ))
    return tasks


def _campaign_snapshot(projection: AgendaProjection, today: date) -> dict:
    applications = []
    for row in projection.applications:
        if row.status not in ACTIVE_APPLICATIONS:
            continue
        deadline = row.deadline
        applications.append({
            "application_id": row.application_id,
            "programme": row.programme,
            "institution": row.institution,
            "route": row.route,
            "cycle": row.cycle,
            "status": row.status,
            "funding_gate": row.funding_gate,
            "funding_status": row.funding_status,
            "deadline": deadline.isoformat() if deadline else None,
            "days_to_deadline": (deadline - today).days if deadline else None,
            "next_action": row.next_action,
            "priority": row.priority,
        })
    applications.sort(key=lambda row: (
        row["days_to_deadline"] is None,
        row["days_to_deadline"] if row["days_to_deadline"] is not None else 99999,
        str(row.get("institution") or ""),
    ))

    tasks = _weekly_tasks(projection, today)
    upcoming = [row for row in applications if row["days_to_deadline"] is not None and row["days_to_deadline"] >= 0]
    overdue = [row for row in applications if row["days_to_deadline"] is not None and row["days_to_deadline"] < 0]
    nearest = upcoming[0] if upcoming else None
    deadline_pressure = {
        "nearest": nearest,
        "due_within_30_days": sum(1 for row in upcoming if row["days_to_deadline"] <= 30),
        "overdue_active": len(overdue),
        "undated_active": sum(1 for row in applications if row["days_to_deadline"] is None),
    }

    latest_audit = None
    if projection.latest_audit:
        row = projection.latest_audit
        latest_audit = {
            "audit_id": row.audit_id,
            "week_start": row.week_start.isoformat() if row.week_start else None,
            "week_end": row.week_end.isoformat() if row.week_end else None,
            "generated_at": str(row.generated_at or "") or None,
            "summary": row.summary,
            "strongest_achievement": row.strongest_achievement,
            "main_failure_pattern": row.main_failure_pattern,
            "next_priorities": row.next_priorities,
            "exact_next_action": row.exact_next_action,
            "effort_basis": row.effort_basis,
            "tasks_done": row.tasks_done,
            "tasks_assigned": row.tasks_assigned,
        }
    return {
        "applications": applications,
        "weekly_plan": tasks,
        "deadline_pressure": deadline_pressure,
        "latest_weekly_audit": latest_audit,
    }


def dashboard_snapshot(*, worklog_path: Path, status_path: Path,
                       agenda_projection: AgendaProjection,
                       now: datetime | None = None) -> dict:
    now = (now or datetime.now(IST)).astimezone(IST)
    today = operational_date(now)
    calendar_today = now.date()
    publication = read_publication(worklog_path, status_path, retries=1)
    rows = publication.rows
    status = publication.status
    month_start = today.replace(day=1)
    reporting_week = reporting_week_for(calendar_today)
    previous_week = previous_reporting_week(calendar_today)
    series_start, series_end = _series_window(rows, today)
    week_totals = _totals(rows, reporting_week.start, min(today, reporting_week.end))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "operational_date": today.isoformat(),
        "status": {**status, **_freshness(status, now)},
        "today": _totals(rows, today, today),
        "week": week_totals,
        "weekly_distribution": _weekly_distribution(week_totals),
        "previous_week": _totals(rows, previous_week.start, previous_week.end),
        "month": _totals(rows, month_start, today),
        "daily_series": _series(rows, series_end, (series_end - series_start).days + 1),
        "series_coverage": {
            "start": series_start.isoformat(),
            "end": series_end.isoformat(),
            "days": (series_end - series_start).days + 1,
            "anchor": "Forest operational dates" if any(str(row.get("source") or "") == "Forest" for row in rows) else "available worklog dates",
        },
        "campaign": _campaign_snapshot(agenda_projection, calendar_today),
        "weekly_history": _weekly_history(agenda_projection, rows, status),
    }


def compose_dashboard(*, environment: ProjectEnvironment, worklog_path: Path,
                      status_path: Path, now: datetime | None = None) -> dict:
    core = AnissaCore(
        environment,
        gateway=WorkbookGateway(environment=environment),
    )
    return dashboard_snapshot(
        worklog_path=worklog_path,
        status_path=status_path,
        agenda_projection=core.projection("dashboard"),
        now=now,
    )
