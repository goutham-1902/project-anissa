from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from worker1.src.accounting import build_worklog, parse_datetime
from worker1.src.publisher import publish_status, publish_worklog, read_status
from worker1.src.store import StateStore


def validate_extraction(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Forest extraction must be an envelope object")
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported Forest extraction schema")
    if str(payload.get("extraction_status") or "").upper() != "COMPLETE":
        raise ValueError("Forest extraction is not COMPLETE; refusing to record zero")
    range_start = parse_datetime(payload.get("range_start"))
    range_end = parse_datetime(payload.get("range_end"))
    if range_end <= range_start:
        raise ValueError("Extraction range_end must be after range_start")
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("Forest extraction sessions must be a list")
    normalized = []
    source_ids = set()
    for index, session in enumerate(sessions):
        if not isinstance(session, dict):
            raise ValueError(f"Forest session {index} is not an object")
        start = parse_datetime(session.get("start_at"))
        end = parse_datetime(session.get("end_at"))
        if end <= start:
            raise ValueError(f"Forest session {index} has invalid timing")
        if start < range_start or end > range_end:
            raise ValueError(f"Forest session {index} lies outside the extraction range")
        source_id = str(session.get("id") or "").strip()
        if not source_id:
            from worker1.src.accounting import _stable_id
            source_id = _stable_id("forest_session", start.isoformat(), end.isoformat(), session.get("tag"))
        if source_id in source_ids:
            raise ValueError(f"Forest session {index} duplicates source ID {source_id}")
        source_ids.add(source_id)
        normalized.append({
            "id": source_id,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "tag": str(session.get("tag") or ""),
        })
    normalized.sort(key=lambda item: (item["start_at"], item["end_at"], item["id"]))
    for previous, current in zip(normalized, normalized[1:]):
        if parse_datetime(current["start_at"]) < parse_datetime(previous["end_at"]):
            raise ValueError(
                f"Forest sessions {previous['id']} and {current['id']} overlap"
            )
    return {
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "sessions": normalized,
    }


def run_sync(*, payload: dict, store_path: Path, worklog_path: Path,
             status_path: Path, completed_task_credit: tuple,
             now: datetime | None = None,
             source_metadata: dict | None = None) -> dict:
    now = now or datetime.now().astimezone()
    attempt_at = now.isoformat(timespec="seconds")
    validated = validate_extraction(payload)
    store = StateStore(store_path)
    result = store.import_sessions(
        validated["sessions"], seen_at=attempt_at,
        range_start=validated["range_start"], range_end=validated["range_end"],
    )
    store.add_coverage(validated["range_start"], validated["range_end"], attempt_at)

    rows = build_worklog(store.sessions(), completed_task_credit)
    digest = publish_worklog(worklog_path, rows)
    coverage = store.coverage_summary()
    status = {
        "schema_version": 1,
        "state": "COMPLETE",
        "last_attempt": attempt_at,
        "last_success": attempt_at,
        "coverage_start": coverage["start"],
        "coverage_through": coverage["through"],
        "coverage_intervals": coverage["intervals"],
        "coverage_has_gaps": coverage["has_gaps"],
        "row_count": len(rows),
        "worklog_sha256": digest,
        "last_error": None,
    }
    if source_metadata:
        status["source"] = source_metadata
    publish_status(status_path, status)
    store.set("last_success", status)
    return {**status, **result}


def record_failure(*, status_path: Path, stage: str, message: str,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    previous = read_status(status_path)
    status = {
        **previous,
        "schema_version": 1,
        "state": "FAILED",
        "last_attempt": now.isoformat(timespec="seconds"),
        "last_error": {"stage": stage, "message": message},
    }
    publish_status(status_path, status)
    return status


def record_stale(*, status_path: Path, stage: str, message: str,
                 now: datetime | None = None) -> dict:
    """Record an expected missing refresh without touching the last good work log."""
    now = now or datetime.now().astimezone()
    previous = read_status(status_path)
    status = {
        **previous,
        "schema_version": 1,
        "state": "STALE",
        "last_attempt": now.isoformat(timespec="seconds"),
        "last_error": None,
        "stale": {"stage": stage, "message": message},
    }
    publish_status(status_path, status)
    return status


def run_csv_sync(*, csv_data: bytes | str, captured_at: object,
                 source_file_id: str, source_modified_at: str,
                 store_path: Path, worklog_path: Path, status_path: Path,
                 completed_task_credit: tuple,
                 now: datetime | None = None) -> dict:
    """Import a Drive-hosted Forest export, or mark an unchanged export stale."""
    from hashlib import sha256
    from worker1.src.forest_csv import parse_forest_csv

    now = now or datetime.now().astimezone()
    previous = read_status(status_path)
    previous_source = previous.get("source") or {}
    identity = {
        "kind": "google_drive_forest_csv",
        "file_id": str(source_file_id or "").strip(),
        "modified_at": parse_datetime(source_modified_at).isoformat(),
    }
    if (previous_source.get("file_id") == identity["file_id"] and
            previous_source.get("modified_at") == identity["modified_at"]):
        return record_stale(
            status_path=status_path,
            stage="drive",
            message="No newer Forest export was available; the next full export will backfill missed days.",
            now=now,
        )

    raw = csv_data.encode("utf-8") if isinstance(csv_data, str) else csv_data
    payload = parse_forest_csv(raw, captured_at=captured_at)
    identity["sha256"] = sha256(raw).hexdigest()
    return run_sync(
        payload=payload,
        store_path=store_path,
        worklog_path=worklog_path,
        status_path=status_path,
        completed_task_credit=completed_task_credit,
        now=now,
        source_metadata=identity,
    )


def load_payload(path: str) -> dict:
    if path == "-":
        import sys
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))
