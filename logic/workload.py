from __future__ import annotations
from datetime import date, datetime
from .dedupe import equivalent_task
from .ids import task_id

LONG_TERM_DEFAULT=0.60
INDIA_DEFAULT=0.40
WORKLOAD_EVENT_TYPES={
    'ANISSA_RECOMMENDED_SURGE',
    'USER_INITIATED_SURGE',
    'EXTERNAL_CAPACITY',
}
OPEN_TASK_STATES={'Not Started','Started','Blocked'}

def discovery_ratios(india_status: str) -> tuple[float,float]:
    """Keep an explicit user-disabled campaign out of future discovery."""
    s=(india_status or '').upper()
    if s in {'SECURED','JOINED','DISABLED'}:
        return 1.0, 0.0
    return LONG_TERM_DEFAULT, INDIA_DEFAULT

def surge_allowed(*, meaningful_deadline: bool, high_value: bool, materially_lowers_risk: bool) -> bool:
    return meaningful_deadline and high_value and materially_lowers_risk


def _yes(value: object) -> bool:
    return str(value or '').strip().upper() in {'YES','TRUE','1'}


def validate_workload_event(event: dict) -> None:
    event_type=str(event.get('Event Type') or '').strip().upper()
    if event_type not in WORKLOAD_EVENT_TYPES:
        raise ValueError(f'Unsupported workload event type: {event_type!r}')
    if not str(event.get('Title') or '').strip():
        raise ValueError('Workload events require a title.')
    if event_type in {'USER_INITIATED_SURGE','EXTERNAL_CAPACITY'} and not _yes(event.get('User Authorized')):
        raise ValueError('User-initiated workload events require explicit user authorization.')
    minutes=event.get('Estimated Minutes')
    if minutes in (None,'') or float(minutes) <= 0:
        raise ValueError('Workload events require positive estimated minutes.')


def task_remaining_minutes(task: dict) -> int:
    if str(task.get('Status') or '') not in OPEN_TASK_STATES:
        return 0
    value=task.get('Remaining Minutes')
    if value in (None,''):
        value=task.get('Estimated Minutes')
    try:
        return max(0,int(float(value or 0)))
    except (TypeError,ValueError):
        return 0


def is_consequential_task_change(current: dict, change: dict) -> bool:
    current_status=str(current.get('Status') or '')
    next_status=str(change.get('Status') or current_status)
    if next_status == 'Cancelled' and current_status != 'Cancelled':
        return True
    if current_status == 'Started' and any(
        key in change and str(change.get(key) or '') != str(current.get(key) or '')
        for key in ('Assigned Date','Due Date','Priority','Campaign')
    ):
        return True
    if 'Due Date' in change and current.get('Due Date') and change.get('Due Date'):
        return str(change['Due Date'])[:10] > str(current['Due Date'])[:10]
    return False


def resolve_replan_changes(tasks: list[dict], changes: list[dict], *, event_id: str | None = None) -> dict:
    """Resolve each change against current and earlier changes in this batch.

    Explicit IDs remain exact. An ID-less change may update only one equivalent
    task; otherwise it creates a deterministic ID or fails closed on ambiguity.
    """
    simulated=[dict(row) for row in tasks]
    known_ids=[str(row.get('Task ID')) for row in simulated if row.get('Task ID')]
    if len(known_ids) != len(set(known_ids)):
        raise ValueError('Replan input has duplicate Task IDs')
    before=sum(task_remaining_minutes(row) for row in simulated)
    affected=[]
    consequential=[]
    prepared=[]
    for raw in changes:
        change=dict(raw)
        if event_id is not None:
            change['Workload Event ID']=change.get('Workload Event ID') or event_id
        explicit_id=str(change.get('Task ID') or '')
        if explicit_id and not explicit_id.strip():
            raise ValueError('Task ID cannot be blank whitespace')
        if explicit_id:
            matches=[row for row in simulated if str(row.get('Task ID') or '') == explicit_id]
        else:
            matches=[row for row in simulated if equivalent_task(row,change)]
        if len(matches) > 1:
            raise ValueError('Replan task change matches multiple existing tasks')
        current=matches[0] if matches else None
        if current:
            resolved_id=str(current.get('Task ID') or '')
            if not resolved_id:
                raise ValueError('Equivalent replan task has no Task ID')
            if is_consequential_task_change(current,change):
                consequential.append(resolved_id)
            change={'Definition of Done':current.get('Definition of Done'),**change,'Task ID':resolved_id}
            current.update(change)
        else:
            resolved_id=explicit_id or task_id(
                str(change.get('Application ID') or ''),str(change.get('Category') or ''),
                str(change.get('Task Title') or ''),str(change.get('Assigned Date') or ''),
            )
            if any(str(row.get('Task ID') or '') == resolved_id for row in simulated):
                raise ValueError('Generated Task ID collides with another task')
            change={**change,'Task ID':resolved_id}
            simulated.append(dict(change))
        affected.append(resolved_id)
        prepared.append(change)
    return {
        'prepared':prepared,
        'affected_task_ids':list(dict.fromkeys(affected)),
        'consequential_task_ids':list(dict.fromkeys(consequential)),
        'open_minutes_before':before,
        'open_minutes_after':sum(task_remaining_minutes(row) for row in simulated),
    }


def replan_preview(tasks: list[dict], event: dict, changes: list[dict]) -> dict:
    validate_workload_event(event)
    resolved=resolve_replan_changes(tasks,changes)
    return {
        'event_type':str(event.get('Event Type') or '').upper(),
        'title':event.get('Title'),
        'affected_task_ids':resolved['affected_task_ids'],
        'consequential_task_ids':resolved['consequential_task_ids'],
        'open_minutes_before':resolved['open_minutes_before'],
        'open_minutes_after':resolved['open_minutes_after'],
        'requires_consequential_approval':bool(resolved['consequential_task_ids']),
    }


def date_value(value: object) -> date | None:
    if isinstance(value,datetime):
        return value.date()
    if isinstance(value,date):
        return value
    text=str(value or '')[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
