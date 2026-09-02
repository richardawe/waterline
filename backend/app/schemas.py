from datetime import date
from typing import Optional

from pydantic import BaseModel


class BlogPostUpdate(BaseModel):
    title: Optional[str] = None
    meta_description: Optional[str] = None
    excerpt: Optional[str] = None
    content_markdown: Optional[str] = None
    tags: Optional[list[str]] = None
    faq: Optional[list[dict]] = None


class BlogTopicCreate(BaseModel):
    prompt: str
    category: str
    target_keywords: Optional[str] = None
    priority: int = 100


class DealCreate(BaseModel):
    originator_institution_id: str
    name: str
    deal_stage: str = "discovery"
    spv_name: Optional[str] = None
    notes: Optional[str] = None


class SPVStructureRequest(BaseModel):
    name: str
    as_of_date: date
    loan_tape_snapshot_id: Optional[str] = None

    # eligibility
    allowed_currencies: tuple[str, ...] = ("NGN",)
    allowed_statuses: tuple[str, ...] = ("ACTIVE",)
    max_days_past_due: int = 30
    exclude_default_flag: bool = True
    exclude_writeoff_flag: bool = True
    min_outstanding_principal: float = 0.0
    max_outstanding_principal: Optional[float] = None
    min_remaining_tenor_days: int = 30
    require_secured: bool = False
    max_single_obligor_pct: float = 0.02
    max_sector_concentration_pct: float = 0.35

    # sizing
    senior_advance_rate: float = 0.80
    mezz_advance_rate: float = 0.10
    senior_coupon_rate: float = 0.19
    mezz_coupon_rate: float = 0.24

    # waterfall
    horizon_months: int = 36
    annual_default_rate: float = 0.05
    annual_prepayment_rate: float = 0.10
    recovery_rate: float = 0.40
    recovery_lag_months: int = 6
    servicing_fee_annual_rate: float = 0.02
