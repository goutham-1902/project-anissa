from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from project.discovery_publication import (
    DiscoveryPublicationError,
    read_discovery_publication,
)
from project.telemetry_contract import IST


FRESH_FOR = timedelta(days=10)
RETAIN_POSITIVE_FOR = timedelta(days=28)
MAX_CONTEXT_CANDIDATES = 12


def _unavailable(reason: str, *, source_state: str = "UNKNOWN") -> dict:
    return {
        "schema_version": 1,
        "availability": "unavailable",
        "data_policy": "ignore",
        "source_state": source_state,
        "freshness": "unavailable",
        "reason": reason,
        "guardrail": "No Lucan result may be treated as evidence that no opportunities exist.",
        "candidates": [],
    }


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def discovery_context(
    publication_root: Path,
    *,
    agenda_id: str,
    now: datetime | None = None,
    max_candidates: int = MAX_CONTEXT_CANDIDATES,
) -> dict:
    """Return Anissa's bounded, read-only view of Lucan's last valid publication."""

    if not 1 <= max_candidates <= MAX_CONTEXT_CANDIDATES:
        raise ValueError(
            f"max_candidates must be between 1 and {MAX_CONTEXT_CANDIDATES}"
        )
    moment = now or datetime.now(IST)
    moment = (
        moment.replace(tzinfo=IST)
        if moment.tzinfo is None
        else moment.astimezone(IST)
    )
    try:
        stored = read_discovery_publication(Path(publication_root), retries=1)
    except DiscoveryPublicationError as exc:
        return _unavailable(f"Lucan publication is invalid: {exc}")

    state = str(stored.status.get("state") or "UNKNOWN").upper()
    channel = str(stored.status.get("channel") or "SHADOW").upper()
    publication = stored.publication
    if channel != "LIVE":
        return _unavailable(
            "Lucan publication is not live.",
            source_state=state,
        )
    if publication is None:
        return _unavailable(
            "Lucan has no retained successful publication.",
            source_state=state,
        )
    if publication.agenda_id != agenda_id:
        return _unavailable(
            "Lucan publication belongs to another agenda.",
            source_state=state,
        )

    completed = publication.completed_at.astimezone(IST)
    age = max(timedelta(0), moment - completed)
    if age > RETAIN_POSITIVE_FOR:
        return _unavailable(
            "Lucan publication is too old for campaign evaluation.",
            source_state=state,
        )
    if state == "FAILED" or publication.status == "PARTIAL" or age > FRESH_FOR:
        policy = "positive_only"
        freshness = "recent_stale" if age > FRESH_FOR else "degraded"
    else:
        policy = "full"
        freshness = "fresh"

    candidates = []
    for candidate in publication.candidates[:max_candidates]:
        candidates.append({
            "candidate_id": candidate.candidate_id,
            "institution": candidate.institution,
            "title": candidate.title,
            "route": candidate.route,
            "country": candidate.country,
            "location": candidate.location,
            "deadline": candidate.deadline.isoformat() if candidate.deadline else None,
            "discovery_url": candidate.discovery_url,
            "official_url": candidate.official_url,
            "funding_text": _clip(candidate.funding_text, 280),
            "eligibility_text": _clip(candidate.eligibility_text, 280),
            "relevance_note": _clip(candidate.relevance_note, 240),
            "verification_state": candidate.verification_state,
            "cautions": list(candidate.cautions[:6]),
            "evidence": [{
                "url": evidence.url,
                "source_kind": evidence.source_kind,
                "checked_at": evidence.checked_at.isoformat(),
                "claim": _clip(evidence.claim, 220),
            } for evidence in candidate.evidence[:3]],
        })

    result = {
        "schema_version": 1,
        "availability": "available",
        "data_policy": policy,
        "source_state": state,
        "freshness": freshness,
        "completed_at": completed.isoformat(),
        "age_hours": round(age.total_seconds() / 3600, 1),
        "assignment_id": publication.assignment_id,
        "run_kind": publication.run_kind,
        "result_count": len(publication.candidates),
        "returned_count": len(candidates),
        "source_failures": list(publication.source_failures[:5]),
        "guardrail": (
            "Candidates are advisory discovery evidence only. Anissa must independently "
            "apply campaign policy and verify material claims before any WorkbookGateway mutation."
        ),
        "candidates": candidates,
    }
    if stored.status.get("last_error"):
        result["last_error"] = stored.status["last_error"]
    return result
