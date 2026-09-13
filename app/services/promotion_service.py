import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.tenancy import TenantContext
from app.models.product import Product
from app.models.promotion import Promotion, PromotionTarget
from app.schemas.promotion import PromotionCreateRequest, PromotionResponse, PromotionUpdateRequest
from app.services import audit_service
from app.services.promotion_engine import LoadedPromotion, LoadedPromotionTarget


async def _targets_by_promotion(ctx: TenantContext, promotion_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PromotionTarget]]:
    if not promotion_ids:
        return {}
    rows = await ctx.db.scalars(
        ctx.scoped(PromotionTarget).where(PromotionTarget.promotion_id.in_(promotion_ids))
    )
    result: dict[uuid.UUID, list[PromotionTarget]] = {}
    for row in rows:
        result.setdefault(row.promotion_id, []).append(row)
    return result


def _to_response(promo: Promotion, targets: list[PromotionTarget]) -> PromotionResponse:
    return PromotionResponse(
        id=promo.id,
        name=promo.name,
        kind=promo.kind,
        scope=promo.scope,
        percent_off=promo.percent_off,
        amount_off=promo.amount_off,
        buy_qty=promo.buy_qty,
        get_qty=promo.get_qty,
        bundle_price=promo.bundle_price,
        coupon_code=promo.coupon_code,
        starts_at=promo.starts_at,
        ends_at=promo.ends_at,
        active_weekdays=promo.active_weekdays,
        daily_start_time=promo.daily_start_time,
        daily_end_time=promo.daily_end_time,
        max_uses=promo.max_uses,
        uses_count=promo.uses_count,
        priority=promo.priority,
        stackable=promo.stackable,
        is_active=promo.is_active,
        target_product_ids=[t.product_id for t in targets if t.product_id is not None],
        target_category_ids=[t.category_id for t in targets if t.category_id is not None],
    )


async def _set_targets(
    ctx: TenantContext, promotion_id: uuid.UUID, product_ids: list[uuid.UUID], category_ids: list[uuid.UUID]
) -> None:
    existing = await ctx.db.scalars(ctx.scoped(PromotionTarget).where(PromotionTarget.promotion_id == promotion_id))
    for row in existing:
        await ctx.db.delete(row)
    for pid in product_ids:
        ctx.db.add(PromotionTarget(tenant_id=ctx.tenant_id, promotion_id=promotion_id, product_id=pid, category_id=None))
    for cid in category_ids:
        ctx.db.add(PromotionTarget(tenant_id=ctx.tenant_id, promotion_id=promotion_id, product_id=None, category_id=cid))


async def create_promotion(ctx: TenantContext, body: PromotionCreateRequest) -> PromotionResponse:
    promo = Promotion(
        tenant_id=ctx.tenant_id,
        name=body.name,
        kind=body.kind,
        scope=body.scope,
        percent_off=body.percent_off,
        amount_off=body.amount_off,
        buy_qty=body.buy_qty,
        get_qty=body.get_qty,
        bundle_price=body.bundle_price,
        coupon_code=body.coupon_code,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        active_weekdays=body.active_weekdays,
        daily_start_time=body.daily_start_time,
        daily_end_time=body.daily_end_time,
        max_uses=body.max_uses,
        priority=body.priority,
        stackable=body.stackable,
    )
    ctx.db.add(promo)
    await ctx.db.flush()
    await _set_targets(ctx, promo.id, body.target_product_ids, body.target_category_ids)
    await audit_service.record(ctx, "promotion.create", f"เพิ่มโปรโมชั่น {promo.name}")
    await ctx.db.commit()
    await ctx.db.refresh(promo)
    targets = (await _targets_by_promotion(ctx, [promo.id])).get(promo.id, [])
    return _to_response(promo, targets)


async def update_promotion(ctx: TenantContext, promotion_id: uuid.UUID, body: PromotionUpdateRequest) -> PromotionResponse:
    promo = await ctx.db.scalar(ctx.scoped(Promotion).where(Promotion.id == promotion_id))
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "promotion not found")

    data = body.model_dump(exclude_unset=True)
    target_product_ids = data.pop("target_product_ids", None)
    target_category_ids = data.pop("target_category_ids", None)
    for field, value in data.items():
        setattr(promo, field, value)

    if target_product_ids is not None or target_category_ids is not None:
        await _set_targets(ctx, promo.id, target_product_ids or [], target_category_ids or [])

    await audit_service.record(ctx, "promotion.update", f"แก้ไขโปรโมชั่น {promo.name}")
    await ctx.db.commit()
    await ctx.db.refresh(promo)
    targets = (await _targets_by_promotion(ctx, [promo.id])).get(promo.id, [])
    return _to_response(promo, targets)


async def deactivate_promotion(ctx: TenantContext, promotion_id: uuid.UUID) -> None:
    promo = await ctx.db.scalar(ctx.scoped(Promotion).where(Promotion.id == promotion_id))
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "promotion not found")
    promo.is_active = False
    await audit_service.record(ctx, "promotion.deactivate", f"ปิดใช้งานโปรโมชั่น {promo.name}")
    await ctx.db.commit()


async def list_promotions(ctx: TenantContext, active_only: bool = False) -> list[PromotionResponse]:
    query = ctx.scoped(Promotion).order_by(Promotion.created_at.desc())
    if active_only:
        query = query.where(Promotion.is_active.is_(True))
    promos = list(await ctx.db.scalars(query))
    targets_by_promo = await _targets_by_promotion(ctx, [p.id for p in promos])
    return [_to_response(p, targets_by_promo.get(p.id, [])) for p in promos]


async def load_for_engine(ctx: TenantContext) -> list[LoadedPromotion]:
    """Everything promotion_engine.resolve needs to evaluate the whole
    cart — a single query up front rather than one per line, since the
    engine itself is DB-free and just takes plain dataclasses."""
    promos = list(
        await ctx.db.scalars(ctx.scoped(Promotion).where(Promotion.is_active.is_(True)))
    )
    targets_by_promo = await _targets_by_promotion(ctx, [p.id for p in promos])
    return [
        LoadedPromotion(
            id=p.id,
            name=p.name,
            kind=p.kind.value,
            scope=p.scope.value,
            percent_off=p.percent_off,
            amount_off=p.amount_off,
            buy_qty=p.buy_qty,
            get_qty=p.get_qty,
            bundle_price=p.bundle_price,
            coupon_code=p.coupon_code,
            starts_at=p.starts_at,
            ends_at=p.ends_at,
            active_weekdays=p.active_weekdays,
            daily_start_time=p.daily_start_time,
            daily_end_time=p.daily_end_time,
            max_uses=p.max_uses,
            uses_count=p.uses_count,
            priority=p.priority,
            stackable=p.stackable,
            targets=[
                LoadedPromotionTarget(product_id=t.product_id, category_id=t.category_id)
                for t in targets_by_promo.get(p.id, [])
            ],
        )
        for p in promos
    ]


async def record_uses(ctx: TenantContext, promotion_ids: list[uuid.UUID]) -> None:
    """Increments uses_count for each promotion that actually discounted
    this sale — called once per checkout, after the sale itself is
    persisted, so max_uses is enforced against completed sales only."""
    if not promotion_ids:
        return
    rows = await ctx.db.scalars(ctx.scoped(Promotion).where(Promotion.id.in_(promotion_ids)))
    for row in rows:
        row.uses_count += 1
