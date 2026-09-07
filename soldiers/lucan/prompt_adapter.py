from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import re
from typing import Mapping

from project.discovery_brief import DiscoveryBrief, validate_discovery_brief
from project.discovery_contract import (
    DiscoveryPublication,
    discovery_publication_payload,
    discovery_candidate_id,
    validate_discovery_publication,
)


MAX_PROMPT_CHARS = 18_000
RAW_RESULT_FIELDS = {"status", "candidates", "source_failures"}
RAW_CANDIDATE_FIELDS = {
    "institution", "title", "route", "country", "location", "deadline",
    "discovery_url", "official_url", "funding_text", "eligibility_text",
    "relevance_note", "verification_state", "cautions", "evidence",
}
DOMESTIC_ONLY = re.compile(
    r"\b(?:domestic[- ]only|domestic (?:candidates|applicants|students) only|"
    r"only domestic (?:candidates|applicants|students))\b",
    re.IGNORECASE,
)


class PromptAdapterError(ValueError):
    """A Lucan prompt or raw model result is unsafe to use."""


@dataclass(frozen=True)
class PromptPackage:
    assignment_id: str
    run_kind: str
    model: str
    reasoning_effort: str
    prompt: str
    approximate_input_tokens: int


def execution_channel(settings: object) -> str:
    """Return the only publication channel allowed by coherent worker settings."""

    row = _mapping(settings, "settings")
    if row.get("schema_version") != 2:
        raise PromptAdapterError("Lucan settings schema_version is unsupported")
    mode = str(row.get("mode") or "").strip().upper()
    automation = str(row.get("automation_status") or "").strip().upper()
    publication = _mapping(row.get("publication"), "settings.publication")
    channel = str(publication.get("state") or "").strip().upper()
    coherent = {
        ("SETUP", "DISABLED", "SHADOW"): "SHADOW",
        ("LIVE", "ACTIVE", "LIVE"): "LIVE",
    }
    try:
        return coherent[(mode, automation, channel)]
    except KeyError as exc:
        raise PromptAdapterError(
            "Lucan settings are incoherent; expected disabled SETUP/SHADOW or "
            "active LIVE/LIVE"
        ) from exc


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PromptAdapterError(f"{label} must be an object")
    return value


def _profile(settings: object, run_kind: str) -> tuple[str, str]:
    row = _mapping(settings, "settings")
    execution_channel(row)
    model_policy = _mapping(row.get("model_policy"), "settings.model_policy")
    key = "scheduled" if run_kind == "SCHEDULED_SWEEP" else "assigned_elaborate"
    profile = _mapping(model_policy.get(key), f"settings.model_policy.{key}")
    model = str(profile.get("model") or "").strip()
    effort = str(profile.get("reasoning_effort") or "").strip()
    if not model or not effort:
        raise PromptAdapterError(f"settings.model_policy.{key} is incomplete")
    return model, effort


def _brief_payload(brief: DiscoveryBrief) -> dict:
    payload = asdict(brief)
    if brief.domestic_eligibility_countries is None:
        payload.pop("domestic_eligibility_countries")
    payload["created_at"] = brief.created_at.isoformat()
    payload["search_since"] = brief.search_since.isoformat() if brief.search_since else None
    for index, candidate in enumerate(brief.known_candidates):
        payload["known_candidates"][index]["last_verified"] = (
            candidate.last_verified.isoformat() if candidate.last_verified else None
        )
    return payload


def build_prompt(brief_payload: object, settings: object) -> PromptPackage:
    """Render one bounded research prompt; perform no browsing or state mutation."""

    brief = validate_discovery_brief(brief_payload)
    model, effort = _profile(settings, brief.run_kind)
    compact_brief = json.dumps(
        _brief_payload(brief), ensure_ascii=False, separators=(",", ":")
    )
    eligibility_rule = (
        "BRIEF_JSON.domestic_eligibility_countries is the complete list of "
        "countries where the applicant qualifies as a domestic candidate. Do "
        "not return opportunities explicitly restricted to domestic candidates "
        "in any other country."
        if brief.domestic_eligibility_countries is not None
        else None
    )
    instructions = [
        "You are Lucan, Project Anissa's research-only opportunity-discovery worker.",
        "Use web search for this assignment. Search broadly enough to satisfy each enabled track, then verify material claims against primary official sources.",
        "Return new candidates or material changes to known candidates only. Treat indexes and professional networks as discovery leads, not primary verification.",
        "Never rank for the campaign, recommend applying, create tasks, change application status, draft application prose, or infer missing facts.",
        "If a source is unavailable or evidence conflicts, preserve the caution and report PARTIAL instead of guessing. PRIMARY_VERIFIED requires an official URL and OFFICIAL evidence.",
        "Respect the brief's maximum candidate count and track allocation approximately. Deduplicate equivalent opportunities across sources.",
        "Return exactly one JSON object with fields status, candidates, source_failures and no prose or Markdown fence.",
        "Each candidate must contain exactly: institution, title, route, country, location, deadline, discovery_url, official_url, funding_text, eligibility_text, relevance_note, verification_state, cautions, evidence.",
        "Use null for an unknown deadline or official_url. verification_state must be DISCOVERED, NEEDS_VERIFICATION, or PRIMARY_VERIFIED.",
        "Each evidence record must contain exactly url, source_kind, checked_at, claim. source_kind must be OFFICIAL, LAB, INDEX, or PROFESSIONAL_NETWORK; checked_at must include a timezone.",
        "status must be COMPLETE with no source_failures, PARTIAL with at least one source failure, or FAILED with failures and no candidates.",
        "Do not generate candidate IDs or envelope metadata; the local adapter supplies and validates them deterministically.",
    ]
    if eligibility_rule is not None:
        instructions.insert(3, eligibility_rule)
    instructions.append(f"BRIEF_JSON={compact_brief}")
    prompt = "\n".join(instructions)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise PromptAdapterError(
            f"rendered prompt exceeds the {MAX_PROMPT_CHARS}-character budget"
        )
    return PromptPackage(
        assignment_id=brief.assignment_id,
        run_kind=brief.run_kind,
        model=model,
        reasoning_effort=effort,
        prompt=prompt,
        approximate_input_tokens=(len(prompt) + 3) // 4,
    )


def _raw_result(value: object) -> Mapping[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PromptAdapterError("model result must be one plain JSON object") from exc
    row = _mapping(value, "model result")
    if set(row) != RAW_RESULT_FIELDS:
        extra = sorted(set(row) - RAW_RESULT_FIELDS)
        missing = sorted(RAW_RESULT_FIELDS - set(row))
        detail = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if extra:
            detail.append(f"unexpected {', '.join(extra)}")
        raise PromptAdapterError(f"model result fields are invalid: {'; '.join(detail)}")
    candidates = row.get("candidates")
    if not isinstance(candidates, list):
        raise PromptAdapterError("model result candidates must be a list")
    for index, item in enumerate(candidates):
        candidate = _mapping(item, f"model result candidates[{index}]")
        if set(candidate) != RAW_CANDIDATE_FIELDS:
            raise PromptAdapterError(
                f"model result candidates[{index}] fields are invalid"
            )
    return row


def assemble_publication(
    brief_payload: object,
    model_result: object,
    *,
    started_at: datetime,
    completed_at: datetime,
) -> DiscoveryPublication:
    """Add trusted metadata and IDs to one raw result, then validate it."""

    brief = validate_discovery_brief(brief_payload)
    result = _raw_result(model_result)
    if len(result["candidates"]) > brief.max_candidates:
        raise PromptAdapterError("model result exceeds the brief candidate limit")
    candidates = []
    for raw in result["candidates"]:
        candidate = dict(raw)
        if brief.domestic_eligibility_countries is not None:
            eligible_countries = {
                country.casefold() for country in brief.domestic_eligibility_countries
            }
            candidate_country = str(candidate["country"]).strip().casefold()
            eligibility_text = str(candidate["eligibility_text"])
            if (
                candidate_country not in eligible_countries
                and DOMESTIC_ONLY.search(eligibility_text)
            ):
                raise PromptAdapterError(
                    "model result includes a domestic-only candidate outside the "
                    "applicant's eligible countries"
                )
        candidate["candidate_id"] = discovery_candidate_id(
            str(candidate["institution"]),
            str(candidate["title"]),
            str(candidate["route"]),
            str(candidate.get("official_url") or ""),
        )
        candidates.append(candidate)
    return validate_discovery_publication({
        "schema_version": 1,
        "worker_id": "lucan",
        "agenda_id": brief.agenda_id,
        "assignment_id": brief.assignment_id,
        "run_kind": brief.run_kind,
        "status": result["status"],
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "candidates": candidates,
        "source_failures": result["source_failures"],
    })


def assemble_shadow_publication(
    brief_payload: object,
    model_result: object,
    *,
    started_at: datetime,
    completed_at: datetime,
) -> DiscoveryPublication:
    """Compatibility alias retained for earlier shadow-trial callers."""

    return assemble_publication(
        brief_payload,
        model_result,
        started_at=started_at,
        completed_at=completed_at,
    )


def publication_payload(publication: DiscoveryPublication) -> dict:
    """Compatibility alias for the shared contract's canonical JSON projection."""

    return discovery_publication_payload(publication)
