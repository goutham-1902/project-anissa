from __future__ import annotations

import base64
import csv
from datetime import datetime
from hashlib import sha256
import io

from worker1.src.accounting import parse_datetime


HEADERS = ["Start Time", "End Time", "Tag", "Note", "Tree Type", "Is Success"]
FOREST_TIME_FORMAT = "%a %b %d %H:%M:%S GMT%z %Y"


def _forest_datetime(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Forest timestamp is required")
    try:
        return parse_datetime(datetime.strptime(text, FOREST_TIME_FORMAT))
    except ValueError as exc:
        raise ValueError(f"Unsupported Forest timestamp: {text!r}") from exc


def _source_id(start: datetime, end: datetime, tree_type: object) -> str:
    identity = "|".join((start.isoformat(), end.isoformat(), str(tree_type or "").strip().lower()))
    return f"forest_csv_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def decode_csv_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("Forest CSV payload is not valid base64") from exc


def parse_forest_csv(data: bytes | str, *, captured_at: object) -> dict:
    """Convert one complete Forest history export into the normalized A2A envelope."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Forest CSV must be UTF-8 encoded") from exc
    else:
        text = str(data).lstrip("\ufeff")

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != HEADERS:
        raise ValueError(f"Unexpected Forest CSV headers: {reader.fieldnames!r}")

    capture = parse_datetime(captured_at)
    sessions_by_id: dict[str, dict] = {}
    unsuccessful = 0
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"Forest CSV row {line_number} has extra columns")
        success = str(row.get("Is Success") or "").strip().lower()
        if success == "false":
            unsuccessful += 1
            continue
        if success != "true":
            raise ValueError(f"Forest CSV row {line_number} has invalid Is Success value")

        start = _forest_datetime(row.get("Start Time"))
        end = _forest_datetime(row.get("End Time"))
        if end <= start:
            raise ValueError(f"Forest CSV row {line_number} has invalid timing")
        if end > capture:
            raise ValueError(f"Forest CSV row {line_number} ends after the export timestamp")

        source_id = _source_id(start, end, row.get("Tree Type"))
        session = {
            "id": source_id,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "tag": str(row.get("Tag") or "").strip(),
        }
        existing = sessions_by_id.get(source_id)
        if existing and existing != session:
            raise ValueError(f"Forest CSV row {line_number} conflicts with another session identity")
        sessions_by_id[source_id] = session

    sessions = sorted(sessions_by_id.values(), key=lambda item: (item["start_at"], item["id"]))
    if not sessions:
        raise ValueError("Forest CSV contains no successful sessions; refusing to record zero")
    range_start = min(parse_datetime(item["start_at"]) for item in sessions)
    if capture <= range_start:
        raise ValueError("Forest export timestamp must be after its first successful session")
    return {
        "schema_version": 1,
        "extraction_status": "COMPLETE",
        "range_start": range_start.isoformat(),
        "range_end": capture.isoformat(),
        "sessions": sessions,
        "source_stats": {
            "successful_sessions": len(sessions),
            "unsuccessful_rows_excluded": unsuccessful,
        },
    }
