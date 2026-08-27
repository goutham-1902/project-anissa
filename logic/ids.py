from __future__ import annotations
import hashlib, re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_KEYS = {'utm_source','utm_medium','utm_campaign','utm_term','utm_content','gclid','fbclid','trk'}


def norm_text(value: object) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


def norm_url(url: str | None) -> str:
    if not url:
        return ''
    raw = url.strip()
    try:
        p = urlsplit(raw)
        q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_KEYS]
        path = re.sub(r'/+$','',p.path or '/')
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(q), ''))
    except Exception:
        return raw.lower().rstrip('/')


def stable_id(prefix: str, *parts: object) -> str:
    payload = '|'.join(norm_text(x) for x in parts)
    return f"{prefix}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def opportunity_id(institution: str, title: str, route: str = '', official_url: str = '') -> str:
    """Stable across discovery sources.

    The canonical identity is institution + title + route. URL is only a fallback when
    those human fields are too incomplete to form a useful identity.
    """
    if norm_text(institution) or norm_text(title) or norm_text(route):
        return stable_id('opp', institution, title, route)
    return stable_id('opp', norm_url(official_url))


def task_id(application_id: str, category: str, title: str, cycle: str = '') -> str:
    return stable_id('task', application_id, category, title, cycle)


def application_id(target_id: str, institution: str, programme: str, cycle: str = '') -> str:
    return stable_id('app', target_id or institution, programme, cycle)


def scholarship_id(target: str, sponsor: str, scholarship: str, cycle: str = '') -> str:
    return stable_id('sch', target, sponsor, scholarship, cycle)


def workload_event_id(event_type: str, title: str, start: object = '', end: object = '') -> str:
    return stable_id('work', event_type, title, start, end)


def weekly_audit_id(week_start: object) -> str:
    return stable_id('audit', week_start)
