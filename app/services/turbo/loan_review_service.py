"""Branch-side loan review — every function here takes a BranchContext, never
a TenantContext. Kept out of loan_service.py (which is TenantContext-only
throughout) specifically so that boundary stays grep-able: this is the one
file where a Branch Champion's scope and a tenant's data meet, and that
meeting point should be easy to find, not smeared across a file whose name
suggests it's tenant-only. See app/core/branch_scope.py for why the two
context types are deliberately incompatible.

Scope-safety invariant: a Branch Champion's token never carries a tenant_id
(see app/core/deps.py get_branch_context) — every tenant_id used below comes
from a LoanApplication row the caller already proved they may touch, by
matching assigned_branch_id == ctx.branch_id first. That match happens in
exactly one place (_get_scoped/_load_for_update); nothing past it re-derives
tenant scope from the caller's own token.
"""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.branch_scope import BranchContext
from app.models.tenant import Tenant
from app.models.turbo.loan import LoanApplication, LoanApplicationEvent, LoanApplicationStatus, LoanProduct
from app.models.user import User, UserRole
from app.schemas.turbo.loan import (
    LoanApplicationEventResponse,
    LoanReviewDetailResponse,
    LoanReviewItemResponse,
)
from app.services import audit_service
from app.services.turbo import loan_service

# Queue default (include_decided=False) — approved is excluded on purpose:
# once approved, only the tenant can act next (disburse), so it's no longer
# something a Champion needs to see in their action queue.
_ACTIVE_REVIEW_STATUSES = (
    LoanApplicationStatus.submitted,
    LoanApplicationStatus.doc_review,
    LoanApplicationStatus.collateral_check,
    LoanApplicationStatus.under_review,
)


async def _to_review_item(ctx: BranchContext, application: LoanApplication) -> LoanReviewItemResponse:
    tenant = await ctx.db.get(Tenant, application.tenant_id)
    owner_phone = await ctx.db.scalar(
        select(User.phone).where(User.tenant_id == application.tenant_id, User.role == UserRole.owner)
    )
    product = await ctx.db.get(LoanProduct, application.product_id)
    return LoanReviewItemResponse(
        id=application.id,
        tenant_name=tenant.name if tenant else "-",
        tenant_phone=owner_phone,
        product_id=application.product_id,
        approved_amount=application.approved_amount,
        monthly_installment=application.monthly_installment,
        term_months=application.term_months,
        collateral_kind=product.collateral_kind.value if product else "-",
        collateral_value=application.collateral_value,
        collateral_detail=application.collateral_detail,
        credit_tier_snapshot=application.credit_tier_snapshot,
        status=application.status.value,
        stage_started_at=application.stage_started_at,
        created_at=application.created_at,
    )


async def _to_detail(ctx: BranchContext, application: LoanApplication) -> LoanReviewDetailResponse:
    item = await _to_review_item(ctx, application)
    events = await loan_service._application_events(ctx.db, application.id)
    return LoanReviewDetailResponse(
        **item.model_dump(), events=[LoanApplicationEventResponse.model_validate(e) for e in events]
    )


async def _get_scoped(ctx: BranchContext, application_id: uuid.UUID) -> LoanApplication:
    application = await ctx.db.scalar(
        select(LoanApplication).where(
            LoanApplication.id == application_id, LoanApplication.assigned_branch_id == ctx.branch_id
        )
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "loan application not found")
    return application


async def _load_for_update(ctx: BranchContext, application_id: uuid.UUID) -> LoanApplication:
    # with_for_update() matters here specifically: the auto-advance clock
    # (loan_service._auto_advance) can fire from a tenant's own poll at the
    # same moment a Champion taps a button, and both write to this same row.
    application = await ctx.db.scalar(
        select(LoanApplication)
        .where(LoanApplication.id == application_id, LoanApplication.assigned_branch_id == ctx.branch_id)
        .with_for_update()
    )
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "loan application not found")
    return application


async def list_review_queue(ctx: BranchContext, include_decided: bool = False) -> list[LoanReviewItemResponse]:
    query = select(LoanApplication).where(LoanApplication.assigned_branch_id == ctx.branch_id)
    if not include_decided:
        query = query.where(LoanApplication.status.in_(_ACTIVE_REVIEW_STATUSES))
    query = query.order_by(LoanApplication.created_at.desc())

    applications = list(await ctx.db.scalars(query))
    now = datetime.now(timezone.utc)
    for application in applications:
        await loan_service._auto_advance(ctx.db, application, now)
    return [await _to_review_item(ctx, application) for application in applications]


async def get_review_detail(ctx: BranchContext, application_id: uuid.UUID) -> LoanReviewDetailResponse:
    application = await _get_scoped(ctx, application_id)
    await loan_service._auto_advance(ctx.db, application, datetime.now(timezone.utc))
    return await _to_detail(ctx, application)


async def advance(
    ctx: BranchContext, application_id: uuid.UUID, to_status: str, note: str | None
) -> LoanReviewDetailResponse:
    application = await _load_for_update(ctx, application_id)

    try:
        target = LoanApplicationStatus(to_status)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ไม่รู้จักสถานะนี้")
    # Rejecting always goes through reject() instead, which requires a
    # reason — allowing it here would let a Champion reject with no reason.
    if target == LoanApplicationStatus.rejected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "กรุณาใช้การปฏิเสธคำขอแทน")
    if target not in loan_service._ALLOWED_TRANSITIONS[application.status]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ไม่สามารถข้ามขั้นตอนได้")

    now = datetime.now(timezone.utc)
    from_status = application.status
    application.status = target
    application.stage_started_at = now
    application.reviewed_by_user_id = ctx.user_id
    if target == LoanApplicationStatus.approved:
        application.decided_at = now

    actor_name = await ctx.db.scalar(select(User.name).where(User.id == ctx.user_id))
    ctx.db.add(
        LoanApplicationEvent(
            application_id=application.id,
            tenant_id=application.tenant_id,
            branch_id=ctx.branch_id,
            from_status=from_status.value,
            to_status=target.value,
            actor_user_id=ctx.user_id,
            actor_name=actor_name or "-",
            actor_kind="champion",
            note=note,
        )
    )
    await audit_service.record_external(
        ctx.db,
        application.tenant_id,
        ctx.user_id,
        "loan.review_advance",
        f"สาขาอัปเดตสถานะคำขอสินเชื่อเป็น {target.value}",
    )
    await ctx.db.commit()
    await ctx.db.refresh(application)
    return await _to_detail(ctx, application)


async def reject(ctx: BranchContext, application_id: uuid.UUID, reason: str) -> LoanReviewDetailResponse:
    application = await _load_for_update(ctx, application_id)

    if LoanApplicationStatus.rejected not in loan_service._ALLOWED_TRANSITIONS[application.status]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ไม่สามารถปฏิเสธคำขอที่สถานะนี้ได้")

    now = datetime.now(timezone.utc)
    from_status = application.status
    application.status = LoanApplicationStatus.rejected
    application.stage_started_at = now
    application.decided_at = now
    application.rejection_reason = reason
    application.reviewed_by_user_id = ctx.user_id

    actor_name = await ctx.db.scalar(select(User.name).where(User.id == ctx.user_id))
    ctx.db.add(
        LoanApplicationEvent(
            application_id=application.id,
            tenant_id=application.tenant_id,
            branch_id=ctx.branch_id,
            from_status=from_status.value,
            to_status=LoanApplicationStatus.rejected.value,
            actor_user_id=ctx.user_id,
            actor_name=actor_name or "-",
            actor_kind="champion",
            note=reason,
        )
    )
    await audit_service.record_external(
        ctx.db, application.tenant_id, ctx.user_id, "loan.review_reject", f"สาขาปฏิเสธคำขอสินเชื่อ: {reason}"
    )
    await ctx.db.commit()
    await ctx.db.refresh(application)
    return await _to_detail(ctx, application)
