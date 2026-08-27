from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class FundingGrade(str, Enum):
    A='A'; B='B'; PARTIAL='Partial / not A-B alone'; NEEDS='Needs Verification'; REJECT='Reject'

@dataclass(frozen=True)
class FundingInput:
    official_verified: bool
    tuition_covered: bool
    living_covered: bool
    loan_required: bool
    monthly_margin_inr: float | None = None

def classify_funding(x: FundingInput) -> FundingGrade:
    if not x.official_verified:
        return FundingGrade.NEEDS
    if x.loan_required:
        return FundingGrade.REJECT
    if x.tuition_covered and x.living_covered:
        if (x.monthly_margin_inr or 0) > 0:
            return FundingGrade.A
        return FundingGrade.B
    if x.tuition_covered or x.living_covered:
        return FundingGrade.PARTIAL
    return FundingGrade.REJECT
