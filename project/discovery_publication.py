from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

from logic.locks import exclusive_lock
from project.discovery_contract import (
    DiscoveryContractError,
    DiscoveryPublication,
    discovery_publication_payload,
    validate_discovery_publication,
)


SCHEMA_VERSION = 1
CHANNELS = {"SHADOW", "LIVE"}
OBJECTS_DIR = "objects"
STATUS_FILE = "status.json"
LOCK_FILE = ".publication.lock"


class DiscoveryPublicationError(ValueError):
    """A Lucan publication slot is invalid, incoherent, or unsafe to update."""


@dataclass(frozen=True)
class StoredDiscoveryPublication:
    publication: DiscoveryPublication | None
    status: Mapping[str, object]


def empty_discovery_status() -> dict:
    """Return the canonical fail-closed state for a new Lucan publication slot."""

    return {
        "schema_version": SCHEMA_VERSION,
        "worker_id": "lucan",
        "channel": "SHADOW",
        "state": "SETUP",
        "last_attempt": None,
        "last_attempt_channel": None,
        "last_success": None,
        "last_error": None,
        "last_publication": None,
    }


def _moment(value: datetime | None) -> datetime:
    current = value or datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise DiscoveryPublicationError("publication timestamps must include a timezone")
    return current


def _channel(value: str) -> str:
    channel = str(value or "").strip().upper()
    if channel not in CHANNELS:
        raise DiscoveryPublicationError("publication channel must be SHADOW or LIVE")
    return channel


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_status(path: Path) -> dict:
    if not path.exists():
        return empty_discovery_status()
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise DiscoveryPublicationError("Lucan publication status is unreadable") from exc
    if (
        status.get("schema_version") != SCHEMA_VERSION
        or status.get("worker_id") != "lucan"
    ):
        raise DiscoveryPublicationError("Lucan publication status schema is invalid")
    return status


def _publication_path(root: Path, metadata: Mapping[str, object]) -> Path:
    digest = str(metadata.get("sha256") or "")
    expected = f"{OBJECTS_DIR}/{digest}.json"
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or metadata.get("file") != expected:
        raise DiscoveryPublicationError("Lucan publication object reference is invalid")
    return root / expected


def _prune_objects(root: Path, keep: Path) -> None:
    objects = root / OBJECTS_DIR
    if not objects.is_dir():
        return
    for path in objects.glob("*.json"):
        if path != keep:
            path.unlink(missing_ok=True)


def read_discovery_publication(
    publication_root: Path,
    *,
    retries: int = 1,
) -> StoredDiscoveryPublication:
    """Read one checksum-coherent Lucan slot, retrying a concurrent handoff once."""

    root = Path(publication_root)
    status_path = root / STATUS_FILE
    last_error: Exception | None = None
    for _ in range(max(0, retries) + 1):
        try:
            status = _read_status(status_path)
            metadata = status.get("last_publication")
            if metadata is None:
                return StoredDiscoveryPublication(publication=None, status=status)
            if not isinstance(metadata, dict):
                raise DiscoveryPublicationError(
                    "Lucan last-publication metadata is invalid"
                )
            channel = str(status.get("channel") or "").upper()
            state = str(status.get("state") or "").upper()
            if channel not in CHANNELS or state not in {"COMPLETE", "PARTIAL", "FAILED"}:
                raise DiscoveryPublicationError("Lucan publication status is invalid")
            publication_path = _publication_path(root, metadata)
            raw = publication_path.read_bytes()
            digest = sha256(raw).hexdigest()
            if digest != str(metadata.get("sha256") or ""):
                raise DiscoveryPublicationError(
                    "Lucan publication checksum does not match its status"
                )
            payload = json.loads(raw.decode("utf-8"))
            publication = validate_discovery_publication(payload)
            if state != "FAILED" and state != publication.status:
                raise DiscoveryPublicationError(
                    "Lucan publication state does not match its payload"
                )
            expected = {
                "agenda_id": publication.agenda_id,
                "assignment_id": publication.assignment_id,
                "run_kind": publication.run_kind,
                "status": publication.status,
                "completed_at": publication.completed_at.isoformat(),
                "candidate_count": len(publication.candidates),
                "sha256": digest,
                "file": f"{OBJECTS_DIR}/{digest}.json",
            }
            if metadata != expected:
                raise DiscoveryPublicationError(
                    "Lucan publication metadata does not match its payload"
                )
            return StoredDiscoveryPublication(publication=publication, status=status)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            last_error = exc
    if isinstance(last_error, DiscoveryPublicationError):
        raise last_error
    if isinstance(last_error, DiscoveryContractError):
        raise DiscoveryPublicationError(str(last_error)) from last_error
    raise DiscoveryPublicationError(
        f"Lucan publication is unreadable: {type(last_error).__name__}"
    ) from last_error


def publish_discovery(
    publication_root: Path,
    payload: object,
    *,
    channel: str,
    attempted_at: datetime | None = None,
) -> StoredDiscoveryPublication:
    """Validate and atomically publish one successful, idempotent Lucan result."""

    root = Path(publication_root)
    publication = validate_discovery_publication(payload)
    if publication.status == "FAILED":
        raise DiscoveryPublicationError(
            "FAILED research results must use record_discovery_failure"
        )
    channel = _channel(channel)
    attempted = _moment(attempted_at or publication.completed_at)
    encoded = _json_bytes(discovery_publication_payload(publication))
    digest = sha256(encoded).hexdigest()
    status_path = root / STATUS_FILE

    with exclusive_lock(root / LOCK_FILE):
        current = read_discovery_publication(root, retries=0)
        previous = current.publication
        previous_channel = str(current.status.get("channel") or "SHADOW").upper()
        if previous is not None and previous.assignment_id == publication.assignment_id:
            previous_digest = str(
                (current.status.get("last_publication") or {}).get("sha256") or ""
            )
            if previous_digest != digest or previous_channel != channel:
                raise DiscoveryPublicationError(
                    "Lucan assignment replay conflicts with the stored publication"
                )
            return current
        if previous is not None and publication.completed_at < previous.completed_at:
            raise DiscoveryPublicationError(
                "Lucan publication is older than the retained result"
            )

        metadata = {
            "agenda_id": publication.agenda_id,
            "assignment_id": publication.assignment_id,
            "run_kind": publication.run_kind,
            "status": publication.status,
            "completed_at": publication.completed_at.isoformat(),
            "candidate_count": len(publication.candidates),
            "sha256": digest,
            "file": f"{OBJECTS_DIR}/{digest}.json",
        }
        status = {
            "schema_version": SCHEMA_VERSION,
            "worker_id": "lucan",
            "channel": channel,
            "state": publication.status,
            "last_attempt": attempted.isoformat(),
            "last_attempt_channel": channel,
            "last_success": attempted.isoformat(),
            "last_error": None,
            "last_publication": metadata,
        }
        publication_path = root / metadata["file"]
        _atomic_bytes(publication_path, encoded)
        _atomic_bytes(status_path, _json_bytes(status))
        _prune_objects(root, publication_path)
        return StoredDiscoveryPublication(publication=publication, status=status)


def record_discovery_failure(
    publication_root: Path,
    *,
    channel: str,
    stage: str,
    message: str,
    attempted_at: datetime | None = None,
) -> StoredDiscoveryPublication:
    """Record a failed attempt without replacing the last known-good publication."""

    root = Path(publication_root)
    channel = _channel(channel)
    stage = " ".join(str(stage or "").split())
    message = " ".join(str(message or "").split())
    if not stage or not message:
        raise DiscoveryPublicationError("failure stage and message are required")
    if len(stage) > 80 or len(message) > 800:
        raise DiscoveryPublicationError("failure stage or message exceeds its size limit")
    attempted = _moment(attempted_at)
    with exclusive_lock(root / LOCK_FILE):
        current = read_discovery_publication(root, retries=0)
        retained_channel = str(
            current.status.get("channel") or "SHADOW"
        ).upper()
        status = {
            **current.status,
            "schema_version": SCHEMA_VERSION,
            "worker_id": "lucan",
            "channel": retained_channel if current.publication is not None else channel,
            "state": "FAILED",
            "last_attempt": attempted.isoformat(),
            "last_attempt_channel": channel,
            "last_error": {"stage": stage, "message": message},
        }
        _atomic_bytes(root / STATUS_FILE, _json_bytes(status))
        return StoredDiscoveryPublication(
            publication=current.publication,
            status=status,
        )
