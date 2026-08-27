from __future__ import annotations

from datetime import date, datetime, time, timedelta
from hashlib import sha1
from typing import Iterable
from zoneinfo import ZoneInfo

from project.projections import CompletedTaskCreditProjection


IST = ZoneInfo("Asia/Kolkata")
BOUNDARY = time(20, 0)


def parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp is required")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def operational_date(moment: object) -> date:
    local = parse_datetime(moment)
    return local.date() + timedelta(days=1 if local.time() >= BOUNDARY else 0)


def window_for(day: date) -> tuple[datetime, datetime]:
    end = datetime.combine(day, BOUNDARY, tzinfo=IST)
    return end - timedelta(days=1), end


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part or "").strip().lower() for part in parts)
    return f"{prefix}_{sha1(payload.encode('utf-8')).hexdigest()[:16]}"


def category_for_tag(tag: object) -> str:
    normalized = " ".join(str(tag or "").strip().lower().split())
    if normalized == "work":
        return "Research"
    if normalized in {"study", "unset"}:
        return "Study"
    return "Other"


def split_forest_session(session: dict) -> list[dict]:
    start = parse_datetime(session.get("start_at"))
    end = parse_datetime(session.get("end_at"))
    if end <= start:
        raise ValueError("Forest session end must be after start")
    source_id = str(session.get("id") or "").strip()
    tag = str(session.get("tag") or "").strip()
    if not source_id:
        source_id = _stable_id("forest_session", start.isoformat(), end.isoformat(), tag)

    rows: list[dict] = []
    cursor = start
    while cursor < end:
        day = operational_date(cursor)
        _, boundary = window_for(day)
        slice_end = min(end, boundary)
        seconds = (slice_end - cursor).total_seconds()
        minutes = round(seconds / 60.0, 3)
        record_id = _stable_id("a2a_forest", source_id, day.isoformat(), cursor.isoformat(), slice_end.isoformat())
        rows.append({
            "record_id": record_id,
            "operational_date": day.isoformat(),
            "window_start": window_for(day)[0].isoformat(),
            "window_end": boundary.isoformat(),
            "category": category_for_tag(tag),
            "minutes": minutes,
            "source": "Forest",
            "source_record_id": source_id,
            "source_tag": tag,
            "basis": "actual",
            "started_at": cursor.isoformat(),
            "ended_at": slice_end.isoformat(),
        })
        cursor = slice_end
    return rows


def completed_task_entry(credit: CompletedTaskCreditProjection) -> dict:
    task_id = credit.task_id
    minutes = round(max(0.0, float(credit.minutes)), 3)
    if minutes <= 0:
        raise ValueError(f"Completed-task credit must be positive for {task_id}")
    if credit.basis not in {"actual", "estimated_proxy"}:
        raise ValueError(f"Completed-task credit basis is invalid for {task_id}")
    completed_at = parse_datetime(credit.completed_at)
    day = operational_date(completed_at)
    start, end = window_for(day)
    return {
        "record_id": _stable_id("a2a_task", task_id),
        "operational_date": day.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "category": "Applications",
        "minutes": minutes,
        "source": "Anissa Task",
        "source_record_id": task_id,
        "source_tag": "",
        "basis": credit.basis,
        "started_at": "",
        "ended_at": completed_at.isoformat(),
    }


def build_worklog(
    forest_sessions: Iterable[dict],
    completed_task_credit: Iterable[CompletedTaskCreditProjection],
) -> list[dict]:
    indexed: dict[str, dict] = {}
    for session in forest_sessions:
        for row in split_forest_session(session):
            indexed[row["record_id"]] = row
    for credit in completed_task_credit:
        row = completed_task_entry(credit)
        indexed[row["record_id"]] = row
    return sorted(indexed.values(), key=lambda row: (
        row["operational_date"], row["category"], row["source"], row["record_id"]
    ))
