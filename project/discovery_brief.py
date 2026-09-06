from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
import re
from typing import Mapping

from project.discovery_contract import MAX_CANDIDATES, RUN_KINDS


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, SCHEMA_VERSION}
MAX_TRACKS = 4
MAX_PROFILE_FACTS = 20
MAX_HARD_RULES = 30
MAX_KNOWN_CANDIDATES = 30
TRACK_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
ASSIGNMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,79}$")


class DiscoveryBriefError(ValueError):
    """A research brief is unsafe or too broad to send to Lucan."""


@dataclass(frozen=True)
class DiscoveryTrack:
    track_id: str
    allocation: float
    objective: str
    directives: tuple[str, ...]
    source_priorities: tuple[str, ...]


@dataclass(frozen=True)
class KnownCandidate:
    candidate_id: str
    institution: str
    title: str
    route: str
    status: str
    last_verified: date | None


@dataclass(frozen=True)
class DiscoveryBrief:
    schema_version: int
    worker_id: str
    agenda_id: str
    assignment_id: str
    run_kind: str
    created_at: datetime
    search_since: date | None
    objective: str
    max_candidates: int
    domestic_eligibility_countries: tuple[str, ...] | None
    profile_facts: tuple[str, ...]
    hard_rules: tuple[str, ...]
    tracks: tuple[DiscoveryTrack, ...]
    known_candidates: tuple[KnownCandidate, ...]


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DiscoveryBriefError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    detail = []
    if missing := sorted(expected - actual):
        detail.append(f"missing {', '.join(missing)}")
    if extra := sorted(actual - expected):
        detail.append(f"unexpected {', '.join(extra)}")
    raise DiscoveryBriefError(f"{label} fields are invalid: {'; '.join(detail)}")


def _text(value: object, label: str, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise DiscoveryBriefError(f"{label} is required")
    if len(text) > max_chars:
        raise DiscoveryBriefError(f"{label} exceeds {max_chars} characters")
    return text


def _strings(
    value: object,
    label: str,
    *,
    limit: int,
    max_chars: int,
    required: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit or (required and not value):
        qualifier = "1-" if required else "at most "
        raise DiscoveryBriefError(f"{label} must contain {qualifier}{limit} strings")
    result = tuple(
        _text(item, f"{label}[{index}]", max_chars=max_chars)
        for index, item in enumerate(value)
    )
    if len(result) != len(set(result)):
        raise DiscoveryBriefError(f"{label} contains duplicates")
    return result


def _datetime(value: object, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DiscoveryBriefError(f"{label} must be an ISO-8601 datetime") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DiscoveryBriefError(f"{label} must include a timezone")
    return result


def _date(value: object, label: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DiscoveryBriefError(f"{label} must be an ISO date") from exc


def _track(value: object, index: int) -> DiscoveryTrack:
    label = f"tracks[{index}]"
    row = _object(value, label)
    _exact_keys(
        row,
        {"track_id", "allocation", "objective", "directives", "source_priorities"},
        label,
    )
    track_id = _text(row["track_id"], f"{label}.track_id", max_chars=32)
    if not TRACK_ID.fullmatch(track_id):
        raise DiscoveryBriefError(f"{label}.track_id must be upper snake case")
    try:
        allocation = float(row["allocation"])
    except (TypeError, ValueError) as exc:
        raise DiscoveryBriefError(f"{label}.allocation must be numeric") from exc
    if not isfinite(allocation) or allocation <= 0 or allocation > 1:
        raise DiscoveryBriefError(f"{label}.allocation must be greater than 0 and at most 1")
    return DiscoveryTrack(
        track_id=track_id,
        allocation=round(allocation, 4),
        objective=_text(row["objective"], f"{label}.objective", max_chars=400),
        directives=_strings(
            row["directives"], f"{label}.directives", limit=12, max_chars=280
        ),
        source_priorities=_strings(
            row["source_priorities"],
            f"{label}.source_priorities",
            limit=10,
            max_chars=160,
        ),
    )


def _known_candidate(value: object, index: int) -> KnownCandidate:
    label = f"known_candidates[{index}]"
    row = _object(value, label)
    _exact_keys(
        row,
        {"candidate_id", "institution", "title", "route", "status", "last_verified"},
        label,
    )
    return KnownCandidate(
        candidate_id=_text(row["candidate_id"], f"{label}.candidate_id", max_chars=80),
        institution=_text(row["institution"], f"{label}.institution", max_chars=180),
        title=_text(row["title"], f"{label}.title", max_chars=240),
        route=_text(row["route"], f"{label}.route", max_chars=100),
        status=_text(row["status"], f"{label}.status", max_chars=100),
        last_verified=_date(row["last_verified"], f"{label}.last_verified"),
    )


def validate_discovery_brief(payload: object) -> DiscoveryBrief:
    """Validate and freeze one bounded, agenda-authored Lucan assignment."""

    row = _object(payload, "brief")
    schema_version = row.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise DiscoveryBriefError("brief schema_version is unsupported")
    expected = {
        "schema_version", "worker_id", "agenda_id", "assignment_id", "run_kind",
        "created_at", "search_since", "objective", "max_candidates",
        "profile_facts", "hard_rules", "tracks", "known_candidates",
    }
    if schema_version == SCHEMA_VERSION:
        expected.add("domestic_eligibility_countries")
    _exact_keys(
        row,
        expected,
        "brief",
    )
    if row["worker_id"] != "lucan":
        raise DiscoveryBriefError("brief worker_id must be lucan")
    agenda_id = _text(row["agenda_id"], "brief.agenda_id", max_chars=80)
    assignment_id = _text(row["assignment_id"], "brief.assignment_id", max_chars=80)
    if not ASSIGNMENT_ID.fullmatch(assignment_id):
        raise DiscoveryBriefError("brief.assignment_id contains unsupported characters")
    run_kind = _text(row["run_kind"], "brief.run_kind", max_chars=40)
    if run_kind not in RUN_KINDS:
        raise DiscoveryBriefError("brief run_kind is unsupported")
    created_at = _datetime(row["created_at"], "brief.created_at")
    search_since = _date(row["search_since"], "brief.search_since")
    if search_since is not None and search_since > created_at.date():
        raise DiscoveryBriefError("brief.search_since cannot follow created_at")
    try:
        max_candidates = int(row["max_candidates"])
    except (TypeError, ValueError) as exc:
        raise DiscoveryBriefError("brief.max_candidates must be an integer") from exc
    if max_candidates < 1 or max_candidates > MAX_CANDIDATES:
        raise DiscoveryBriefError(
            f"brief.max_candidates must be between 1 and {MAX_CANDIDATES}"
        )
    raw_tracks = row["tracks"]
    if not isinstance(raw_tracks, list) or not 1 <= len(raw_tracks) <= MAX_TRACKS:
        raise DiscoveryBriefError(f"brief.tracks must contain 1-{MAX_TRACKS} tracks")
    tracks = tuple(_track(item, index) for index, item in enumerate(raw_tracks))
    track_ids = [item.track_id for item in tracks]
    if len(track_ids) != len(set(track_ids)):
        raise DiscoveryBriefError("brief.tracks contains duplicate track IDs")
    if abs(sum(item.allocation for item in tracks) - 1.0) > 0.001:
        raise DiscoveryBriefError("brief track allocations must sum to 1")
    raw_known = row["known_candidates"]
    if not isinstance(raw_known, list) or len(raw_known) > MAX_KNOWN_CANDIDATES:
        raise DiscoveryBriefError(
            f"brief.known_candidates must contain at most {MAX_KNOWN_CANDIDATES} records"
        )
    known_candidates = tuple(
        _known_candidate(item, index) for index, item in enumerate(raw_known)
    )
    if any(
        item.last_verified is not None and item.last_verified > created_at.date()
        for item in known_candidates
    ):
        raise DiscoveryBriefError("known candidate verification cannot follow created_at")
    known_ids = [item.candidate_id for item in known_candidates]
    if len(known_ids) != len(set(known_ids)):
        raise DiscoveryBriefError("brief.known_candidates contains duplicate candidate IDs")
    return DiscoveryBrief(
        schema_version=schema_version,
        worker_id="lucan",
        agenda_id=agenda_id,
        assignment_id=assignment_id,
        run_kind=run_kind,
        created_at=created_at,
        search_since=search_since,
        objective=_text(row["objective"], "brief.objective", max_chars=600),
        max_candidates=max_candidates,
        domestic_eligibility_countries=(
            _strings(
                row["domestic_eligibility_countries"],
                "brief.domestic_eligibility_countries",
                limit=8,
                max_chars=80,
            )
            if schema_version == SCHEMA_VERSION
            else None
        ),
        profile_facts=_strings(
            row["profile_facts"],
            "brief.profile_facts",
            limit=MAX_PROFILE_FACTS,
            max_chars=240,
        ),
        hard_rules=_strings(
            row["hard_rules"],
            "brief.hard_rules",
            limit=MAX_HARD_RULES,
            max_chars=280,
        ),
        tracks=tracks,
        known_candidates=known_candidates,
    )
