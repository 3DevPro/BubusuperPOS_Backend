import uuid
from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.tenancy import TenantContext
from app.core.turbo_config import CLAIMABLE_CLOSE_REASONS
from app.models.turbo.daily_close import DailyClose, DailyCloseReason
from app.models.turbo.insurance import (
    InsuranceClaim,
    InsuranceClaimStatus,
    InsurancePolicy,
    InsurancePolicyStatus,
    InsuranceProduct,
    InsuranceProductKind,
)
from app.schemas.turbo.insurance import DetectedClaimResponse
from app.services import audit_service

_CLAIMABLE_REASONS = [DailyCloseReason(r) for r in CLAIMABLE_CLOSE_REASONS]


async def _get_daily_income_policy(ctx: TenantContext, policy_id: uuid.UUID) -> InsurancePolicy:
    result = (
        await ctx.db.execute(
            select(InsurancePolicy, InsuranceProduct.kind)
            .join(InsuranceProduct, InsuranceProduct.id == InsurancePolicy.product_id)
            .where(InsurancePolicy.id == policy_id, InsurancePolicy.tenant_id == ctx.tenant_id)
        )
    ).one_or_none()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "policy not found")
    policy, kind = result
    if kind != InsuranceProductKind.daily_income:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "เฉพาะกรมธรรม์ชดเชยรายได้รายวันเท่านั้นที่เคลมอัตโนมัติได้"
        )
    if policy.status != InsurancePolicyStatus.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "กรมธรรม์นี้ไม่ได้ใช้งานอยู่")
    return policy


async def _claimed_dates(ctx: TenantContext, policy_id: uuid.UUID) -> set[date]:
    """Every date already covered by a non-rejected claim on this policy —
    a day can only ever be paid out once."""
    claims = await ctx.db.scalars(
        ctx.scoped(InsuranceClaim).where(
            InsuranceClaim.policy_id == policy_id, InsuranceClaim.status != InsuranceClaimStatus.rejected
        )
    )
    covered: set[date] = set()
    for claim in claims:
        d = claim.start_date
        while d <= claim.end_date:
            covered.add(d)
            d += timedelta(days=1)
    return covered


async def _eligible_closes(ctx: TenantContext, policy: InsurancePolicy) -> list[DailyClose]:
    result = await ctx.db.scalars(
        ctx.scoped(DailyClose)
        .where(
            DailyClose.closed_reason.in_(_CLAIMABLE_REASONS),
            DailyClose.business_date >= policy.starts_at.date(),
        )
        .order_by(DailyClose.business_date)
    )
    return list(result)


def _group_consecutive(closes: list[DailyClose]) -> list[list[DailyClose]]:
    runs: list[list[DailyClose]] = []
    for close in closes:
        if runs and (close.business_date - runs[-1][-1].business_date).days == 1:
            runs[-1].append(close)
        else:
            runs.append([close])
    return runs


async def detect_claimable_periods(ctx: TenantContext, policy_id: uuid.UUID) -> list[DetectedClaimResponse]:
    """Un-persisted claim proposals — nothing here writes anything. Groups
    consecutive sick/accident DailyClose days that aren't already covered by
    an existing claim into one draft per run, mirroring the pitch's
    "3 consecutive days = ฿4,500" example."""
    policy = await _get_daily_income_policy(ctx, policy_id)
    claimed = await _claimed_dates(ctx, policy_id)
    closes = [c for c in await _eligible_closes(ctx, policy) if c.business_date not in claimed]

    return [
        DetectedClaimResponse(
            policy_id=policy.id,
            start_date=run[0].business_date,
            end_date=run[-1].business_date,
            days=len(run),
            benefit_amount=policy.daily_benefit * len(run),
            reasons={str(c.business_date): c.closed_reason.value for c in run},
        )
        for run in _group_consecutive(closes)
    ]


async def create_claim(
    ctx: TenantContext, policy_id: uuid.UUID, start_date: date, end_date: date
) -> InsuranceClaim:
    """Re-derives eligibility from DailyClose at confirm time rather than
    trusting a client-supplied amount — a caller can only ever get back
    exactly what the current evidence justifies, same "server re-validates"
    posture as the chatbot's propose/confirm flow (see chatbot/app/ai/tools.py)."""
    if start_date > end_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date must not be after end_date")

    policy = await _get_daily_income_policy(ctx, policy_id)
    claimed = await _claimed_dates(ctx, policy_id)
    closes_by_date = {c.business_date: c for c in await _eligible_closes(ctx, policy)}

    reasons: dict[str, str] = {}
    d = start_date
    while d <= end_date:
        if d in claimed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"วันที่ {d} ถูกเคลมไปแล้ว")
        close = closes_by_date.get(d)
        if close is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"วันที่ {d} ไม่มีหลักฐานร้านปิดจากเจ็บป่วย/อุบัติเหตุ"
            )
        reasons[str(d)] = close.closed_reason.value
        d += timedelta(days=1)

    days = (end_date - start_date).days + 1
    benefit_amount = policy.daily_benefit * days

    claim = InsuranceClaim(
        tenant_id=ctx.tenant_id,
        policy_id=policy.id,
        start_date=start_date,
        end_date=end_date,
        days=days,
        benefit_amount=benefit_amount,
        evidence={"reasons": reasons},
        status=InsuranceClaimStatus.approved,
    )
    ctx.db.add(claim)
    await audit_service.record(
        ctx, "insurance.claim", f"เคลมประกัน {days} วัน ({start_date} ถึง {end_date}) {benefit_amount} บาท"
    )
    await ctx.db.commit()
    await ctx.db.refresh(claim)
    return claim


async def list_claims(ctx: TenantContext) -> list[InsuranceClaim]:
    result = await ctx.db.scalars(ctx.scoped(InsuranceClaim).order_by(InsuranceClaim.created_at.desc()))
    return list(result)
