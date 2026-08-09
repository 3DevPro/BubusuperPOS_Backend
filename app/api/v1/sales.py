import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.sale import Sale
from app.schemas.refund import RefundCreateRequest, RefundResult
from app.schemas.sale import SaleCreateRequest, SaleListItem, SaleResult
from app.services import refund_service, report_service, sales_service

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("", response_model=SaleResult, status_code=201)
async def create_sale(
    body: SaleCreateRequest,
    ctx: TenantContext = Depends(require(Permission.create_sale)),
) -> SaleResult:
    return await sales_service.create_sale(ctx, body)


@router.get("", response_model=list[SaleListItem])
async def list_sales(
    start_date: date | None = None,
    end_date: date | None = None,
    ctx: TenantContext = Depends(require(Permission.view_sales)),
) -> list[Sale]:
    query = ctx.scoped(Sale)
    # Both or neither — a lone start_date/end_date is ambiguous (open-ended
    # range vs a mistyped param) and CSV export always supplies a full range,
    # so reject it rather than guess.
    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date and end_date must be provided together")
        start_utc, end_utc = await report_service.local_date_range_to_utc(ctx, start_date, end_date)
        query = query.where(Sale.created_at >= start_utc, Sale.created_at < end_utc)
    result = await ctx.db.scalars(query.order_by(Sale.created_at.desc()))
    return list(result)


@router.get("/by-receipt/{receipt_no}", response_model=SaleResult)
async def get_sale_by_receipt(
    receipt_no: str,
    ctx: TenantContext = Depends(require(Permission.view_sales)),
) -> SaleResult:
    # Exact match, case-insensitive — not ilike. A caller-supplied "%" must
    # not match every receipt in the shop (see app/ai chatbot's find_sale,
    # which this endpoint replaces the in-process equivalent of).
    sale = await ctx.db.scalar(ctx.scoped(Sale).where(func.upper(Sale.receipt_no) == receipt_no.upper()))
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sale not found")
    return await sales_service.to_sale_result(ctx, sale)


@router.get("/{sale_id}", response_model=SaleResult)
async def get_sale(
    sale_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.view_sales)),
) -> SaleResult:
    sale = await ctx.db.scalar(ctx.scoped(Sale).where(Sale.id == sale_id))
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sale not found")
    return await sales_service.to_sale_result(ctx, sale)


@router.post("/{sale_id}/refunds", response_model=RefundResult, status_code=201)
async def refund_sale(
    sale_id: uuid.UUID,
    body: RefundCreateRequest,
    ctx: TenantContext = Depends(require(Permission.refund_sale)),
) -> RefundResult:
    return await refund_service.refund_sale(ctx, sale_id, body)
