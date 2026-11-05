"""Pure, lossless planning groups over canonical application and task rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
import json

from logic.ids import norm_text
from logic.workload import task_remaining_minutes


_OPEN_TASKS = {"Not Started", "Started", "Blocked"}
_ACTIONABLE_APPLICATIONS = {"Researching", "Preparing", "Applying"}
_PRIORITY = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}
_CLASSIFICATION = ("country", "programme_family", "application_mechanism")


def _required_id(row: dict, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _deadline(value: object) -> tuple[str | None, date | None, bool]:
    if value in (None, ""):
        return None, None, False
    if isinstance(value, datetime):
        return value.isoformat(), value.date(), False
    if isinstance(value, date):
        return value.isoformat(), value, False
    raw = str(value).strip()
    if not raw:
        return None, None, False
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return raw, None, True
    return raw, parsed, False


def _text(value: object) -> str:
    return str(value or "").strip()


def _bucket_id(*parts: str) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "bucket_" + sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _task(row: dict, *, as_of: date) -> dict:
    due_raw, due, due_unverified = _deadline(row.get("Due Date"))
    assigned_raw, _, _ = _deadline(row.get("Assigned Date"))
    status = _text(row.get("Status"))
    return {
        "task_id": _required_id(row, "Task ID"),
        "application_id": _text(row.get("Application ID")),
        "title": _text(row.get("Task Title")),
        "status": status,
        "priority": _text(row.get("Priority")),
        "assigned": assigned_raw,
        "due": due_raw,
        "due_date": due.isoformat() if due else None,
        "due_needs_verification": due_unverified,
        "days_to_due": (due - as_of).days if due else None,
        "blocker": _text(row.get("Blocker")),
        "unblock_action": _text(row.get("Unblock Action")),
        "evidence": _text(row.get("Evidence")),
        "definition_of_done": _text(row.get("Definition of Done")),
        "estimated_minutes": row.get("Estimated Minutes"),
        "actual_minutes": row.get("Actual Minutes"),
        "remaining_minutes": task_remaining_minutes(row),
        "depends_on_task_id": _text(row.get("Depends On Task ID")),
    }


def _application(row: dict, *, as_of: date) -> dict:
    deadline_raw, deadline, deadline_unverified = _deadline(row.get("Deadline"))
    return {
        "application_id": _required_id(row, "Application ID"),
        "programme": _text(row.get("Programme / Position")),
        "institution": _text(row.get("Institution")),
        "status": _text(row.get("Status")),
        "deadline": deadline_raw,
        "deadline_date": deadline.isoformat() if deadline else None,
        "deadline_needs_verification": deadline_unverified,
        "days_to_deadline": (deadline - as_of).days if deadline else None,
        "next_action": _text(row.get("Next Action")),
        "funding_gate": _text(row.get("Funding Gate")),
        "funding_status": _text(row.get("Funding Status")),
    }


def _task_order(row: dict) -> tuple:
    return (
        row["status"] not in _OPEN_TASKS,
        row["due_date"] or "9999-12-31",
        _PRIORITY.get(row["priority"], 9),
        row["task_id"],
    )


def _waves(applications: list[dict], tasks: list[dict]) -> tuple[list[dict], str | None]:
    weeks: dict[tuple[int, int], dict[str, set[str]]] = defaultdict(
        lambda: {"application_ids": set(), "task_ids": set()}
    )
    deadlines = []
    for application in applications:
        if application["status"] in _ACTIONABLE_APPLICATIONS and application["deadline_date"]:
            day = date.fromisoformat(application["deadline_date"])
            year, week, _ = day.isocalendar()
            weeks[(year, week)]["application_ids"].add(application["application_id"])
            deadlines.append(day)
    for task in tasks:
        if task["status"] in _OPEN_TASKS and task["due_date"]:
            day = date.fromisoformat(task["due_date"])
            year, week, _ = day.isocalendar()
            weeks[(year, week)]["task_ids"].add(task["task_id"])
            deadlines.append(day)
    waves = []
    for (year, week), members in sorted(weeks.items()):
        monday = date.fromisocalendar(year, week, 1)
        waves.append({
            "iso_week": f"{year}-W{week:02d}",
            "week_start": monday.isoformat(),
            "week_end": (monday + timedelta(days=6)).isoformat(),
            "application_ids": sorted(members["application_ids"]),
            "task_ids": sorted(members["task_ids"]),
        })
    return waves, min(deadlines).isoformat() if deadlines else None


def build_application_buckets(
    applications: list[dict],
    tasks: list[dict],
    metadata: dict,
    *,
    as_of: date,
) -> dict:
    """Group only complete explicit classifications; never infer a merge.

    Each task appears once: in its referenced application's bucket or in the
    cross-cutting/orphan list. Deadlines determine ordering, never bucket IDs.
    """

    if not isinstance(applications, list) or not isinstance(tasks, list):
        raise TypeError("Applications and tasks must be lists")
    if not isinstance(metadata, dict):
        raise TypeError("Application bucket metadata must be a mapping")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise TypeError("as_of must be a calendar date")
    if any(not isinstance(row, dict) for row in applications + tasks):
        raise TypeError("Application and task rows must be mappings")
    for application_id, classification in metadata.items():
        if not isinstance(application_id, str) or not application_id.strip():
            raise ValueError("Application classification IDs must be non-empty strings")
        if not isinstance(classification, dict):
            raise TypeError("Application classification must be a mapping")
        if any(
            key in classification and not isinstance(classification[key], str)
            for key in _CLASSIFICATION
        ):
            raise TypeError("Application classification values must be strings")

    grouped: dict[str, dict] = {}
    app_to_bucket: dict[str, str] = {}
    for row in sorted(applications, key=lambda item: _required_id(item, "Application ID")):
        application = _application(row, as_of=as_of)
        application_id = application["application_id"]
        if application_id in app_to_bucket:
            raise ValueError(f"Duplicate Application ID: {application_id}")
        classification = metadata.get(application_id, {})
        labels = {key: _text(classification.get(key)) for key in _CLASSIFICATION}
        complete = all(labels.values())
        bucket_id = (
            _bucket_id("classified", *(norm_text(labels[key]) for key in _CLASSIFICATION))
            if complete else _bucket_id("unclassified", application_id)
        )
        bucket = grouped.setdefault(bucket_id, {
            "bucket_id": bucket_id,
            **labels,
            "needs_classification": not complete,
            "applications": [],
            "tasks": [],
        })
        bucket["applications"].append(application)
        app_to_bucket[application_id] = bucket_id

    seen_tasks = set()
    unassigned_tasks = []
    for row in sorted(tasks, key=lambda item: _required_id(item, "Task ID")):
        task = _task(row, as_of=as_of)
        task_id = task["task_id"]
        if task_id in seen_tasks:
            raise ValueError(f"Duplicate Task ID: {task_id}")
        seen_tasks.add(task_id)
        bucket_id = app_to_bucket.get(task["application_id"])
        if bucket_id is None:
            unassigned_tasks.append(task)
        else:
            grouped[bucket_id]["tasks"].append(task)

    buckets = []
    for bucket in grouped.values():
        bucket["applications"].sort(key=lambda row: (
            row["deadline_date"] or "9999-12-31", row["application_id"]
        ))
        bucket["tasks"].sort(key=_task_order)
        waves, earliest = _waves(bucket["applications"], bucket["tasks"])
        bucket["deadline_waves"] = waves
        bucket["earliest_actionable_deadline"] = earliest
        bucket["actionable"] = any(
            row["status"] in _ACTIONABLE_APPLICATIONS for row in bucket["applications"]
        ) or any(row["status"] in _OPEN_TASKS for row in bucket["tasks"])
        bucket["remaining_task_minutes"] = sum(
            row["remaining_minutes"] for row in bucket["tasks"]
        )
        buckets.append(bucket)
    buckets.sort(key=lambda row: (
        not row["actionable"],
        row["earliest_actionable_deadline"] or "9999-12-31", row["bucket_id"]
    ))
    unassigned_tasks.sort(key=_task_order)
    return {"buckets": buckets, "unassigned_tasks": unassigned_tasks}
