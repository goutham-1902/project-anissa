VALID_STATUS={'Verified','Supported','User-attested','Public-secondary','Needs Verification','Superseded'}

def claim_allowed(record: dict, proposed: str) -> tuple[bool,str]:
    if record.get('status') not in VALID_STATUS:
        return False, 'Unknown evidence status.'
    forbidden=(record.get('forbidden') or '').strip().lower()
    if forbidden and forbidden in proposed.lower():
        return False, 'Proposed wording matches forbidden/overclaim guidance.'
    return True, 'Review permitted wording and evidence status before use.'
