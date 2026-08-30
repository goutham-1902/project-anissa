from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from project.environment import resolve_environment
from project.reporting_week import reporting_week_for
from project.telemetry_contract import TelemetryContractError, read_publication


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = resolve_environment(ROOT)
SHARED_ROOT = ENVIRONMENT.telemetry_root
IST = ZoneInfo("Asia/Kolkata")
BOUNDARY = time(20, 0)


def _parse_moment(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _operational_date(moment: datetime) -> date:
    local = moment.astimezone(IST)
    return local.date() + timedelta(days=1 if local.time() >= BOUNDARY else 0)


def _empty_period(start: date, end: date) -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "observed_focus_minutes": 0.0,
        "research_minutes": 0.0,
        "study_minutes": 0.0,
        "application_credit_minutes": 0.0,
        "application_actual_minutes": 0.0,
        "application_estimated_proxy_minutes": 0.0,
        "combined_workload_credit_minutes": 0.0,
        "active_days": 0,
    }


def _period(rows: list[dict], start: date, end: date) -> dict:
    result = _empty_period(start, end)
    active_days: set[date] = set()
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("operational_date") or ""))
            minutes = float(row.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
        if minutes < 0 or not start <= day <= end:
            continue
        source = str(row.get("source") or "")
        category = str(row.get("category") or "")
        basis = str(row.get("basis") or "")
        if source == "Forest":
            if category == "Research":
                result["research_minutes"] += minutes
            elif category == "Study":
                result["study_minutes"] += minutes
            else:
                continue
            result["observed_focus_minutes"] += minutes
        elif source == "Anissa Task" and category == "Applications":
            result["application_credit_minutes"] += minutes
            if basis == "actual":
                result["application_actual_minutes"] += minutes
            else:
                result["application_estimated_proxy_minutes"] += minutes
        else:
            continue
        if minutes > 0:
            active_days.add(day)
    for key, value in list(result.items()):
        if key.endswith("_minutes"):
            result[key] = round(float(value), 2)
    result["combined_workload_credit_minutes"] = round(
        result["observed_focus_minutes"] + result["application_credit_minutes"], 2
    )
    result["active_days"] = len(active_days)
    return result


def _unavailable(reason: str, *, state: str = "UNKNOWN") -> dict:
    return {
        "schema_version": 1,
        "availability": "unavailable",
        "data_policy": "ignore",
        "source_state": state,
        "freshness": "unavailable",
        "coverage_through": None,
        "guardrail": "ignore",
        "reason": reason,
        "behavior": {
            "workload_band": "unknown",
            "materially_above_normal": False,
            "positive_evidence_only": True,
        },
    }


def _read_contract(shared_root: Path, now: datetime) -> tuple[list[dict], dict] | dict:
    try:
        schema = json.loads((shared_root / "schema.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return _unavailable(f"A2A telemetry contract is unreadable: {exc.__class__.__name__}")
    try:
        publication = read_publication(
            shared_root / "worklog.csv", shared_root / "status.json", retries=1
        )
    except TelemetryContractError as exc:
        return _unavailable(f"A2A telemetry is invalid: {exc}")
    status = publication.status
    state = str(status.get("state") or "UNKNOWN").upper()
    if int(schema.get("version") or 0) != 1 or int(status.get("schema_version") or 0) != 1:
        return _unavailable("A2A telemetry schema version is unsupported", state=state)
    if status.get("coverage_has_gaps") is True:
        return _unavailable("A2A telemetry coverage has gaps", state=state)

    try:
        rows = publication.rows
        coverage = _parse_moment(status.get("coverage_through"))
    except (ValueError, TypeError) as exc:
        return _unavailable(f"A2A telemetry is invalid: {exc}", state=state)
    age_hours = max(0.0, (now - coverage).total_seconds() / 3600)
    if state == "COMPLETE" and age_hours <= 28:
        policy, freshness = "full", "fresh"
    elif state in {"COMPLETE", "STALE", "FAILED"} and age_hours <= 52:
        policy, freshness = "positive_only", "recent_stale"
    else:
        return _unavailable("A2A telemetry is too old or incomplete for judgment", state=state)
    return rows, {
        "schema_version": 1,
        "availability": "available",
        "data_policy": policy,
        "source_state": state,
        "freshness": freshness,
        "coverage_through": coverage.isoformat(),
        "age_hours": round(age_hours, 1),
        "guardrail": "none" if policy == "full" else "positive_only",
    }


def _compact_period(period: dict, *, detailed: bool = False) -> dict:
    keys = [
        "start", "end", "observed_focus_minutes", "application_credit_minutes",
        "application_estimated_proxy_minutes", "combined_workload_credit_minutes",
    ]
    if detailed:
        keys.extend(("research_minutes", "study_minutes", "application_actual_minutes", "active_days"))
    return {key: period[key] for key in keys}


def telemetry_context(workflow: str, *, shared_root: Path = SHARED_ROOT,
                      now: datetime | None = None) -> dict:
    """Return the only compact A2A context Anissa is allowed to consume."""
    now = (now or datetime.now(IST)).astimezone(IST)
    loaded = _read_contract(Path(shared_root), now)
    if isinstance(loaded, dict):
        return loaded
    rows, base = loaded

    current = _operational_date(now)
    calendar_today = now.date()
    week_end = calendar_today if workflow == "weekly-audit" else current
    week_start = reporting_week_for(week_end).start
    today = _period(rows, current, current)
    recent_day = _period(rows, current - timedelta(days=1), current - timedelta(days=1))
    week = _period(rows, week_start, week_end)
    if current.weekday() < 5:
        comparison = max(
            (today, recent_day),
            key=lambda period: period["combined_workload_credit_minutes"],
        )
        threshold = 135
    else:
        saturday = current - timedelta(days=(current.weekday() - 5) % 7)
        comparison = _period(rows, saturday, saturday + timedelta(days=1))
        threshold = 360
    credit = comparison["combined_workload_credit_minutes"]
    materially_above = credit >= threshold
    if credit >= threshold:
        band = "exceptional"
    elif credit >= 90:
        band = "strong"
    elif credit > 0:
        band = "light"
    else:
        band = "unknown"
    behavior = {
        "workload_band": band,
        "materially_above_normal": materially_above,
        "positive_evidence_only": True,
    }
    detailed = workflow == "weekly-audit"
    result = {
        **base,
        "behavior": behavior,
        "today": _compact_period(today, detailed=detailed),
        "recent_completed_day": _compact_period(recent_day, detailed=detailed),
    }
    if workflow not in {"weekday-reminder", "weekend-reminder"}:
        result["week"] = _compact_period(week, detailed=detailed)
    return result
