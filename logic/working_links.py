"""Pure, bounded working-link context for a canonical task row.

Verification evidence is a JSON object with nonempty ``authority``,
``relevance`` and ``entrypoint`` attestations. Recording those attestations is
an editorial source check; this resolver never visits a website.
"""

from __future__ import annotations

from datetime import date, datetime
from ipaddress import ip_address
import json
import re
from urllib.parse import parse_qsl, urlsplit

from project.telemetry_contract import IST


LINK_FIELDS = (
    "Working Link URL", "Working Link Verified At",
    "Working Link Verification Evidence", "Working Link Unblock Action",
)
_SECRET_KEY = re.compile(r"(?:token|session|secret|password|passwd|auth|jwt|api.?key|code|credential|sid|state|nonce|ticket|sso|access)", re.I)
_SECRET_EVIDENCE = re.compile(r"\b(?:token|session|secret|password|passwd|jwt|api.?key|credential)s?\b", re.I)
_REQUIRED_EVIDENCE = ("authority", "relevance", "entrypoint")


def working_link_required(control: dict) -> bool:
    return str(control.get("task_working_link_required") or "").strip().lower() in {"true", "yes", "1"}


def working_link_max_age(control: dict) -> int:
    raw = control.get("working_link_max_age_days")
    if raw in (None, ""):
        return 7
    value = int(raw)
    if value < 0:
        raise ValueError("working_link_max_age_days must not be negative")
    return value


def _safe_url(value: str) -> bool:
    if not value or any(ord(char) < 33 for char in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.port == 0):
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            address = ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return False
        if parsed.fragment:
            return False
        return not any(
            _SECRET_KEY.search(key) or len(value) > 80 or (
                value.count(".") == 2 and value.startswith("eyJ")
            )
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        )
    except ValueError:
        return False


def _attestation(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        claims = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(claims, dict):
        return None
    if any(not isinstance(claims.get(key), str) or not claims[key].strip() for key in _REQUIRED_EVIDENCE):
        return None
    if any(len(claims[key]) > 240 for key in _REQUIRED_EVIDENCE):
        return None
    authority = claims["authority"].lower()
    if not ("official" in authority or "delegat" in authority):
        return None
    if any(_SECRET_EVIDENCE.search(str(item)) for item in claims.values()):
        return None
    return json.dumps({key: claims[key].strip() for key in _REQUIRED_EVIDENCE}, ensure_ascii=False)


def _verified_day(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(IST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(IST).date() if parsed.tzinfo else parsed.date()
    except (TypeError, ValueError):
        return None


def task_working_link(
    row: dict, *, as_of: date, required: bool = True, max_age_days: int = 7,
) -> dict | None:
    """Expose a URL only when safe, attested and fresh in IST calendar days."""
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise TypeError("as_of must be a calendar date")
    if max_age_days < 0:
        raise ValueError("max_age_days must not be negative")
    metadata = [row.get(key) for key in LINK_FIELDS]
    if not required and all(value in (None, "") for value in metadata):
        return None
    raw_url = str(row.get(LINK_FIELDS[0]) or "").strip()
    raw_day = row.get(LINK_FIELDS[1])
    evidence = _attestation(row.get(LINK_FIELDS[2]))
    day = _verified_day(raw_day) if raw_day not in (None, "") else None
    unblock = str(row.get(LINK_FIELDS[3]) or "").strip()
    reason = None
    if not raw_url:
        status, reason = "missing", "no task-specific URL is recorded"
    elif not _safe_url(raw_url):
        status, reason = "invalid", "the recorded URL is unsafe or malformed"
    elif raw_day not in (None, "") and day is None:
        status, reason = "invalid", "the verification date is invalid"
    elif day is not None and day > as_of:
        status, reason = "invalid", "the verification date is in the future"
    elif day is None or evidence is None:
        status, reason = "unverified", "source, task relevance, entrypoint or date is not attested"
    elif (as_of - day).days > max_age_days:
        status, reason = "stale", "the source check has expired"
    else:
        status = "verified"
    if status == "verified":
        return {
            "url": raw_url, "status": status, "verified_at": day.isoformat(),
            "verification_evidence": evidence, "blocker": None, "unblock_action": None,
        }
    return {
        "url": None, "status": status,
        "verified_at": day.isoformat() if day is not None else None,
        "verification_evidence": evidence,
        "blocker": f"Verified link missing: {reason}.",
        "unblock_action": unblock or (
            "Check the current official or authoritatively delegated task entrypoint, "
            "record its safe URL, verification date and source/relevance/entrypoint attestations."
        ),
    }


def validate_task_link_write(
    row: dict, *, as_of: date, required: bool, max_age_days: int = 7,
) -> None:
    """Reject unsafe submitted metadata and require a recorded recovery action."""
    if not required and all(row.get(key) in (None, "") for key in LINK_FIELDS):
        return
    link = task_working_link(row, as_of=as_of, required=required, max_age_days=max_age_days)
    if link is None:
        return
    if link["status"] == "invalid":
        raise ValueError(link["blocker"])
    if link["status"] != "verified" and not str(row.get(LINK_FIELDS[3]) or "").strip():
        raise ValueError("Unverified task link requires an explicit Working Link Unblock Action")
