from __future__ import annotations

import csv
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from openpyxl import Workbook

from project.environment import ProjectEnvironment
from project.telemetry_contract import FIELDS


def _json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare_instance_root(instance_root: Path, release_root: Path) -> Path:
    root = Path(instance_root).expanduser().resolve()
    release = Path(release_root).resolve()
    home = Path.home().resolve()
    if (
        root in {Path("/"), home, release}
        or release.is_relative_to(root)
        or root.is_relative_to(release)
    ):
        raise ValueError(f"Unsafe instance directory: {root}")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Instance directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _empty_workbook(environment: ProjectEnvironment, package_version: str) -> None:
    schema = json.loads(environment.schema_path.read_text(encoding="utf-8"))
    roles = json.loads(environment.role_registry_path.read_text(encoding="utf-8"))
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, headers in schema["sheets"].items():
        sheet = workbook.create_sheet(name)
        if headers:
            sheet.append(headers)

    control = workbook["CONTROL"]
    control_rows = (
        ("setup_mode", "SETUP", "Activation requires explicit go-live authorization."),
        ("package_version", package_version, "Project Anissa release version."),
        ("schema_version", schema["version"], "Canonical workbook schema version."),
        ("timezone", "Asia/Kolkata", "Configure during onboarding if required."),
        ("owner", "Local user", "Replace with verified onboarding data."),
        (
            "default_chat_topology",
            "; ".join(roles[role_id]["name"] for role_id in (
                "COMMAND", "WEEKDAY_OPS", "WEEKEND"
            )),
            "Permanent command, weekday and weekend interfaces.",
        ),
        ("india_campaign_status", "PAUSED", "Configure the active agenda before go-live."),
        ("writing_mode", "USER_WRITES_ALL", "Pre-writing briefs only."),
        ("last_updated", datetime.now().isoformat(timespec="seconds"), "Instance initialization."),
    )
    for row in control_rows:
        control.append(row)

    registry = workbook["CHAT_REGISTRY"]
    purposes = {
        "COMMAND": "Home interaction, strategy and major decisions.",
        "WEEKDAY_OPS": "Weekday execution and accountability.",
        "WEEKEND": "Weekend deep work and weekly audit.",
    }
    for role_id, purpose in purposes.items():
        row = roles[role_id]
        registry.append((role_id, row["name"], row.get("purpose", purpose), "PROPOSED"))

    environment.brain_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(environment.brain_path)
    workbook.close()


def _empty_telemetry(environment: ProjectEnvironment) -> None:
    environment.telemetry_root.mkdir(parents=True, exist_ok=True)
    (environment.telemetry_root / "schema.json").write_bytes(
        (environment.release_root / "schemas" / "telemetry_publication.json").read_bytes()
    )
    worklog = environment.telemetry_root / "worklog.csv"
    with worklog.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(FIELDS)
    _json(environment.telemetry_root / "status.json", {
        "schema_version": 1,
        "state": "SETUP",
        "row_count": 0,
        "worklog_sha256": sha256(worklog.read_bytes()).hexdigest(),
    })


def initialize_instance(
    release_root: Path,
    instance_root: Path,
) -> ProjectEnvironment:
    """Create a private, non-live instance containing no personal source data."""
    release = Path(release_root).resolve()
    root = prepare_instance_root(instance_root, release)
    environment = ProjectEnvironment.external_instance(release, root)
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "0")

    environment.role_registry_path.parent.mkdir(parents=True, exist_ok=True)
    environment.role_registry_path.write_bytes(
        (release / "schemas" / "chat_roles.json").read_bytes()
    )
    _empty_workbook(environment, version)
    _json(environment.runtime_settings_path, {
        "package_version": version,
        "schema_version": json.loads(environment.schema_path.read_text())["version"],
        "mode": "SETUP",
        "go_live_authorized": False,
        "timezone": "Asia/Kolkata",
        "chat_bindings": {},
        "automation_bindings": {},
        "telemetry_integration": {"status": "SETUP", "campaign_state": False},
    })
    _json(environment.portfolio_path, {
        "schema_version": 1,
        "default_agenda_id": "graduate_applications",
        "agendas": [{
            "agenda_id": "graduate_applications",
            "name": "Graduate Applications",
            "lifecycle": "ACTIVE",
            "state_locator": "canonical_brain",
            "allocation_weight": 1.0,
        }],
    })
    _json(environment.worker_settings_path, {
        "name": "A2A | assistant to anissa",
        "mode": "SETUP",
        "acquisition": {"type": "manual_csv_import", "timezone": "Asia/Kolkata"},
        "dashboard": {"host": "127.0.0.1", "port": 8765},
    })
    _json(environment.profile_root / "verified_profile.json", {
        "schema_version": 1,
        "status": "UNCONFIGURED",
        "facts": {},
    })
    _json(environment.profile_root / "onboarding_gaps.json", {
        "schema_version": 1,
        "status": "UNRESOLVED",
        "questions": [],
    })
    environment.private_persona_root.mkdir(parents=True, exist_ok=True)
    environment.a2a_private_root.mkdir(parents=True, exist_ok=True)
    environment.private_assets_root.mkdir(parents=True, exist_ok=True)
    environment.maintainer_ledgers_root.mkdir(parents=True, exist_ok=True)
    environment.lock_path.parent.mkdir(parents=True, exist_ok=True)
    (environment.backup_root / "daily").mkdir(parents=True, exist_ok=True)
    (environment.backup_root / "weekly").mkdir(parents=True, exist_ok=True)
    environment.events_path.touch()
    environment.errors_path.touch()
    _empty_telemetry(environment)
    _json(root / "instance.json", {
        "schema_version": 1,
        "release_version": version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "origin": "clean_initialization",
    })
    return environment
