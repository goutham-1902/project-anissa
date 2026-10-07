from __future__ import annotations

from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from typing import Callable

from logic.locks import exclusive_lock
from project.environment import ProjectEnvironment
from project.recovery import role_for_slot
from project.telemetry_contract import IST


SCHEMA_VERSION = 1
DEFAULT_LEASE_SECONDS = 3600
RETENTION_DAYS = 45
SLOT_NAME = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


def dispatch_id(slot: str, on: date) -> str:
    """Return the stable technical receipt ID for one logical schedule slot."""
    normalized = str(slot).strip().lower()
    if not SLOT_NAME.fullmatch(normalized):
        raise ValueError("Dispatch slot must be a lowercase hyphenated name")
    digest = sha256(f"dispatch:v1:{on.isoformat()}:{normalized}".encode()).hexdigest()
    return f"dispatch_{digest[:16]}"


def _moment(value: datetime | None) -> datetime:
    current = value or datetime.now(IST)
    return current.replace(tzinfo=IST) if current.tzinfo is None else current.astimezone(IST)


class DispatchGate:
    """Provide leased, idempotent technical receipts outside campaign state."""

    def __init__(
        self,
        environment: ProjectEnvironment,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.path = environment.dispatch_slots_path
        self.lock_path = environment.dispatch_lock_path
        self._now = now_provider or (lambda: datetime.now(IST))

    def claim(
        self,
        slot: str,
        *,
        on: date | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict:
        if lease_seconds < 60 or lease_seconds > 86400:
            raise ValueError("Dispatch lease must be between 60 seconds and 24 hours")
        now = _moment(self._now())
        run_date = on or now.date()
        receipt_id = dispatch_id(slot, run_date)
        with exclusive_lock(self.lock_path):
            payload = self._read()
            self._prune(payload, now.date())
            existing = payload["slots"].get(receipt_id)
            if existing and existing["status"] == "completed":
                if existing.get("delivery_status") == "pending":
                    expires = existing.get("delivery_lease_expires_at")
                    if expires and datetime.fromisoformat(expires) > now:
                        return self._result("busy", existing)
                    existing["delivery_token"] = token_urlsafe(18)
                    existing["delivery_lease_expires_at"] = (now + timedelta(seconds=120)).isoformat(timespec="seconds")
                    self._write(payload)
                    return {**self._result("delivery", existing), "claim_token": existing["delivery_token"]}
                return self._result("existing", existing)
            if existing and existing["status"] == "superseded":
                return self._result("existing", existing)
            role = role_for_slot(slot)
            if role and any(
                row.get("status") == "running" and role_for_slot(row["slot"]) == role
                and datetime.fromisoformat(row["lease_expires_at"]) > now
                for key, row in payload["slots"].items() if key != receipt_id
            ):
                return {"action": "busy", "slot": slot, "run_date": run_date.isoformat()}
            if existing and existing["status"] == "running":
                lease_expires = datetime.fromisoformat(existing["lease_expires_at"])
                if lease_expires > now:
                    return self._result("busy", existing)

            token = token_urlsafe(18)
            record = {
                "dispatch_id": receipt_id,
                "slot": str(slot).strip().lower(),
                "run_date": run_date.isoformat(),
                "status": "running",
                "claim_token": token,
                "claimed_at": now.isoformat(timespec="seconds"),
                "lease_expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(
                    timespec="seconds"
                ),
                "attempt": int((existing or {}).get("attempt") or 0) + 1,
            }
            payload["slots"][receipt_id] = record
            self._write(payload)
            return self._result("acquired", record, include_token=True)

    def complete(self, receipt_id: str, claim_token: str, *, message_expected: bool = False) -> dict:
        return self._finish(receipt_id, claim_token, "completed", message_expected=message_expected)

    def fail(self, receipt_id: str, claim_token: str) -> dict:
        return self._finish(receipt_id, claim_token, "failed")

    def _finish(self, receipt_id: str, claim_token: str, status: str, *, message_expected: bool = False) -> dict:
        now = _moment(self._now())
        with exclusive_lock(self.lock_path):
            payload = self._read()
            record = payload["slots"].get(receipt_id)
            if record is None:
                raise ValueError("Unknown dispatch receipt")
            if record["status"] == "completed":
                if status == "completed":
                    return self._result("existing", record)
                raise ValueError("Completed dispatch receipt cannot be failed")
            if record["status"] != "running" or record.get("claim_token") != claim_token:
                raise PermissionError("Dispatch claim is no longer owned by this token")
            record["status"] = status
            record[f"{status}_at"] = now.isoformat(timespec="seconds")
            if status == "completed":
                record["delivery_status"] = "pending" if message_expected else "silent"
                if message_expected:
                    record["delivery_token"] = claim_token
                    record["delivery_lease_expires_at"] = (now + timedelta(seconds=120)).isoformat(timespec="seconds")
            record.pop("claim_token", None)
            record.pop("lease_expires_at", None)
            self._write(payload)
            return self._result(status, record)

    def state(self) -> dict:
        """Technical receipt state for code; do not expose full history to a model."""
        return self._read()

    def deliver(self, receipt_id: str, token: str) -> dict:
        now = _moment(self._now())
        with exclusive_lock(self.lock_path):
            payload = self._read()
            row = payload["slots"].get(receipt_id, {})
            if row.get("delivery_status") == "delivered":
                return self._result("existing", row)
            if row.get("status") != "completed" or row.get("delivery_token") != token:
                raise PermissionError("Report delivery is not owned by this token")
            row["delivery_status"] = "delivered"
            row["delivered_at"] = now.isoformat(timespec="seconds")
            row.pop("delivery_token", None)
            row.pop("delivery_lease_expires_at", None)
            role = role_for_slot(row["slot"])
            if role:
                payload.setdefault("coverage", {}).setdefault(role, {})["last_delivered_at"] = row["delivered_at"]
            self._write(payload)
            return self._result("delivered", row)

    def supersede(self, receipt_ids: list[str]) -> dict:
        now = _moment(self._now())
        with exclusive_lock(self.lock_path):
            payload = self._read()
            updated = []
            for identifier in receipt_ids:
                row = payload["slots"].get(identifier)
                expires = row.get("delivery_lease_expires_at") if row else None
                if expires and datetime.fromisoformat(expires) > now:
                    continue
                if row and (row.get("status") == "failed" or row.get("delivery_status") == "pending"):
                    row["status"] = "superseded"
                    row["delivery_status"] = "superseded"
                    row["superseded_at"] = now.isoformat(timespec="seconds")
                    row.pop("delivery_token", None)
                    row.pop("delivery_lease_expires_at", None)
                    updated.append(identifier)
            self._write(payload)
            return {"superseded": updated}

    def _read(self) -> dict:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "slots": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Dispatch receipt store is invalid") from exc
        if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(
            payload.get("slots"), dict
        ):
            raise RuntimeError("Dispatch receipt store schema is invalid")
        return payload

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _prune(payload: dict, today: date) -> None:
        cutoff = today - timedelta(days=RETENTION_DAYS)
        payload["slots"] = {
            key: record
            for key, record in payload["slots"].items()
            if date.fromisoformat(record["run_date"]) >= cutoff
        }

    @staticmethod
    def _result(action: str, record: dict, *, include_token: bool = False) -> dict:
        result = {
            "action": action,
            "dispatch_id": record["dispatch_id"],
            "slot": record["slot"],
            "run_date": record["run_date"],
        }
        if include_token:
            result["claim_token"] = record["claim_token"]
        return result
