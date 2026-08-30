from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from project.telemetry_contract import BOUNDARY, IST


@dataclass(frozen=True)
class ReportingWeek:
    """One Monday-Sunday reporting week and its closed A2A telemetry window."""

    start: date
    end: date
    telemetry_start: datetime
    telemetry_end: datetime


def _local_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        local = value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
        return local.date()
    return value


def _local_moment(value: datetime) -> datetime:
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def reporting_week_for(value: date | datetime) -> ReportingWeek:
    day = _local_date(value)
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return ReportingWeek(
        start=start,
        end=end,
        telemetry_start=datetime.combine(start - timedelta(days=1), BOUNDARY, tzinfo=IST),
        telemetry_end=datetime.combine(end, BOUNDARY, tzinfo=IST),
    )


def previous_reporting_week(value: date | datetime) -> ReportingWeek:
    current = reporting_week_for(value)
    return reporting_week_for(current.start - timedelta(days=1))


def completed_reporting_week(moment: datetime) -> ReportingWeek:
    """Return the latest week whose Sunday 20:00 A2A window has closed."""

    local = _local_moment(moment)
    current = reporting_week_for(local)
    if local >= current.telemetry_end:
        return current
    return previous_reporting_week(current.start)
