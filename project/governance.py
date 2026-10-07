from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

from logic.locks import exclusive_lock
from project.environment import ProjectEnvironment


MAINTAINER_IDS = ("GENERAL", "ANISSA_MAINTAINER", "SOLDIERS_MAINTAINER")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def verification_identity(root: Path) -> dict:
    """Identify the actual tested source, including uncommitted source edits."""
    root = Path(root).resolve()
    ignored = {".git", "__pycache__", "node_modules", "brain", "profile", "runtime", "shared", "work", "tmp", "private"}
    suffixes = {".py", ".json", ".md", ".css", ".js", ".html", ".toml", ".yml", ".yaml", ".txt"}
    digest = sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or set(relative.parts) & ignored or path.suffix not in suffixes:
            continue
        digest.update(relative.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
    )
    return {
        "version": _read_json(root / "manifest.json")["version"],
        "source_revision": revision.stdout.strip() if revision.returncode == 0 else None,
        "source_digest": digest.hexdigest(),
    }


def automation_contract_problems(settings: dict, automations: dict, workers: tuple = ()) -> list[str]:
    """Reject scheduled work whose declared mode or task binding disallows it."""
    problems = []
    for role, identifier in (settings.get("automation_bindings") or {}).items():
        task = automations.get(identifier, {})
        if task.get("status") != "ACTIVE":
            problems.append(f"bound automation is not active: {identifier}")
        if settings.get("mode") != "LIVE" or settings.get("go_live_authorized") is not True:
            problems.append(f"scheduled campaign work requires authorized LIVE mode: {identifier}")
        role_id = role.removesuffix("_DISPATCHER")
        expected = (settings.get("chat_bindings") or {}).get(role_id)
        if not expected or task.get("target_thread_id") != expected:
            problems.append(f"automation task binding disagrees: {identifier}")
    for worker in workers:
        identifier = worker.get("automation_id")
        task = automations.get(identifier, {})
        if task.get("status") != "ACTIVE":
            if worker.get("automation_status") == "ACTIVE":
                problems.append(f"worker automation is not active: {identifier}")
            continue
        if worker.get("mode") != "LIVE":
            problems.append(f"scheduled worker requires LIVE mode: {identifier}")
        if worker.get("automation_status") != "ACTIVE":
            problems.append(f"worker automation status disagrees: {identifier}")
        if not worker.get("thread_id") or task.get("target_thread_id") != worker.get("thread_id"):
            problems.append(f"worker automation task binding disagrees: {identifier}")
        if worker.get("project_version") != settings.get("package_version"):
            problems.append(f"scheduled worker release version disagrees: {identifier}")
    project_path = (settings.get("project_binding") or {}).get("path")
    targets = set((settings.get("chat_bindings") or {}).values())
    targets.update((settings.get("maintainer_chat_bindings") or {}).values())
    targets.update(worker.get("thread_id") for worker in workers if worker.get("thread_id"))
    for identifier, task in automations.items():
        if (project_path and project_path in task.get("prompt", "")
                and task.get("status") == "ACTIVE" and task.get("target_thread_id") not in targets):
            problems.append(f"unregistered scheduled project task: {identifier}")
    return problems


def validate_release_evidence(record: dict, identity: dict, *, implementation_only: bool,
                              operational_evidence: dict | None = None) -> dict:
    """Software tests cannot substitute for acceptance of changed workflows."""
    if (record.get("status") != "PASSED" or not record.get("checks")
            or any(check.get("status") != "PASSED" for check in record["checks"])
            or any(record.get(key) != value for key, value in identity.items())):
        raise ValueError("Release source differs from the passing verification record")
    if implementation_only:
        return {"status": "UNCHANGED_RUNTIME", "evidence": "Implementation-only release"}
    evidence = operational_evidence or {}
    if (evidence.get("status") != "PASSED" or not evidence.get("evidence")
            or evidence.get("source_revision") != identity.get("source_revision")):
        raise ValueError("Changed runtime behavior requires revision-matched operational acceptance")
    return evidence


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
        publication = self.policy.get("publication", {})
        if publication.get("publisher") != "GENERAL":
            raise RuntimeError("General must own the guarded public release seam")
        for field in ("projection_builder", "safety_gate", "eligibility", "rule"):
            if not str(publication.get(field) or "").strip():
                raise RuntimeError(f"Publication policy is missing {field}")
        coordination = self.policy.get("coordination", {})
        if coordination.get("transport") != "persistent_task_messages":
            raise RuntimeError("Maintainer coordination transport is invalid")
        required_checkpoints = {
            "SCOPE", "ACK", "HANDOFF", "RECEIVED",
            "INTEGRATED_OR_REPAIR_REQUIRED", "PUBLISHED",
        }
        if set(coordination.get("checkpoints") or ()) != required_checkpoints:
            raise RuntimeError("Maintainer coordination checkpoints are invalid")
        for field in (
            "single_writer_per_file_scope",
            "general_closes_cross_scope_work",
        ):
            if coordination.get(field) is not True:
                raise RuntimeError(f"Maintainer coordination must enable {field}")
        if coordination.get("silence_is_acceptance") is not False:
            raise RuntimeError("Maintainer silence cannot imply acceptance")
        role_notes = coordination.get("anissa_role_update_notes", {})
        if role_notes.get("owner") != "GENERAL":
            raise RuntimeError("General must own Anissa role update notes")
        if role_notes.get("targets") != [
            "COMMAND",
            "WEEKDAY_OPS",
            "WEEKEND",
        ]:
            raise RuntimeError("Anissa role update-note targets are invalid")
        required_content = {
            "deployed_version",
            "changed_behavior_or_interfaces",
            "preserved_invariants",
            "user_action_required",
        }
        if set(role_notes.get("content") or ()) != required_content:
            raise RuntimeError("Anissa role update-note content is invalid")
        for field in (
            "required_before_closure",
            "delivery_must_be_verified",
            "implementation_only_updates_state_behavior_unchanged",
        ):
            if role_notes.get(field) is not True:
                raise RuntimeError(f"Anissa role update notes must enable {field}")
        if role_notes.get("request_acknowledgement") is not False:
            raise RuntimeError("Anissa role update notes cannot request acknowledgement")
        if role_notes.get("format") != "compact_delta_only":
            raise RuntimeError("Anissa role update notes must be compact and delta-only")

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

    def publication_route(self, maintainer_id: str, files: list[str]) -> dict:
        """Return the one guarded route from a verified change to GitHub."""
        decision = self.evaluate_scope(maintainer_id, files)
        publication = self.policy["publication"]
        if not decision["accepted"]:
            return {
                **decision,
                "publisher": publication["publisher"],
                "action": "DEFER_SCOPE",
            }
        return {
            **decision,
            "publisher": publication["publisher"],
            "action": (
                "GENERATE_AUDIT_AND_PUSH"
                if maintainer_id == publication["publisher"]
                else "HANDOFF_VERIFIED_CHANGE_TO_GENERAL"
            ),
            "eligibility": publication["eligibility"],
            "projection_builder": publication["projection_builder"],
            "safety_gate": publication["safety_gate"],
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
