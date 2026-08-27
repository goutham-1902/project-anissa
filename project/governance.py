from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re

from logic.locks import exclusive_lock
from project.environment import ProjectEnvironment


MAINTAINER_IDS = ("GENERAL", "ANISSA_MAINTAINER", "SOLDIERS_MAINTAINER")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Invalid governance document {path.name}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Governance document is not an object: {path.name}")
    return payload


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class Governance:
    """Validate maintainer scope and append-only private release history."""

    def __init__(self, environment: ProjectEnvironment):
        self.environment = environment
        release = environment.release_root
        self.roles = _read_json(release / "schemas" / "maintainer_roles.json")
        self.schema = _read_json(release / "schemas" / "maintainer_ledger.json")
        self.policy = _read_json(release / "config" / "maintainer_scope_policy.json")
        if set(self.roles) != set(MAINTAINER_IDS):
            raise RuntimeError("Maintainer role registry is invalid")

    def ledger_path(self, maintainer_id: str) -> Path:
        self._require_maintainer(maintainer_id)
        return self.environment.maintainer_ledgers_root / self.roles[maintainer_id]["ledger"]

    def initialize_ledgers(self, component_version: str) -> list[Path]:
        if not str(component_version).strip():
            raise ValueError("Initial component version is required")
        paths = []
        for maintainer_id in MAINTAINER_IDS:
            path = self.ledger_path(maintainer_id)
            if path.exists():
                self.validate_ledger(_read_json(path), expected_maintainer=maintainer_id)
            else:
                _atomic_json(path, {
                    "schema_version": self.schema["schema_version"],
                    "maintainer_id": maintainer_id,
                    "current_component_version": component_version,
                    "changes": [],
                })
            paths.append(path)
        return paths

    def owner_for_file(self, relative_path: str) -> str:
        normalized = str(relative_path).strip().lstrip("./")
        matches = []
        for owner, rules in self.policy["owners"].items():
            if normalized in rules.get("exact", []) or any(
                normalized.startswith(prefix) for prefix in rules.get("prefixes", [])
            ):
                matches.append(owner)
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous maintainer ownership: {normalized}")
        return matches[0] if matches else self.policy["default_owner"]

    def evaluate_scope(self, maintainer_id: str, files: list[str]) -> dict:
        self._require_maintainer(maintainer_id)
        if not files:
            return {
                "accepted": False,
                "requested_maintainer": maintainer_id,
                "defer_to": "GENERAL",
                "reason": "No concrete file scope was supplied.",
                "owners": [],
            }
        owners = sorted({self.owner_for_file(path) for path in files})
        natural_owner = owners[0] if len(owners) == 1 else "GENERAL"
        accepted = maintainer_id == "GENERAL" or maintainer_id == natural_owner
        return {
            "accepted": accepted,
            "requested_maintainer": maintainer_id,
            "defer_to": None if accepted else natural_owner,
            "reason": (
                "Scope accepted before execution."
                if accepted
                else f"Scope belongs to {natural_owner}; defer before execution."
            ),
            "owners": owners,
        }

    def append_change(self, maintainer_id: str, writer_id: str, change: dict) -> dict:
        self._require_maintainer(maintainer_id)
        if writer_id != maintainer_id:
            raise PermissionError(f"{writer_id} cannot write {maintainer_id}'s ledger")
        path = self.ledger_path(maintainer_id)
        lock = path.with_suffix(path.suffix + ".lock")
        with exclusive_lock(lock):
            ledger = _read_json(path)
            self.validate_ledger(ledger, expected_maintainer=maintainer_id)
            normalized = self.validate_change(change)
            if normalized["base_version"] != ledger["current_component_version"]:
                raise ValueError("Change base version does not match the current component version")
            decision = self.evaluate_scope(maintainer_id, normalized["files"])
            if not decision["accepted"]:
                raise PermissionError(decision["reason"])
            existing_ids = {
                row["change_id"]
                for owner in MAINTAINER_IDS
                for row in self._changes_if_available(owner)
            }
            if normalized["change_id"] in existing_ids:
                raise ValueError("Duplicate maintainer change ID")
            updated = {
                **ledger,
                "current_component_version": normalized["target_version"],
                "changes": [*ledger["changes"], normalized],
            }
            self.validate_ledger(updated, expected_maintainer=maintainer_id)
            _atomic_json(path, updated)
            return normalized

    def validate_ledger(self, ledger: dict, *, expected_maintainer: str) -> None:
        missing = [
            field for field in self.schema["required_ledger_fields"] if field not in ledger
        ]
        if missing:
            raise ValueError(f"Ledger fields are missing: {', '.join(missing)}")
        if ledger["schema_version"] != self.schema["schema_version"]:
            raise ValueError("Ledger schema version is unsupported")
        if ledger["maintainer_id"] != expected_maintainer:
            raise PermissionError("Ledger owner does not match its file")
        if not isinstance(ledger["changes"], list):
            raise ValueError("Ledger changes must be a list")
        seen = set()
        previous_version = None
        for row in ledger["changes"]:
            change = self.validate_change(row)
            if change["change_id"] in seen:
                raise ValueError("Duplicate maintainer change ID")
            if previous_version is not None and change["base_version"] != previous_version:
                raise ValueError("Ledger version chain is discontinuous")
            previous_version = change["target_version"]
            seen.add(change["change_id"])
        if previous_version is not None and ledger["current_component_version"] != previous_version:
            raise ValueError("Ledger current component version disagrees with its history")

    def validate_change(self, change: dict) -> dict:
        if not isinstance(change, dict):
            raise ValueError("Maintainer change must be an object")
        missing = [
            field for field in self.schema["required_change_fields"] if field not in change
        ]
        if missing:
            raise ValueError(f"Change fields are missing: {', '.join(missing)}")
        normalized = dict(change)
        for field in (
            "change_id", "base_version", "target_version", "scope", "summary",
            "commit_sha", "migration_id",
        ):
            normalized[field] = str(normalized[field]).strip()
            if not normalized[field]:
                raise ValueError(f"Change {field} is required")
        try:
            datetime.fromisoformat(str(normalized["timestamp"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Change timestamp must be ISO-8601") from exc
        if normalized["base_version"] == normalized["target_version"]:
            raise ValueError("Change target version must advance")
        if not COMMIT_SHA.fullmatch(normalized["commit_sha"]):
            raise ValueError("Change commit SHA must be a full lowercase Git SHA")
        if not isinstance(normalized["files"], list) or not normalized["files"]:
            raise ValueError("Change files must be a non-empty list")
        normalized["files"] = [str(path).strip() for path in normalized["files"]]
        if not all(normalized["files"]):
            raise ValueError("Change files contain an empty path")
        verification = normalized["verification"]
        if not isinstance(verification, list) or not verification:
            raise ValueError("Change requires passing verification records")
        for record in verification:
            if not isinstance(record, dict) or not str(record.get("command") or "").strip():
                raise ValueError("Verification command is required")
            if record.get("status") != self.schema["verification_status"]:
                raise ValueError("Every verification record must be PASSED")
        return normalized

    def _changes_if_available(self, maintainer_id: str) -> list[dict]:
        path = self.ledger_path(maintainer_id)
        if not path.exists():
            return []
        ledger = _read_json(path)
        self.validate_ledger(ledger, expected_maintainer=maintainer_id)
        return ledger["changes"]

    @staticmethod
    def _require_maintainer(maintainer_id: str) -> None:
        if maintainer_id not in MAINTAINER_IDS:
            raise ValueError(f"Unknown maintainer: {maintainer_id}")
