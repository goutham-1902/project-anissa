SOFT_LIMIT_INR = 50_000
HARD_LIMIT_INR = 60_000

def fee_band_research_master(fee_inr: float | int | None) -> str:
    if fee_inr is None:
        return 'VERIFY'
    if fee_inr <= 5_000:
        return 'NORMAL'
    if fee_inr <= 10_000:
        return 'ASK'
    return 'REJECT_UNLESS_PROMOTED'

def budget_state(paid_inr: float, planned_inr: float = 0.0) -> str:
    total = paid_inr + planned_inr
    if total > HARD_LIMIT_INR:
        return 'HARD_LIMIT_EXCEEDED'
    if total > SOFT_LIMIT_INR:
        return 'ABOVE_SOFT_LIMIT'
    return 'OK'
