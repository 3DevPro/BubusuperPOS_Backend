from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.tenancy import TenantContext
from app.core.turbo_config import DAILY_BENEFIT_RATIO, DAILY_INCOME_PREMIUM_RATE
from app.models.turbo.insurance import InsurancePolicy, InsurancePolicyStatus, InsuranceProduct, InsuranceProductKind
from app.schemas.turbo.insurance import InsuranceQuoteResponse
from app.services import audit_service
from app.services.turbo import income_service

_CENTS = Decimal("0.01")


async def list_products(ctx: TenantContext) -> list[InsuranceProduct]:
    result = await ctx.db.scalars(
        select(InsuranceProduct).where(InsuranceProduct.is_active.is_(True)).order_by(InsuranceProduct.code)
    )
    return list(result)


async def _get_product(ctx: TenantContext, product_code: str) -> InsuranceProduct:
    product = await ctx.db.scalar(
        select(InsuranceProduct).where(InsuranceProduct.code == product_code, InsuranceProduct.is_active.is_(True))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "insurance product not found")
    return product


async def quote(ctx: TenantContext, product_code: str) -> tuple[InsuranceProduct, InsuranceQuoteResponse, dict]:
    """Returns (product, quote, income_profile_snapshot). purchase() calls
    this and persists the same snapshot on the policy it creates, so a
    separate quote call and the policy it leads to always agree on the
    numbers even though they're two independent requests."""
    product = await _get_product(ctx, product_code)

    if product.kind == InsuranceProductKind.daily_income:
        profile = await income_service.get_income_profile(ctx)
        daily_benefit = (profile.avg_daily_revenue * DAILY_BENEFIT_RATIO).quantize(_CENTS, rounding=ROUND_HALF_UP)
        premium = (daily_benefit * DAILY_INCOME_PREMIUM_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)
        quoted = InsuranceQuoteResponse(
            product_code=product.code, daily_benefit=daily_benefit, premium_amount=premium, premium_cycle="daily"
        )
        return product, quoted, profile.model_dump(mode="json")

    quoted = InsuranceQuoteResponse(
        product_code=product.code,
        daily_benefit=Decimal("0"),
        premium_amount=product.flat_monthly_premium,
        premium_cycle="monthly",
    )
    return product, quoted, {}


async def purchase(ctx: TenantContext, product_code: str) -> InsurancePolicy:
    product, quoted, snapshot = await quote(ctx, product_code)

    existing = await ctx.db.scalar(
        ctx.scoped(InsurancePolicy).where(
            InsurancePolicy.product_id == product.id,
            InsurancePolicy.status == InsurancePolicyStatus.active,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "มีกรมธรรม์ประเภทนี้ใช้งานอยู่แล้ว")

    policy = InsurancePolicy(
        tenant_id=ctx.tenant_id,
        product_id=product.id,
        daily_benefit=quoted.daily_benefit,
        premium_amount=quoted.premium_amount,
        premium_cycle=quoted.premium_cycle,
        income_profile_snapshot=snapshot,
    )
    ctx.db.add(policy)
    await audit_service.record(ctx, "insurance.purchase", f"ซื้อประกัน {product.name}")
    await ctx.db.commit()
    await ctx.db.refresh(policy)
    return policy


async def list_policies(ctx: TenantContext) -> list[InsurancePolicy]:
    result = await ctx.db.scalars(ctx.scoped(InsurancePolicy).order_by(InsurancePolicy.created_at.desc()))
    return list(result)
