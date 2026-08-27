from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import io
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
BOUNDARY = time(20, 0)
FIELDS = (
    "record_id", "operational_date", "window_start", "window_end", "category",
    "minutes", "source", "source_record_id", "source_tag", "basis",
    "started_at", "ended_at",
)
ALLOWED_KINDS = {
    ("Forest", "Research", "actual"),
    ("Forest", "Study", "actual"),
    ("Forest", "Other", "actual"),
    ("Anissa Task", "Applications", "actual"),
    ("Anissa Task", "Applications", "estimated_proxy"),
}


class TelemetryContractError(ValueError):
    """A published A2A worklog/status pair is not safe to consume."""


@dataclass(frozen=True)
class TelemetryPublication:
    rows: list[dict]
    status: dict


def parse_moment(value: object, field: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryContractError(f"Telemetry {field} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _validate_row(row: dict, index: int) -> dict:
    missing = [field for field in FIELDS if field not in row]
    if missing:
        raise TelemetryContractError(
            f"Telemetry row {index} is missing fields: {', '.join(missing)}"
        )
    record_id = str(row.get("record_id") or "").strip()
    source_record_id = str(row.get("source_record_id") or "").strip()
    if not record_id or not source_record_id:
        raise TelemetryContractError(f"Telemetry row {index} has no stable identity")
    kind = (
        str(row.get("source") or "").strip(),
        str(row.get("category") or "").strip(),
        str(row.get("basis") or "").strip(),
    )
    if kind not in ALLOWED_KINDS:
        raise TelemetryContractError(f"Telemetry row {index} has an unsupported source/category/basis")
    try:
        operational_day = date.fromisoformat(str(row.get("operational_date") or ""))
        minutes = float(row.get("minutes"))
    except (TypeError, ValueError) as exc:
        raise TelemetryContractError(f"Telemetry row {index} has invalid date or minutes") from exc
    if not math.isfinite(minutes) or minutes <= 0:
        raise TelemetryContractError(f"Telemetry row {index} minutes must be finite and positive")

    window_start = parse_moment(row.get("window_start"), f"row {index} window_start")
    window_end = parse_moment(row.get("window_end"), f"row {index} window_end")
    expected_end = datetime.combine(operational_day, BOUNDARY, tzinfo=IST)
    if window_end != expected_end or window_start != expected_end - timedelta(days=1):
        raise TelemetryContractError(f"Telemetry row {index} has an invalid operational window")

    ended_at = parse_moment(row.get("ended_at"), f"row {index} ended_at")
    if not window_start <= ended_at <= window_end:
        raise TelemetryContractError(f"Telemetry row {index} completion lies outside its window")
    started_text = str(row.get("started_at") or "").strip()
    if kind[0] == "Forest":
        if not started_text:
            raise TelemetryContractError(f"Telemetry Forest row {index} has no start time")
        started_at = parse_moment(started_text, f"row {index} started_at")
        if not window_start <= started_at < ended_at <= window_end:
            raise TelemetryContractError(f"Telemetry Forest row {index} lies outside its window")
        observed_minutes = (ended_at - started_at).total_seconds() / 60
        if abs(observed_minutes - minutes) > 0.01:
            raise TelemetryContractError(f"Telemetry Forest row {index} minutes disagree with its timestamps")
    elif started_text:
        started_at = parse_moment(started_text, f"row {index} started_at")
        if not window_start <= started_at <= ended_at:
            raise TelemetryContractError(f"Telemetry task row {index} has invalid timing")
    return {**row, "minutes": minutes}


def decode_worklog(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TelemetryContractError("A2A worklog must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != list(FIELDS):
        raise TelemetryContractError("A2A worklog fields do not match the contract")
    rows = [_validate_row(row, index) for index, row in enumerate(reader, start=2)]
    ids = [str(row["record_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise TelemetryContractError("A2A worklog contains duplicate record IDs")
    return rows


def validate_rows(rows: list[dict]) -> list[dict]:
    """Validate rows before publication using the same contract as consumers."""
    normalized = [_validate_row(dict(row), index) for index, row in enumerate(rows, start=2)]
    ids = [str(row["record_id"]) for row in normalized]
    if len(ids) != len(set(ids)):
        raise TelemetryContractError("A2A worklog contains duplicate record IDs")
    return normalized


def operational_date(moment: object) -> date:
    local = parse_moment(moment)
    return local.date() + timedelta(days=1 if local.time() >= BOUNDARY else 0)


def read_publication(worklog_path: Path, status_path: Path, *, retries: int = 1) -> TelemetryPublication:
    """Read one checksum-coherent publication, retrying a concurrent handoff once."""
    last_error: Exception | None = None
    for _ in range(max(0, retries) + 1):
        try:
            status = json.loads(Path(status_path).read_text(encoding="utf-8"))
            raw = Path(worklog_path).read_bytes()
            expected = str(status.get("worklog_sha256") or "")
            if not expected or sha256(raw).hexdigest() != expected:
                raise TelemetryContractError("A2A worklog checksum does not match its status")
            rows = decode_worklog(raw)
            try:
                expected_rows = int(status.get("row_count"))
            except (TypeError, ValueError) as exc:
                raise TelemetryContractError("A2A status has no valid row count") from exc
            if expected_rows != len(rows):
                raise TelemetryContractError("A2A worklog row count does not match its status")
            return TelemetryPublication(rows=rows, status=status)
        except (OSError, ValueError, TypeError) as exc:
            last_error = exc
    if isinstance(last_error, TelemetryContractError):
        raise last_error
    raise TelemetryContractError(
        f"A2A telemetry publication is unreadable: {type(last_error).__name__}"
    ) from last_error
