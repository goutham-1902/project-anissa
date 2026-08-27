from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS forest_sessions (
    source_id TEXT PRIMARY KEY,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    tag TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage (
    range_start TEXT NOT NULL,
    range_end TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE(range_start, range_end)
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def import_sessions(self, sessions: list[dict], *, seen_at: str,
                        range_start: str, range_end: str) -> dict:
        inserted = 0
        updated = 0
        removed = 0
        with self.connect() as db:
            incoming_ids = {str(session["id"]) for session in sessions}
            overlapping = db.execute(
                "SELECT source_id FROM forest_sessions WHERE start_at < ? AND end_at > ?",
                (range_end, range_start),
            ).fetchall()
            stale_ids = [row["source_id"] for row in overlapping if row["source_id"] not in incoming_ids]
            if stale_ids:
                db.executemany("DELETE FROM forest_sessions WHERE source_id=?", ((value,) for value in stale_ids))
                removed = len(stale_ids)
            for session in sessions:
                source_id = str(session["id"])
                exists = db.execute(
                    "SELECT 1 FROM forest_sessions WHERE source_id=?", (source_id,)
                ).fetchone()
                db.execute(
                    """INSERT INTO forest_sessions
                       (source_id,start_at,end_at,tag,payload_json,last_seen_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(source_id) DO UPDATE SET
                         start_at=excluded.start_at,
                         end_at=excluded.end_at,
                         tag=excluded.tag,
                         payload_json=excluded.payload_json,
                         last_seen_at=excluded.last_seen_at""",
                    (
                        source_id, str(session["start_at"]), str(session["end_at"]),
                        str(session.get("tag") or ""),
                        json.dumps(session, ensure_ascii=False, sort_keys=True), seen_at,
                    ),
                )
                if exists:
                    updated += 1
                else:
                    inserted += 1
        return {"inserted": inserted, "updated": updated, "removed": removed}

    def add_coverage(self, start: str, end: str, imported_at: str):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO coverage(range_start,range_end,imported_at) VALUES (?,?,?)",
                (start, end, imported_at),
            )

    def coverage_bounds(self) -> tuple[str | None, str | None]:
        with self.connect() as db:
            row = db.execute("SELECT MIN(range_start) AS start, MAX(range_end) AS end FROM coverage").fetchone()
        return (row["start"], row["end"]) if row else (None, None)

    def coverage_summary(self) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT range_start,range_end FROM coverage ORDER BY range_start,range_end").fetchall()
        if not rows:
            return {"start": None, "through": None, "intervals": [], "has_gaps": False}
        intervals: list[list[str]] = []
        for row in rows:
            start, end = row["range_start"], row["range_end"]
            if not intervals or start > intervals[-1][1]:
                intervals.append([start, end])
            elif end > intervals[-1][1]:
                intervals[-1][1] = end
        return {
            "start": intervals[0][0],
            "through": intervals[-1][1],
            "intervals": [{"start": start, "end": end} for start, end in intervals],
            "has_gaps": len(intervals) > 1,
        }

    def sessions(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT source_id,start_at,end_at,tag FROM forest_sessions ORDER BY start_at,source_id"
            ).fetchall()
        return [
            {"id": row["source_id"], "start_at": row["start_at"], "end_at": row["end_at"], "tag": row["tag"]}
            for row in rows
        ]

    def set(self, key: str, value: object):
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        with self.connect() as db:
            db.execute(
                "INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encoded),
            )

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
