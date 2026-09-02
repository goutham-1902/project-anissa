from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


RELEASE_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_ENV = "PROJECT_ANISSA_INSTANCE"
DEFAULT_POINTER = (
    Path.home() / "Library" / "Application Support" / "Project Anissa"
    / "current-instance.json"
)


@dataclass(frozen=True)
class ProjectEnvironment:
    """Resolve physical deployment paths behind one small, validated interface."""

    release_root: Path
    instance_root: Path
    layout: str
    brain_path: Path
    schema_path: Path
    runtime_settings_path: Path
    role_registry_path: Path
    worker_settings_path: Path
    lock_path: Path
    events_path: Path
    errors_path: Path
    dispatch_slots_path: Path
    dispatch_lock_path: Path
    backup_root: Path
    profile_root: Path
    public_persona_root: Path
    private_persona_root: Path
    a2a_private_root: Path
    telemetry_root: Path
    private_assets_root: Path
    portfolio_path: Path
    maintainer_ledgers_root: Path

    @classmethod
    def existing_layout(cls, release_root: Path = RELEASE_ROOT) -> "ProjectEnvironment":
        root = Path(release_root).resolve()
        runtime = root / "runtime"
        return cls(
            release_root=root,
            instance_root=root,
            layout="existing",
            brain_path=root / "brain" / "anissa_brain.xlsx",
            schema_path=root / "schemas" / "workbook_schema.json",
            runtime_settings_path=runtime / "settings.json",
            role_registry_path=root / "schemas" / "chat_roles.json",
            worker_settings_path=root / "worker1" / "settings.json",
            lock_path=runtime / "locks" / "anissa_brain.lock",
            events_path=runtime / "events.jsonl",
            errors_path=runtime / "errors.jsonl",
            dispatch_slots_path=runtime / "dispatch_slots.json",
            dispatch_lock_path=runtime / "locks" / "dispatch_slots.lock",
            backup_root=runtime / "backups",
            profile_root=root / "profile",
            public_persona_root=root / "persona",
            private_persona_root=root / "persona",
            a2a_private_root=root / "worker1" / "private",
            telemetry_root=root / "shared" / "worker1",
            private_assets_root=root / "worker1" / "dashboard" / "assets",
            portfolio_path=runtime / "portfolio.json",
            maintainer_ledgers_root=runtime / "maintainer_ledgers",
        )

    @classmethod
    def external_instance(
        cls, release_root: Path, instance_root: Path
    ) -> "ProjectEnvironment":
        release = Path(release_root).resolve()
        instance = Path(instance_root).expanduser().resolve()
        runtime = instance / "runtime"
        agenda = instance / "agendas" / "graduate_applications"
        return cls(
            release_root=release,
            instance_root=instance,
            layout="external",
            brain_path=agenda / "brain" / "anissa_brain.xlsx",
            schema_path=release / "schemas" / "workbook_schema.json",
            runtime_settings_path=runtime / "settings.json",
            role_registry_path=runtime / "roles.json",
            worker_settings_path=instance / "soldiers" / "a2a" / "settings.json",
            lock_path=runtime / "locks" / "anissa_brain.lock",
            events_path=runtime / "events.jsonl",
            errors_path=runtime / "errors.jsonl",
            dispatch_slots_path=runtime / "dispatch_slots.json",
            dispatch_lock_path=runtime / "locks" / "dispatch_slots.lock",
            backup_root=runtime / "backups",
            profile_root=agenda / "profile",
            public_persona_root=release / "anissa" / "persona" / "default_clean",
            private_persona_root=instance / "anissa" / "persona" / "private",
            a2a_private_root=instance / "soldiers" / "a2a" / "private",
            telemetry_root=instance / "telemetry" / "a2a",
            private_assets_root=instance / "assets" / "private",
            portfolio_path=instance / "portfolio.json",
            maintainer_ledgers_root=instance / "maintainer_ledgers",
        )

    def validate(self, *, require_state: bool = True) -> list[str]:
        problems = []
        if not self.release_root.is_dir():
            problems.append(f"release root is unavailable: {self.release_root}")
        if not self.schema_path.is_file():
            problems.append(f"workbook schema is unavailable: {self.schema_path}")
        if require_state:
            for label, path in (
                ("canonical workbook", self.brain_path),
                ("runtime settings", self.runtime_settings_path),
                ("role registry", self.role_registry_path),
            ):
                if not path.is_file():
                    problems.append(f"{label} is unavailable: {path}")
        return problems


def _pointer_instance(pointer_path: Path) -> Path | None:
    if not pointer_path.is_file():
        return None
    payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    value = str(payload.get("instance_root") or "").strip()
    if not value:
        raise ValueError(f"Project Anissa pointer has no instance_root: {pointer_path}")
    return Path(value).expanduser()


def resolve_environment(
    release_root: Path = RELEASE_ROOT,
    *,
    instance_root: Path | None = None,
    pointer_path: Path = DEFAULT_POINTER,
    require_state: bool = True,
) -> ProjectEnvironment:
    release = Path(release_root).resolve()
    configured = instance_root
    if configured is None:
        value = os.environ.get(INSTANCE_ENV, "").strip()
        configured = Path(value).expanduser() if value else None
    if configured is None:
        configured = _pointer_instance(Path(pointer_path))
    environment = (
        ProjectEnvironment.external_instance(release, configured)
        if configured is not None
        else ProjectEnvironment.existing_layout(release)
    )
    problems = environment.validate(require_state=require_state)
    if problems:
        raise RuntimeError("; ".join(problems))
    return environment
