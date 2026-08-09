from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rate_limit import public_quote_limiter
from app.schemas.turbo.public import PublicQuoteRequest, PublicQuoteResponse
from app.services.turbo import public_quote_service

router = APIRouter(prefix="/public", tags=["turbo-public"])


@router.post("/quote", response_model=PublicQuoteResponse, status_code=status.HTTP_201_CREATED)
async def public_quote(
    body: PublicQuoteRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicQuoteResponse:
    # Anonymous endpoint — keyed on caller IP since there's no account to
    # attribute the request to.
    limit_key = request.client.host if request.client else "unknown"
    retry_after = public_quote_limiter.retry_after(limit_key)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "ขอราคาบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่",
            headers={"Retry-After": str(retry_after)},
        )
    public_quote_limiter.record_failure(limit_key)

    return await public_quote_service.quote_and_create_lead(db, body)
