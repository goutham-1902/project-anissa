from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path

from project.telemetry_contract import FIELDS, decode_worklog, validate_rows


def _atomic_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def publish_worklog(path: Path, rows: list[dict]) -> str:
    import csv

    rows = validate_rows(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    digest = sha256(tmp.read_bytes()).hexdigest()
    os.replace(tmp, path)
    return digest


def publish_status(path: Path, status: dict):
    payload = json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    _atomic_bytes(Path(path), payload.encode("utf-8"))


def read_worklog(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return decode_worklog(path.read_bytes())


def read_status(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"state": "SETUP", "schema_version": 1}
    return json.loads(path.read_text(encoding="utf-8"))
