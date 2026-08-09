from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.turbo_config import DAILY_INCOME_PREMIUM_RATE
from app.models.turbo.branch import Branch, Lead, LeadSource
from app.schemas.turbo.public import PublicQuoteRequest, PublicQuoteResponse

_CENTS = Decimal("0.01")
_DAYS_PER_MONTH = Decimal("30")


async def _pick_branch(db: AsyncSession, province: str | None) -> Branch:
    if province:
        branch = await db.scalar(
            select(Branch).where(Branch.province == province).order_by(func.random()).limit(1)
        )
        if branch is not None:
            return branch
    branch = await db.scalar(select(Branch).order_by(func.random()).limit(1))
    if branch is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no branches available to route this lead to")
    return branch


async def quote_and_create_lead(db: AsyncSession, body: PublicQuoteRequest) -> PublicQuoteResponse:
    """Quotes the daily_income product from the *budget* the visitor states —
    the reverse of the in-app quote (see insurance_service.quote), which
    starts from a tenant's actual sales. A prospect has no sales history yet,
    only what they say they can afford."""
    daily_premium = (body.monthly_budget / _DAYS_PER_MONTH).quantize(_CENTS, rounding=ROUND_HALF_UP)
    daily_benefit = (daily_premium / DAILY_INCOME_PREMIUM_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)

    branch = await _pick_branch(db, body.province)

    lead = Lead(
        assigned_branch_id=branch.id,
        source=LeadSource.o2o_web,
        name=body.name,
        phone=body.phone,
        occupation=body.occupation,
        age=body.age,
        quoted_daily_benefit=daily_benefit,
        quoted_premium=daily_premium,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    return PublicQuoteResponse(daily_benefit=daily_benefit, premium_amount=daily_premium, lead_id=lead.id)
