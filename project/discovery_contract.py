from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Mapping
from urllib.parse import urlsplit

from logic.ids import opportunity_id


SCHEMA_VERSION = 1
MAX_CANDIDATES = 40
RUN_KINDS = {"SCHEDULED_SWEEP", "ASSIGNED_RESEARCH"}
RUN_STATES = {"COMPLETE", "PARTIAL", "FAILED"}
VERIFICATION_STATES = {"DISCOVERED", "NEEDS_VERIFICATION", "PRIMARY_VERIFIED"}
SOURCE_KINDS = {"OFFICIAL", "LAB", "INDEX", "PROFESSIONAL_NETWORK"}


class DiscoveryContractError(ValueError):
    """A Lucan publication is unsafe to pass to an agenda."""


@dataclass(frozen=True)
class DiscoveryEvidence:
    url: str
    source_kind: str
    checked_at: datetime
    claim: str


@dataclass(frozen=True)
class DiscoveryCandidate:
    candidate_id: str
    institution: str
    title: str
    route: str
    country: str
    location: str
    deadline: date | None
    discovery_url: str
    official_url: str | None
    funding_text: str
    eligibility_text: str
    relevance_note: str
    verification_state: str
    cautions: tuple[str, ...]
    evidence: tuple[DiscoveryEvidence, ...]


@dataclass(frozen=True)
class DiscoveryPublication:
    schema_version: int
    worker_id: str
    agenda_id: str
    assignment_id: str
    run_kind: str
    status: str
    started_at: datetime
    completed_at: datetime
    candidates: tuple[DiscoveryCandidate, ...]
    source_failures: tuple[str, ...]


def discovery_candidate_id(
    institution: str,
    title: str,
    route: str,
    official_url: str | None = None,
) -> str:
    """Return the campaign-compatible identity used for cross-source deduplication."""

    return opportunity_id(institution, title, route, official_url or "")


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DiscoveryContractError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if extra:
            detail.append(f"unexpected {', '.join(extra)}")
        raise DiscoveryContractError(f"{label} fields are invalid: {'; '.join(detail)}")


def _text(value: object, label: str, *, optional: bool = False) -> str | None:
    text = " ".join(str(value or "").split())
    if not text and not optional:
        raise DiscoveryContractError(f"{label} is required")
    return text or None


def _url(value: object, label: str, *, optional: bool = False) -> str | None:
    text = _text(value, label, optional=optional)
    if text is None:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DiscoveryContractError(f"{label} must be an absolute HTTP(S) URL")
    return text


def _datetime(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DiscoveryContractError(f"{label} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DiscoveryContractError(f"{label} must include a timezone")
    return parsed


def _date(value: object, label: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DiscoveryContractError(f"{label} must be an ISO date") from exc


def _strings(value: object, label: str, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise DiscoveryContractError(f"{label} must be a list of at most {limit} strings")
    result = tuple(_text(item, label) for item in value)
    if len(result) != len(set(result)):
        raise DiscoveryContractError(f"{label} contains duplicates")
    return result


def _evidence(value: object, index: int) -> DiscoveryEvidence:
    row = _object(value, f"evidence[{index}]")
    expected = {"url", "source_kind", "checked_at", "claim"}
    _exact_keys(row, expected, f"evidence[{index}]")
    source_kind = str(_text(row["source_kind"], f"evidence[{index}].source_kind"))
    if source_kind not in SOURCE_KINDS:
        raise DiscoveryContractError(f"evidence[{index}].source_kind is unsupported")
    return DiscoveryEvidence(
        url=str(_url(row["url"], f"evidence[{index}].url")),
        source_kind=source_kind,
        checked_at=_datetime(row["checked_at"], f"evidence[{index}].checked_at"),
        claim=str(_text(row["claim"], f"evidence[{index}].claim")),
    )


def _candidate(value: object, index: int) -> DiscoveryCandidate:
    row = _object(value, f"candidates[{index}]")
    expected = {
        "candidate_id", "institution", "title", "route", "country", "location",
        "deadline", "discovery_url", "official_url", "funding_text",
        "eligibility_text", "relevance_note", "verification_state", "cautions",
        "evidence",
    }
    _exact_keys(row, expected, f"candidates[{index}]")
    institution = str(_text(row["institution"], f"candidates[{index}].institution"))
    title = str(_text(row["title"], f"candidates[{index}].title"))
    route = str(_text(row["route"], f"candidates[{index}].route"))
    official_url = _url(row["official_url"], f"candidates[{index}].official_url", optional=True)
    candidate_id = str(_text(row["candidate_id"], f"candidates[{index}].candidate_id"))
    expected_id = discovery_candidate_id(institution, title, route, official_url)
    if candidate_id != expected_id:
        raise DiscoveryContractError(f"candidates[{index}].candidate_id is not deterministic")
    verification_state = str(
        _text(row["verification_state"], f"candidates[{index}].verification_state")
    )
    if verification_state not in VERIFICATION_STATES:
        raise DiscoveryContractError(f"candidates[{index}].verification_state is unsupported")
    raw_evidence = row["evidence"]
    if not isinstance(raw_evidence, list) or not raw_evidence or len(raw_evidence) > 12:
        raise DiscoveryContractError(f"candidates[{index}].evidence must contain 1-12 records")
    evidence = tuple(_evidence(item, item_index) for item_index, item in enumerate(raw_evidence))
    if verification_state == "PRIMARY_VERIFIED" and (
        official_url is None or not any(item.source_kind == "OFFICIAL" for item in evidence)
    ):
        raise DiscoveryContractError(
            f"candidates[{index}] cannot be PRIMARY_VERIFIED without official evidence"
        )
    return DiscoveryCandidate(
        candidate_id=candidate_id,
        institution=institution,
        title=title,
        route=route,
        country=str(_text(row["country"], f"candidates[{index}].country")),
        location=str(_text(row["location"], f"candidates[{index}].location")),
        deadline=_date(row["deadline"], f"candidates[{index}].deadline"),
        discovery_url=str(_url(row["discovery_url"], f"candidates[{index}].discovery_url")),
        official_url=official_url,
        funding_text=str(_text(row["funding_text"], f"candidates[{index}].funding_text")),
        eligibility_text=str(_text(row["eligibility_text"], f"candidates[{index}].eligibility_text")),
        relevance_note=str(_text(row["relevance_note"], f"candidates[{index}].relevance_note")),
        verification_state=verification_state,
        cautions=_strings(row["cautions"], f"candidates[{index}].cautions", limit=12),
        evidence=evidence,
    )


def validate_discovery_publication(payload: object) -> DiscoveryPublication:
    """Validate and freeze one bounded Lucan result without mutating agenda state."""

    row = _object(payload, "publication")
    expected = {
        "schema_version", "worker_id", "agenda_id", "assignment_id", "run_kind",
        "status", "started_at", "completed_at", "candidates", "source_failures",
    }
    _exact_keys(row, expected, "publication")
    if row["schema_version"] != SCHEMA_VERSION:
        raise DiscoveryContractError("publication schema_version is unsupported")
    if row["worker_id"] != "lucan":
        raise DiscoveryContractError("publication worker_id must be lucan")
    run_kind = str(_text(row["run_kind"], "publication.run_kind"))
    status = str(_text(row["status"], "publication.status"))
    if run_kind not in RUN_KINDS:
        raise DiscoveryContractError("publication run_kind is unsupported")
    if status not in RUN_STATES:
        raise DiscoveryContractError("publication status is unsupported")
    started_at = _datetime(row["started_at"], "publication.started_at")
    completed_at = _datetime(row["completed_at"], "publication.completed_at")
    if completed_at < started_at:
        raise DiscoveryContractError("publication completed_at precedes started_at")
    raw_candidates = row["candidates"]
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_CANDIDATES:
        raise DiscoveryContractError(
            f"publication candidates must be a list of at most {MAX_CANDIDATES} records"
        )
    candidates = tuple(_candidate(item, index) for index, item in enumerate(raw_candidates))
    ids = [item.candidate_id for item in candidates]
    if len(ids) != len(set(ids)):
        raise DiscoveryContractError("publication contains duplicate candidates")
    failures = _strings(row["source_failures"], "publication.source_failures", limit=20)
    if status == "COMPLETE" and failures:
        raise DiscoveryContractError("COMPLETE publication cannot contain source failures")
    if status == "PARTIAL" and not failures:
        raise DiscoveryContractError("PARTIAL publication requires source failures")
    if status == "FAILED" and (candidates or not failures):
        raise DiscoveryContractError("FAILED publication requires failures and no candidates")
    return DiscoveryPublication(
        schema_version=SCHEMA_VERSION,
        worker_id="lucan",
        agenda_id=str(_text(row["agenda_id"], "publication.agenda_id")),
        assignment_id=str(_text(row["assignment_id"], "publication.assignment_id")),
        run_kind=run_kind,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        candidates=candidates,
        source_failures=failures,
    )


def discovery_publication_payload(publication: DiscoveryPublication) -> dict:
    """Return the canonical JSON-ready form of a validated publication."""

    payload = asdict(publication)
    payload["started_at"] = publication.started_at.isoformat()
    payload["completed_at"] = publication.completed_at.isoformat()
    payload["candidates"] = list(payload["candidates"])
    payload["source_failures"] = list(payload["source_failures"])
    for index, candidate in enumerate(publication.candidates):
        payload["candidates"][index]["deadline"] = (
            candidate.deadline.isoformat() if candidate.deadline else None
        )
        payload["candidates"][index]["cautions"] = list(
            payload["candidates"][index]["cautions"]
        )
        payload["candidates"][index]["evidence"] = list(
            payload["candidates"][index]["evidence"]
        )
        for evidence_index, evidence in enumerate(candidate.evidence):
            payload["candidates"][index]["evidence"][evidence_index]["checked_at"] = (
                evidence.checked_at.isoformat()
            )
    return payload
