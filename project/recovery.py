"""Select useful scheduled work after a gap without replaying expired runs."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from project.telemetry_contract import IST


SLOT_TIMES = {
    "weekday-morning": time(8), "weekday-reminder": time(14), "weekday-close": time(21, 30),
    "weekend-morning": time(8), "weekend-reminder-sat-17": time(17),
    "weekend-reminder-sun-11": time(11), "weekend-reminder-sun-15": time(15),
    "weekly-audit": time(21, 30), "weekly-audit-recovery": time(8), "thula-ingestion": time(21),
}


def role_for_slot(slot: str) -> str | None:
    if slot.startswith("weekday-"):
        return "WEEKDAY_OPS"
    if slot.startswith(("weekend-", "weekly-audit")):
        return "WEEKEND"
    return "THULA" if slot == "thula-ingestion" else None


def _local(now: datetime) -> datetime:
    return now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)


def latest_audit_due_week_end(now: datetime) -> date:
    now = _local(now)
    sunday = now.date() - timedelta(days=(now.weekday() + 1) % 7)
    if now.weekday() == 6 and now.time() < time(21, 30):
        sunday -= timedelta(days=7)
    return sunday


def plan_recovery(role: str, *, now: datetime, receipts: dict, state: dict | None = None) -> dict:
    now = _local(now)
    state = state or {}
    rows = receipts.get("slots", {})
    relevant = {key: row for key, row in rows.items() if role_for_slot(row["slot"]) == role}
    delivered = [row.get("delivered_at") or row.get("completed_at")
                 for row in relevant.values()
                 if row.get("delivery_status") == "delivered"
                 or (row.get("status") == "completed" and "delivery_status" not in row)]
    since = (receipts.get("coverage", {}).get(role, {}).get("last_delivered_at")
             or max((value for value in delivered if value), default=None))
    result = {"role": role, "as_of": now.isoformat(), "since": since,
              "actions": [], "superseded_ids": [], "message_limit": 1}
    if state.get("blocked"):
        return {**result, "blocked": True}
    candidates = []

    def add(slot, day, clock, workflow, allow_new_tasks=False):
        due = datetime.combine(day, clock, IST)
        if due <= now:
            candidates.append({"slot": slot, "run_date": day.isoformat(),
                               "workflow": workflow, "allow_new_tasks": allow_new_tasks,
                               "due_at": due.isoformat()})

    today = now.date()
    if role == "THULA":
        day = today if now.time() >= time(21) else today - timedelta(days=1)
        add("thula-ingestion", day, time(21), "thula-ingestion")
    elif role == "WEEKDAY_OPS":
        if now.weekday() < 5 and time(8) <= now.time() < time(21, 30):
            add("weekday-morning", today, time(8), "weekday-morning", True)
            add("weekday-reminder", today, time(14), "weekday-reminder")
        else:
            day = today if now.weekday() < 5 and now.time() >= time(21, 30) else today - timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            add("weekday-close", day, time(21, 30), "weekday-close")
    elif role == "WEEKEND":
        audit_end = latest_audit_due_week_end(now)
        add("weekly-audit", audit_end, time(21, 30), "weekly-audit")
        if now.weekday() in (5, 6) and not (now.weekday() == 6 and now.time() >= time(21, 30)):
            saturday = today - timedelta(days=now.weekday() - 5)
            if now.weekday() == 5:
                add("weekend-morning", saturday, time(8), "weekend-morning", True)
            reminders = [(saturday, time(17), "weekend-reminder-sat-17"),
                         (saturday + timedelta(days=1), time(11), "weekend-reminder-sun-11"),
                         (saturday + timedelta(days=1), time(15), "weekend-reminder-sun-15")]
            eligible = [(day, clock, slot) for day, clock, slot in reminders
                        if datetime.combine(day, clock, IST) <= now]
            if eligible:
                day, clock, slot = eligible[-1]
                add(slot, day, clock, "weekend-reminder")
    else:
        raise ValueError("Recovery role must be WEEKDAY_OPS, WEEKEND or THULA")

    for candidate in candidates:
        matching = next(((key, row) for key, row in relevant.items()
                         if row["slot"] == candidate["slot"] and row["run_date"] == candidate["run_date"]), None)
        record = matching[1] if matching else {}
        lease = record.get("lease_expires_at") or record.get("delivery_lease_expires_at")
        if lease and datetime.fromisoformat(lease) > now:
            return {**result, "busy": True}
        pending = record.get("delivery_status") == "pending"
        if record.get("status") in ("completed", "superseded") and not pending:
            continue
        if candidate["workflow"] == "weekly-audit" and state.get("audit_recorded") and not pending:
            continue
        if candidate["workflow"] in ("weekday-reminder", "weekend-reminder"):
            opening = "weekday-morning" if role == "WEEKDAY_OPS" else "weekend-morning"
            if any(row["slot"] == opening and row.get("completed_at", "") > candidate["due_at"]
                   and row["run_date"] == candidate["run_date"] for row in relevant.values()):
                continue
        candidate["action"] = "delivery" if pending else "run"
        if pending:
            candidate["allow_new_tasks"] = False
        result["actions"].append(candidate)
        if role in ("WEEKDAY_OPS", "THULA") or candidate["workflow"] != "weekly-audit":
            break
    selected = {(row["slot"], row["run_date"]) for row in result["actions"]}
    if selected:
        latest = max(datetime.fromisoformat(row["due_at"]) for row in result["actions"])
        result["superseded_ids"] = [key for key, row in relevant.items()
                                    if (row["slot"], row["run_date"]) not in selected
                                    and row["slot"] in SLOT_TIMES
                                    and datetime.combine(date.fromisoformat(row["run_date"]), SLOT_TIMES[row["slot"]], IST) <= latest
                                    and (row.get("delivery_status") == "pending" or row.get("status") == "failed")]
    for action in result["actions"]:
        if action["workflow"] == "weekday-morning":
            action["remaining_window_minutes"] = max(
                0, int((datetime.combine(today, time(21, 30), IST) - now).total_seconds() // 60)
            )
            action["allow_new_tasks"] &= action["remaining_window_minutes"] >= 15
    return result
