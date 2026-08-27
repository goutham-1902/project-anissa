from __future__ import annotations
from datetime import date, datetime

LONG_TERM_DEFAULT=0.60
INDIA_DEFAULT=0.40
WORKLOAD_EVENT_TYPES={
    'ANISSA_RECOMMENDED_SURGE',
    'USER_INITIATED_SURGE',
    'EXTERNAL_CAPACITY',
}
OPEN_TASK_STATES={'Not Started','Started','Blocked'}

def discovery_ratios(india_status: str) -> tuple[float,float]:
    s=(india_status or '').upper()
    if s in {'SECURED','JOINED'}:
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


def replan_preview(tasks: list[dict], event: dict, changes: list[dict]) -> dict:
    validate_workload_event(event)
    by_id={str(row.get('Task ID')):row for row in tasks if row.get('Task ID')}
    consequential=[]
    affected=[]
    before=sum(task_remaining_minutes(row) for row in tasks)
    simulated=[dict(row) for row in tasks]
    simulated_by_id={str(row.get('Task ID')):row for row in simulated if row.get('Task ID')}
    for change in changes:
        task_id_value=str(change.get('Task ID') or '')
        current=by_id.get(task_id_value)
        if current and is_consequential_task_change(current,change):
            consequential.append(task_id_value)
        if task_id_value:
            affected.append(task_id_value)
        if task_id_value in simulated_by_id:
            simulated_by_id[task_id_value].update(change)
        else:
            simulated.append(dict(change))
    after=sum(task_remaining_minutes(row) for row in simulated)
    return {
        'event_type':str(event.get('Event Type') or '').upper(),
        'title':event.get('Title'),
        'affected_task_ids':affected,
        'consequential_task_ids':consequential,
        'open_minutes_before':before,
        'open_minutes_after':after,
        'requires_consequential_approval':bool(consequential),
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
