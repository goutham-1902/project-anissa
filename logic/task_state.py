from __future__ import annotations
VALID = {'Not Started','Started','Blocked','Done','Cancelled'}

def validate_transition(status: str, *, evidence: str = '', blocker: str = '', unblock_action: str = '', meaningful: bool = True) -> None:
    if status not in VALID:
        raise ValueError(f'Invalid task status: {status}')
    if status == 'Done' and meaningful and not str(evidence or '').strip():
        raise ValueError('Meaningful tasks cannot be marked Done without completion evidence.')
    if status == 'Blocked':
        if not str(blocker or '').strip() or not str(unblock_action or '').strip():
            raise ValueError('Blocked tasks require both Blocker and Unblock Action.')
