"""Fail-closed identity check before reading application mail or portal context.

Matching an address is not permission to send, submit, or authenticate a portal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


_ADDRESS = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\Z"
)
_OPERATIONS = {"gmail", "outreach", "portal"}


def _address(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    local = normalized.split("@", 1)[0]
    if (
        not _ADDRESS.fullmatch(normalized)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
    ):
        return None
    return normalized


def _blocked(reason: str) -> dict:
    return {"ok": False, "blocked": True, "reason": reason}


def check_mail_identity(
    expected_email: str,
    profile: dict,
    *,
    operation: str = "gmail",
    portal_email: str | None = None,
) -> dict:
    """Compare exact normalized identities; never infer an alias or permission."""

    if operation not in _OPERATIONS:
        return _blocked("Unsupported mail identity operation")
    expected = _address(expected_email)
    if expected is None:
        return _blocked("Canonical application email is missing or invalid")
    if not isinstance(profile, Mapping):
        return _blocked("Connector profile is missing or invalid")
    observed = []
    for field in ("email", "emailAddress"):
        if field in profile:
            address = _address(profile[field])
            if address is None:
                return _blocked("Connector profile email is missing or invalid")
            observed.append(address)
    if not observed:
        return _blocked("Connector profile email is missing")
    if len(set(observed)) != 1:
        return _blocked("Connector profile email fields conflict")
    if observed[0] != expected:
        return _blocked("Connector profile does not match canonical application email")
    if operation == "portal":
        portal = _address(portal_email)
        if portal is None:
            return _blocked("Independently observed portal email is missing or invalid")
        if portal != expected:
            return _blocked("Portal email does not match canonical application email")
    return {
        "ok": True,
        "blocked": False,
        "reason": "Identity matched; this grants no send, submit, or portal access permission",
    }
