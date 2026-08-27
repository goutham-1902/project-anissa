from __future__ import annotations
from difflib import SequenceMatcher
from .ids import norm_text


def _similar(a: object, b: object, threshold: float = 0.90) -> bool:
    aa, bb = norm_text(a), norm_text(b)
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    return SequenceMatcher(None, aa, bb).ratio() >= threshold


def equivalent_opportunity(a: dict, b: dict) -> bool:
    """Conservative cross-source opportunity dedupe.

    Official URLs are supporting evidence, not sufficient identity by themselves: one
    generic listing page can contain several distinct vacancies. Require institution and
    route agreement plus a very similar title. Discovery-source URLs are intentionally
    not identity-bearing so the same call found on an aggregator and an official page can
    merge.
    """
    if norm_text(a.get('Institution')) != norm_text(b.get('Institution')):
        return False
    if norm_text(a.get('Route')) != norm_text(b.get('Route')):
        return False
    return _similar(a.get('Opportunity'), b.get('Opportunity'), 0.90)


def equivalent_task(a: dict, b: dict) -> bool:
    """Merge task wording variants only inside the same application/campaign category."""
    if norm_text(a.get('Application ID')) != norm_text(b.get('Application ID')):
        return False
    if norm_text(a.get('Campaign')) != norm_text(b.get('Campaign')):
        return False
    if norm_text(a.get('Category')) != norm_text(b.get('Category')):
        return False
    return _similar(a.get('Task Title'), b.get('Task Title'), 0.92)


def equivalent_application(a: dict, b: dict) -> bool:
    if a.get('Target ID') and b.get('Target ID'):
        return (
            norm_text(a.get('Target ID')) == norm_text(b.get('Target ID'))
            and norm_text(a.get('Cycle')) == norm_text(b.get('Cycle'))
        )
    return (
        norm_text(a.get('Institution')) == norm_text(b.get('Institution'))
        and norm_text(a.get('Route')) == norm_text(b.get('Route'))
        and norm_text(a.get('Cycle')) == norm_text(b.get('Cycle'))
        and _similar(a.get('Programme / Position'), b.get('Programme / Position'), 0.92)
    )


def equivalent_scholarship(a: dict, b: dict) -> bool:
    return (
        norm_text(a.get('Target')) == norm_text(b.get('Target'))
        and norm_text(a.get('Sponsor')) == norm_text(b.get('Sponsor'))
        and _similar(a.get('Scholarship'), b.get('Scholarship'), 0.92)
    )
