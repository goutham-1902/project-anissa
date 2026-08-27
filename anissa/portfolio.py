from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


LIFECYCLES = {"ACTIVE", "PAUSED", "COMPLETE", "ARCHIVED"}


@dataclass(frozen=True)
class AgendaRegistration:
    agenda_id: str
    name: str
    lifecycle: str
    state_locator: str
    allocation_weight: float


@dataclass(frozen=True)
class Portfolio:
    """Validated agenda registration and cross-agenda allocation."""

    default_agenda_id: str
    agendas: tuple[AgendaRegistration, ...]

    @classmethod
    def load(cls, path: Path) -> "Portfolio":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(
                f"Portfolio is unavailable or invalid ({exc.__class__.__name__})"
            ) from exc
        if int(payload.get("schema_version") or 0) != 1:
            raise RuntimeError("Portfolio schema version is unsupported")
        raw_agendas = payload.get("agendas")
        if not isinstance(raw_agendas, list) or not raw_agendas:
            raise RuntimeError("Portfolio must register at least one agenda")
        registrations = []
        seen = set()
        for index, row in enumerate(raw_agendas):
            if not isinstance(row, dict):
                raise RuntimeError(f"Portfolio agenda {index} is not an object")
            agenda_id = str(row.get("agenda_id") or "").strip()
            lifecycle = str(row.get("lifecycle") or "").upper()
            locator = str(row.get("state_locator") or "").strip()
            try:
                weight = float(row.get("allocation_weight"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Portfolio agenda {index} has invalid allocation") from exc
            if not agenda_id or agenda_id in seen:
                raise RuntimeError("Portfolio agenda IDs must be present and unique")
            if lifecycle not in LIFECYCLES:
                raise RuntimeError(f"Portfolio agenda {agenda_id} has invalid lifecycle")
            if locator != "canonical_brain":
                raise RuntimeError(f"Portfolio agenda {agenda_id} has unsupported state locator")
            if weight < 0:
                raise RuntimeError(f"Portfolio agenda {agenda_id} has negative allocation")
            seen.add(agenda_id)
            registrations.append(AgendaRegistration(
                agenda_id=agenda_id,
                name=str(row.get("name") or agenda_id).strip(),
                lifecycle=lifecycle,
                state_locator=locator,
                allocation_weight=weight,
            ))
        default = str(payload.get("default_agenda_id") or "").strip()
        selected = next((row for row in registrations if row.agenda_id == default), None)
        if selected is None or selected.lifecycle != "ACTIVE":
            raise RuntimeError("Portfolio default agenda must be registered and ACTIVE")
        active_weight = sum(row.allocation_weight for row in registrations if row.lifecycle == "ACTIVE")
        if active_weight <= 0:
            raise RuntimeError("Portfolio active allocation must be positive")
        return cls(default_agenda_id=default, agendas=tuple(registrations))

    @property
    def default(self) -> AgendaRegistration:
        return next(row for row in self.agendas if row.agenda_id == self.default_agenda_id)
